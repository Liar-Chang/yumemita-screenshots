#!/usr/bin/env python3
"""手動加圖：把任意圖片檔案加進網站（自動轉檔成跟其他截圖一樣的規格）。

用法：
    python add_image.py --image "C:\\某張圖.png" --ep 1 --text "我的名場面"
    python add_image.py --image ... --ep op --text "OP 名場面"       # 加進 OP 分類
    python add_image.py --image ... --ep ed --text "ED 名場面"       # 加進 ED 分類
    python add_image.py --image ... --ep 5 --text "" --tags 場景     # 沒有台詞、只有標籤
    python add_image.py --image ... --ep 3 --text "..." --t 620      # 指定排序用的時間點（秒）

說明：
    - 圖片會自動轉成 1280 寬、webp（跟管線產生的截圖同規格）
    - 檔名開頭用 m（manual），不會跟 extract.py／clip_frames.py 的檔名撞在一起
    - --t 只影響同一集內的顯示順序，沒指定就自動排在該集最後面
    - 之後想刪掉，跟其他截圖一樣：直接刪檔案、雙擊 刪圖同步.bat
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEBP_QUALITY = "82"
OUT_WIDTH = 1280
EP_ALIASES = {"op": -1, "ed": 1000}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--ep", required=True, type=str, help='集數，或 "op" / "ed"')
    ap.add_argument("--text", default="", help="台詞或描述文字（可留空）")
    ap.add_argument("--t", type=float, default=None, help="排序用時間點（秒），不填就排最後")
    ap.add_argument("--tags", default="", help="逗號分隔的標籤，例：場景,重要")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "site" / "public")
    args = ap.parse_args()

    if not args.image.exists():
        sys.exit(f"圖片不存在：{args.image}")

    ep = parse_ep(args.ep)
    folder = ep_folder(ep)
    img_dir = args.out / "img" / folder
    index_path = args.out / "index" / "yumemita.json"
    img_dir.mkdir(parents=True, exist_ok=True)

    index = {"series": "yumemita", "items": []}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))

    same_ep = [x for x in index["items"] if x["ep"] == ep]
    existing_m = [int(Path(x["img"]).stem[1:]) for x in same_ep
                  if Path(x["img"]).stem.startswith("m") and Path(x["img"]).stem[1:].isdigit()]
    n = (max(existing_m) + 1) if existing_m else 1
    name = f"m{n:04d}.webp"
    dst = img_dir / name

    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(args.image),
                        "-vf", f"scale={OUT_WIDTH}:-2", "-c:v", "libwebp",
                        "-quality", WEBP_QUALITY, str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        sys.exit(f"轉檔失敗：{r.stderr[-400:]}")

    t = args.t
    if t is None:
        t = (max((x["t"] for x in same_ep), default=0) + 1)

    item = {
        "id": f"{folder}-{name.split('.')[0]}",
        "ep": ep,
        "t": round(t, 2),
        "text": args.text,
        "img": f"img/{folder}/{name}",
        "tags": [s.strip() for s in args.tags.split(",") if s.strip()],
    }
    index["items"].append(item)
    index["items"].sort(key=lambda x: (x["ep"], x["t"]))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")

    size_kb = dst.stat().st_size / 1024
    print(f"已加入：{item['id']} | {folder} | {size_kb:.0f}KB | {args.text or '（無文字）'}")
    print(f"索引共 {len(index['items'])} 筆")


if __name__ == "__main__":
    main()
