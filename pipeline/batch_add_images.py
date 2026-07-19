#!/usr/bin/env python3
"""批次匯入手動截圖：整個資料夾的截圖，自動比對找出對應影片時間點並加入索引。

原理：把來源影片降到 1fps 小縮圖當「候選幀資料庫」，每張截圖也降到同樣大小，
逐一比對找出畫面最像的候選幀，取得時間點；再從字幕檔找該時間點附近最近的
台詞當作預設文字（可能不完全準，之後用網站的編輯模式看著圖修正即可）。

全自動用法（推薦）——不指定集數/影片，自動從檔名判斷、自動找對應影片：
    python batch_add_images.py --folder "C:\\...\\Capture"

手動指定（檔名判斷不出集數，或要處理 OP/ED 時用）：
    python batch_add_images.py --folder "C:\\...\\Capture" --video 素材\\ep01.mp4 --ep 1

已經成功匯入過的截圖（用檔名比對）不會重複處理，資料夾可以一直往裡面加新截圖，
重跑只會處理新的。比對信心不足的截圖不會自動加入，會列在最後提醒手動處理。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEBP_QUALITY = "82"
OUT_WIDTH = 1280
SAMPLE_FPS = 1.0          # 候選幀採樣頻率
THUMB_SIZE = (64, 36)     # 比對用縮圖大小（寬,高）
GOOD_DIFF = 15.0          # 平均像素差在這以下才算是有信心的匹配（0~255 尺度）
NEARBY_SECONDS = 3.0      # 候選幀時間差在這以內都算「同一幕」，不當作互相競爭的候選
CAPTION_WINDOW = 2.0      # 匹配時間點前後幾秒內找字幕當建議文字
EP_ALIASES = {"op": -1, "ed": 1000}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm"}

# 自動找影片時要搜尋的資料夾（有新的下載資料夾就加進來）
VIDEO_SEARCH_DIRS = [
    Path.home() / "Videos" / "iFlyDown Video",
    Path.home() / "Videos" / "video",
]
EP_PATTERN = re.compile(r"第(\d+)話")


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


def parse_ep(s: str) -> int:
    if s.lower() in EP_ALIASES:
        return EP_ALIASES[s.lower()]
    return int(s)


def ep_folder(ep: int) -> str:
    if ep == -1:
        return "op"
    if ep == 1000:
        return "ed"
    return f"e{ep:02d}"


def detect_episode(filename: str) -> int | None:
    m = EP_PATTERN.search(filename)
    return int(m.group(1)) if m else None


def find_video_for_episode(ep: int) -> Path | None:
    tag = f"第{ep:02d}話"
    for d in VIDEO_SEARCH_DIRS:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() in VIDEO_EXTS and tag in f.name:
                return f
    return None


def build_candidate_frames(video: Path, work: Path) -> np.ndarray:
    """回傳 shape (N, H, W) 的 float32 灰階縮圖陣列，index i 對應第 i 秒。"""
    print(f"  掃描來源影片建立比對資料庫（{SAMPLE_FPS}fps，約需 1~2 分鐘）...")
    w, h = THUMB_SIZE
    pattern = work / "f_%06d.png"
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video),
         "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", str(pattern)],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg 失敗：{r.stderr[-400:]}")
    frames = sorted(work.glob("f_*.png"))
    if not frames:
        sys.exit("沒有抽出任何候選幀，請檢查影片檔案")
    arr = np.stack([np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in frames])
    print(f"  候選幀共 {len(frames)} 張")
    return arr


def match_timestamp(shot_path: Path, candidates: np.ndarray) -> tuple[float, float, float]:
    """回傳 (最佳時間點秒數, 最佳差值, 離最佳時間點較遠的次佳差值)。

    同一幕靜止畫面會連續好幾秒，最相近的幾個候選其實是同一個答案，不能拿來當
    「不確定」的證據；只有時間上明顯不同的候選也一樣像，才是真的有疑慮。
    """
    w, h = THUMB_SIZE
    shot = Image.open(shot_path).convert("L").resize((w, h))
    shot_arr = np.asarray(shot, dtype=np.float32)
    diff = np.abs(candidates - shot_arr[None]).mean(axis=(1, 2))
    order = np.argsort(diff)
    best_idx = int(order[0])
    best_diff = float(diff[order[0]])
    second_diff = 1e9
    for idx in order[1:]:
        if abs(int(idx) - best_idx) / SAMPLE_FPS > NEARBY_SECONDS:
            second_diff = float(diff[idx])
            break
    return best_idx / SAMPLE_FPS, best_diff, second_diff


def load_subs(subs_path: Path) -> list[dict]:
    import pysubs2
    subs = pysubs2.load(str(subs_path))
    return [{"start": e.start / 1000.0, "end": e.end / 1000.0, "text": e.plaintext.strip()}
            for e in subs if not e.is_comment and e.plaintext.strip()]


def nearest_caption(t: float, events: list[dict]) -> str:
    for e in events:
        if e["start"] - CAPTION_WINDOW <= t <= e["end"] + CAPTION_WINDOW:
            return e["text"]
    return ""


def process_group(shots: list[Path], video: Path, ep: int, out: Path, subs_arg: Path | None) -> None:
    folder = ep_folder(ep)
    img_dir = out / "img" / folder
    index_path = out / "index" / "yumemita.json"
    img_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== 第 {ep} 集 | 影片：{video.name} | {len(shots)} 張截圖 ===")

    index = {"series": "yumemita", "items": [], "draftEpisodes": []}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))
        index.setdefault("draftEpisodes", [])

    same_ep = [x for x in index["items"] if x["ep"] == ep]
    already = {x["source"] for x in same_ep if "source" in x}
    shots = [s for s in shots if s.name not in already]
    if not shots:
        print("  這些截圖都已經匯入過了，沒有新的要處理。")
        return

    subs = subs_arg or Path(__file__).parent.parent / "素材" / f"ep{ep:02d}.srt"
    events = load_subs(subs) if subs.exists() else []
    print(f"  字幕來源：{subs.name}（{len(events)} 句）" if events else "  （無字幕檔，文字欄位留空）")
    print(f"  待處理 {len(shots)} 張（已跳過先前匯入過的）")

    existing_m = [int(Path(x["img"]).stem[1:]) for x in same_ep
                  if Path(x["img"]).stem.startswith("m") and Path(x["img"]).stem[1:].isdigit()]
    n = (max(existing_m) + 1) if existing_m else 1

    work = Path(tempfile.mkdtemp(prefix="batchadd_"))
    imported, uncertain = [], []
    try:
        candidates = build_candidate_frames(video, work)

        print("  比對每張截圖...")
        for i, shot in enumerate(shots, 1):
            t, best, second = match_timestamp(shot, candidates)
            confident = best <= GOOD_DIFF and (second - best) >= best * 0.5
            if not confident:
                uncertain.append((shot.name, t, best))
                continue

            text = nearest_caption(t, events)
            name = f"m{n:04d}.webp"
            dst = img_dir / name
            r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(shot),
                                "-vf", f"scale={OUT_WIDTH}:-2", "-c:v", "libwebp",
                                "-quality", WEBP_QUALITY, str(dst)],
                               capture_output=True, text=True)
            if r.returncode != 0 or not dst.exists():
                uncertain.append((shot.name, t, best))
                continue

            index["items"].append({
                "id": f"{folder}-m{n:04d}",
                "ep": ep,
                "t": round(t, 2),
                "text": text,
                "img": f"img/{folder}/{name}",
                "tags": ["待確認"],
                "source": shot.name,
            })
            imported.append((shot.name, t, text))
            n += 1
            if i % 10 == 0 or i == len(shots):
                print(f"    {i}/{len(shots)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    index["items"].sort(key=lambda x: (x["ep"], x["t"]))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")

    print(f"完成：成功匯入 {len(imported)} 張，索引共 {len(index['items'])} 筆")
    for name, t, text in imported:
        print(f"  {int(t//60)}:{int(t%60):02d}  {text or '（無字幕，需手動補文字）'}  ← {name}")
    if uncertain:
        print(f"無法確定對應時間，未自動匯入（{len(uncertain)} 張，需手動用 add_image.py 處理）：")
        for name, t, best in uncertain:
            print(f"  {name}  最接近 {int(t//60)}:{int(t%60):02d}（差異值 {best:.0f}，信心不足）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--video", type=Path, default=None, help="不指定就從檔名自動找對應影片")
    ap.add_argument("--ep", type=str, default=None, help='不指定就從檔名自動判斷集數（"op"/"ed" 要手動指定）')
    ap.add_argument("--subs", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "site" / "public")
    args = ap.parse_args()

    if not args.folder.is_dir():
        sys.exit(f"資料夾不存在：{args.folder}")

    all_shots = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in IMG_EXTS)
    if not all_shots:
        sys.exit(f"資料夾裡沒有找到圖片：{args.folder}")

    if args.ep is not None:
        # 手動指定模式：整個資料夾當同一集處理
        if args.video is None:
            sys.exit("有指定 --ep 就也要指定 --video")
        process_group(all_shots, args.video, parse_ep(args.ep), args.out, args.subs)
        return

    # 全自動模式：從檔名判斷集數分組，各自找影片
    groups: dict[int, list[Path]] = {}
    unknown = []
    for shot in all_shots:
        ep = detect_episode(shot.name)
        if ep is None:
            unknown.append(shot.name)
        else:
            groups.setdefault(ep, []).append(shot)

    if unknown:
        print(f"檔名判斷不出集數，略過（{len(unknown)} 張，需手動用 --ep 指定處理）：")
        for name in unknown:
            print(f"  {name}")

    for ep in sorted(groups):
        video = args.video or find_video_for_episode(ep)
        if video is None:
            print(f"\n=== 第 {ep} 集 | {len(groups[ep])} 張截圖 ===")
            print(f"  找不到對應的影片檔（搜尋過：{', '.join(str(d) for d in VIDEO_SEARCH_DIRS)}），略過")
            continue
        process_group(groups[ep], video, ep, args.out, args.subs)


if __name__ == "__main__":
    main()
