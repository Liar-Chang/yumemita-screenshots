#!/usr/bin/env python3
"""指定片段逐幀擷取：把一段時間內的每一幀都截下來，加入網站索引。

用法：
    python clip_frames.py --video 影片.mp4 --ep 1 --from 3:20 --to 3:25
    python clip_frames.py --video ... --ep 1 --from 165 --to 167.5 --step 2   # 每 2 幀取 1
    python clip_frames.py --video ... --ep 1 --from 3:20 --to 3:25 --subs 素材/ep01.srt
        （附字幕檔的話，每一幀會自動掛上當下的台詞，變成可搜尋）
    python clip_frames.py ... --text "阿拉蕾吐舌名場面"   # 或手動給整段一個描述

說明：
    - 檔名/編號以「全域幀號」命名（c＋幀號），同一段重跑會覆蓋而不是重複
    - 與整集管線互不干擾：extract.py 重跑不會清掉逐幀圖
    - 超過 1500 幀（約 60 秒）會擋下，避免誤把整集塞進來；確定要就加 --force
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEBP_QUALITY = "82"
OUT_WIDTH = 1280
MAX_FRAMES = 1500


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


def parse_time(s: str) -> float:
    """接受 200 / 200.5 / 3:20 / 3:20.5 / 1:03:20"""
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(s)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def video_fps(video: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True)
    num, den = r.stdout.strip().split("/")
    return float(num) / float(den)


def load_sub_events(subs_path: Path) -> list[dict]:
    import pysubs2
    subs = pysubs2.load(str(subs_path))
    return [{"start": e.start / 1000.0, "end": e.end / 1000.0, "text": e.plaintext.strip()}
            for e in subs if not e.is_comment and e.plaintext.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--ep", required=True, type=int)
    ap.add_argument("--from", dest="start", required=True, help="起點，如 3:20 或 200.5")
    ap.add_argument("--to", dest="end", required=True, help="終點")
    ap.add_argument("--step", type=int, default=1, help="每 N 幀取 1（預設 1＝每幀）")
    ap.add_argument("--text", default="", help="給整段掛的描述文字")
    ap.add_argument("--subs", type=Path, default=None, help="字幕檔：每幀自動掛當下台詞")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "site" / "public")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"影片不存在：{args.video}")
    t0, t1 = parse_time(args.start), parse_time(args.end)
    if t1 <= t0:
        sys.exit("終點必須大於起點")

    fps = video_fps(args.video)
    n_frames = int((t1 - t0) * fps / args.step)
    if n_frames > MAX_FRAMES and not args.force:
        sys.exit(f"這段有約 {n_frames} 幀（>{MAX_FRAMES}），確定要的話加 --force，"
                 f"或用 --step 降密度（--step 2 = 每2幀取1）")

    img_dir = args.out / "img" / f"e{args.ep:02d}"
    index_path = args.out / "index" / "yumemita.json"
    img_dir.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"第 {args.ep} 集 {t0:.2f}s → {t1:.2f}s｜{fps:.3f}fps｜預計 {n_frames} 幀")

    # 一次 ffmpeg 解出整段所有幀到暫存資料夾
    tmp = Path(tempfile.mkdtemp(prefix="clip_"))
    try:
        vf = f"scale={OUT_WIDTH}:-2"
        if args.step > 1:
            vf = f"select='not(mod(n\\,{args.step}))'," + vf
        cmd = [FFMPEG, "-y", "-v", "error",
               "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(args.video),
               "-vf", vf, "-fps_mode", "passthrough",
               "-c:v", "libwebp", "-quality", WEBP_QUALITY,
               str(tmp / "f_%05d.webp")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"ffmpeg 失敗：{r.stderr[-400:]}")
        files = sorted(tmp.glob("f_*.webp"))
        if not files:
            sys.exit("沒有解出任何幀，請檢查時間範圍")

        sub_events = load_sub_events(args.subs) if args.subs else []

        # 排除清單（prune.py 刪過的不復活）
        exclude_path = Path(__file__).parent.parent / "素材" / "排除清單.json"
        blocked = []
        if exclude_path.exists():
            blocked = [x["t"] for x in json.loads(exclude_path.read_text(encoding="utf-8-sig"))
                       if x["ep"] == args.ep]

        items, skipped = [], 0
        for n, f in enumerate(files):
            t = t0 + n * args.step / fps
            if any(abs(t - bt) <= 0.3 for bt in blocked):
                skipped += 1
                continue
            frame_no = round(t * fps)
            name = f"c{frame_no:06d}.webp"
            shutil.move(str(f), str(img_dir / name))
            text = args.text
            if not text and sub_events:
                hit = next((e for e in sub_events if e["start"] <= t <= e["end"]), None)
                if hit:
                    text = hit["text"]
            items.append({
                "id": f"e{args.ep:02d}c{frame_no:06d}",
                "ep": args.ep,
                "t": round(t, 3),
                "text": text,
                "img": f"img/e{args.ep:02d}/{name}",
                "tags": ["逐幀"],
            })

        # 合併索引：同 id 覆蓋（重跑同一段不會重複）
        index = []
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8-sig"))["items"]
        new_ids = {x["id"] for x in items}
        index = [x for x in index if x["id"] not in new_ids]
        index.extend(items)
        index.sort(key=lambda x: (x["ep"], x["t"]))
        index_path.write_text(
            json.dumps({"series": "yumemita", "items": index}, ensure_ascii=False,
                       separators=(",", ":")),
            encoding="utf-8")

        size_mb = sum((img_dir / Path(x["img"]).name).stat().st_size for x in items) / 1024 / 1024
        print(f"完成：{len(items)} 幀（排除清單跳過 {skipped}）｜{size_mb:.1f} MB｜索引共 {len(index)} 筆")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
