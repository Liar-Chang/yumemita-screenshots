#!/usr/bin/env python3
"""YUME∞MITA 截圖管線：字幕時間軸 + 場景偵測 → ffmpeg 抽幀 → WebP → JSON 索引。

用法：
    python extract.py --video 素材/ep01.mp4 --ep 1 --subs 素材/ep01.ass
    python extract.py --video 素材/ep01.mp4 --ep 1              # 無字幕：純場景偵測
    （--out 預設輸出到 ../site/public，重跑同一集會先清掉該集舊資料，可安全重跑）

輸出：
    site/public/img/eNN/0001.webp ...
    site/public/index/yumemita.json   （{id, ep, t, text, img, tags} 陣列，依集數/時間排序）
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------- 參數 ----------

SCENE_THRESHOLD = 0.35   # 場景變化敏感度（0~1，越低抓越多）
GAP_MIN = 4.0            # 無台詞空窗超過此秒數才補場景圖
SCENE_MIN_SPACING = 2.5  # 場景圖之間最小間隔（秒）
GAP_FALLBACK_STEP = 6.0  # 空窗內完全沒場景變化時，退而求其次的固定取樣間隔
LONG_LINE_SPAN = 6.0     # 台詞持續超過此秒數就多抽幾張
WEBP_QUALITY = "82"
OUT_WIDTH = 1280


def find_bin(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    cand = Path.home() / "AppData/Local/Microsoft/WinGet/Links" / f"{name}.exe"
    if cand.exists():
        return str(cand)
    pkgs = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if pkgs.exists():
        hits = sorted(pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"))
        if hits:
            return str(hits[-1])
    sys.exit(f"找不到 {name}，請先安裝 ffmpeg（winget install Gyan.FFmpeg）")


FFMPEG = find_bin("ffmpeg")
FFPROBE = find_bin("ffprobe")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def video_duration(video: Path) -> float:
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)])
    return float(r.stdout.strip())


# ---------- 字幕解析 ----------

TAG_RE = re.compile(r"\{[^}]*\}")


def clean_text(raw: str) -> str:
    t = TAG_RE.sub("", raw)
    t = t.replace("\\N", " ").replace("\\n", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_subs(subs_path: Path) -> list[dict]:
    """回傳 [{start, end, text}]（秒），已清理、去重疊。"""
    import pysubs2
    subs = pysubs2.load(str(subs_path))
    events = []
    for ev in subs:
        if ev.is_comment:
            continue
        text = clean_text(ev.plaintext if hasattr(ev, "plaintext") else ev.text)
        if not text:
            continue
        start, end = ev.start / 1000.0, ev.end / 1000.0
        if end <= start:
            continue
        events.append({"start": start, "end": end, "text": text})
    events.sort(key=lambda e: e["start"])
    # 完全相同時間+文字的重複行（有些字幕檔為了描邊會疊兩層）
    dedup, seen = [], set()
    for e in events:
        key = (round(e["start"], 2), e["text"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)
    return dedup


# ---------- 場景偵測 ----------

PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def detect_scenes(video: Path) -> list[float]:
    print("  場景偵測中（需解碼整部影片，約 1~3 分鐘）...")
    r = run([FFMPEG, "-i", str(video),
             "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
             "-an", "-f", "null", "-"])
    times = [float(m.group(1)) for m in PTS_RE.finditer(r.stderr)]
    print(f"  偵測到 {len(times)} 個場景變化點")
    return times


# ---------- 抽幀計畫 ----------

def plan_shots(events: list[dict], scenes: list[float], duration: float) -> list[dict]:
    """回傳 [{t, text, tags}]，依時間排序。"""
    shots = []

    # 1) 台詞：中點抽 1 張，長句平均多抽
    for e in events:
        span = e["end"] - e["start"]
        n = max(1, min(3, int(span // LONG_LINE_SPAN) + 1))
        for i in range(n):
            t = e["start"] + span * (i + 1) / (n + 1)
            shots.append({"t": t, "text": e["text"], "tags": []})

    # 2) 無台詞空窗：用場景變化點補圖（OP/ED、純畫面演出都靠這個涵蓋）
    intervals = [(e["start"], e["end"]) for e in events]
    gaps, cursor = [], 0.0
    for s, e in intervals:
        if s - cursor >= GAP_MIN:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if duration - cursor >= GAP_MIN:
        gaps.append((cursor, duration))

    for gs, ge in gaps:
        in_gap = [t for t in scenes if gs + 0.3 <= t <= ge - 0.3]
        picked, last = [], -1e9
        for t in in_gap:
            if t - last >= SCENE_MIN_SPACING:
                picked.append(t)
                last = t
        if not picked and ge - gs >= GAP_FALLBACK_STEP:
            k = gs + GAP_FALLBACK_STEP / 2
            while k < ge:
                picked.append(k)
                k += GAP_FALLBACK_STEP
        for t in picked:
            shots.append({"t": t, "text": "", "tags": ["場景"]})

    shots.sort(key=lambda s: s["t"])
    return shots


# ---------- 抽幀 ----------

def extract_frame(video: Path, t: float, out: Path) -> bool:
    r = run([FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={OUT_WIDTH}:-2",
             "-c:v", "libwebp", "-quality", WEBP_QUALITY, str(out)])
    return out.exists() and out.stat().st_size > 0


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--ep", required=True, type=int)
    ap.add_argument("--subs", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "site" / "public")
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"影片不存在：{args.video}")
    if args.subs and not args.subs.exists():
        sys.exit(f"字幕不存在：{args.subs}")

    img_dir = args.out / "img" / f"e{args.ep:02d}"
    index_path = args.out / "index" / "yumemita.json"
    img_dir.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    duration = video_duration(args.video)
    print(f"第 {args.ep} 集 | 片長 {duration/60:.1f} 分鐘 | {args.video.name}")

    events = parse_subs(args.subs) if args.subs else []
    print(f"  台詞 {len(events)} 句" if events else "  （無字幕檔，走純場景偵測模式）")

    scenes = detect_scenes(args.video)
    shots = plan_shots(events, scenes, duration)

    # 使用者刪過的圖不再復活（見 prune.py）
    exclude_path = Path(__file__).parent.parent / "素材" / "排除清單.json"
    if exclude_path.exists():
        blocked = [x["t"] for x in json.loads(exclude_path.read_text(encoding="utf-8-sig"))
                   if x["ep"] == args.ep]
        before = len(shots)
        shots = [s for s in shots if not any(abs(s["t"] - bt) <= 0.3 for bt in blocked)]
        if before != len(shots):
            print(f"  依排除清單跳過 {before - len(shots)} 張")

    print(f"  預計抽 {len(shots)} 張")

    # 重跑安全：清掉這一集舊圖（c 開頭的逐幀圖由 clip_frames.py 管理，不動）
    for old in img_dir.glob("*.webp"):
        if not old.name.startswith("c"):
            old.unlink()

    items, fail = [], 0
    for i, s in enumerate(shots, 1):
        name = f"{i:04d}.webp"
        if extract_frame(args.video, s["t"], img_dir / name):
            items.append({
                "id": f"e{args.ep:02d}-{i:04d}",
                "ep": args.ep,
                "t": round(s["t"], 2),
                "text": s["text"],
                "img": f"img/e{args.ep:02d}/{name}",
                "tags": s["tags"],
            })
        else:
            fail += 1
        if i % 50 == 0 or i == len(shots):
            print(f"  進度 {i}/{len(shots)}")

    # 合併索引（先移除本集舊資料；eNNc 開頭的逐幀項目保留）
    clip_prefix = f"e{args.ep:02d}c"
    index = []
    if index_path.exists():
        index = [x for x in json.loads(index_path.read_text(encoding="utf-8-sig"))["items"]
                 if x["ep"] != args.ep or x["id"].startswith(clip_prefix)]
    index.extend(items)
    index.sort(key=lambda x: (x["ep"], x["t"]))
    index_path.write_text(
        json.dumps({"series": "yumemita", "items": index}, ensure_ascii=False, indent=None,
                   separators=(",", ":")),
        encoding="utf-8")

    size_mb = sum(f.stat().st_size for f in img_dir.glob("*.webp")) / 1024 / 1024
    print(f"完成：{len(items)} 張（失敗 {fail}）｜本集容量 {size_mb:.1f} MB｜索引共 {len(index)} 筆 → {index_path}")


if __name__ == "__main__":
    main()
