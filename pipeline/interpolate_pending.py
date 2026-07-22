#!/usr/bin/env python3
"""補齊批次匯入時「信心不足、沒自動收」的截圖：不用像素比對，改用截圖擷取的真實
時間順序內插影片時間點。

原理：使用者是照著影片順序邊看邊截圖的，所以 PotPlayer 檔名裡的擷取時間戳順序，
會高度對應到影片時間順序。對每張還沒匯入的截圖，往前後找最近「已經有時間點」
的鄰居（不管是原本就信心足夠自動匯入的，還是內插過的都算），用兩邊的
(擷取時間差, 已知影片時間) 線性內插出一個影片時間，直接匯入（文字留空給
ocr_images.py 補）。找不到任何鄰居（例如整批都沒人工確認過）才會跳過。

用法：
    python interpolate_pending.py --ep 3 --folder "素材\\..\\Capture\\第3話"
    python interpolate_pending.py --ep 3 --folder "%APPDATA%\\PotPlayerMini64\\Capture\\第3話"
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_PATH = ROOT / "site" / "public" / "index" / "yumemita.json"
EP_ALIASES = {"op": -1, "ed": 1000}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TS_RE = re.compile(r"_(\d{8})_(\d{6})\.(\d+)\.\w+$")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def parse_ep(s: str) -> int:
    return EP_ALIASES.get(s.lower(), None) if s.lower() in EP_ALIASES else int(s)


def capture_seconds(name: str) -> float | None:
    m = TS_RE.search(name)
    if not m:
        return None
    date, hms, ms = m.group(1), m.group(2), m.group(3)
    dt = datetime.strptime(date + hms, "%Y%m%d%H%M%S")
    return dt.timestamp() + float("0." + ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True, type=str, help='集數，或 "op" / "ed"')
    ap.add_argument("--folder", required=True, type=Path, help="該集手動截圖所在資料夾")
    args = ap.parse_args()
    ep = parse_ep(args.ep)

    if not args.folder.is_dir():
        sys.exit(f"資料夾不存在：{args.folder}")

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8-sig"))
    known = {it["source"]: it["t"] for it in index["items"] if it["ep"] == ep and it.get("source")}

    shots = [p.name for p in args.folder.iterdir() if p.suffix.lower() in IMG_EXTS]
    dated = [(name, capture_seconds(name)) for name in shots]
    undated = [name for name, cs in dated if cs is None]
    all_shots = sorted((name for name, cs in dated if cs is not None), key=lambda n: capture_seconds(n))
    if undated:
        print(f"檔名看不出擷取時間，略過（{len(undated)} 張）：{', '.join(undated[:5])}")

    pending = [name for name in all_shots if name not in known]
    print(f"{len(pending)} 張要內插時間點")
    if not pending:
        return

    skipped = []
    for name in pending:
        idx = all_shots.index(name)
        cx = capture_seconds(name)

        left = next(((capture_seconds(all_shots[j]), known[all_shots[j]])
                     for j in range(idx - 1, -1, -1) if all_shots[j] in known), None)
        right = next(((capture_seconds(all_shots[j]), known[all_shots[j]])
                      for j in range(idx + 1, len(all_shots)) if all_shots[j] in known), None)

        if left and right:
            cl, tl = left
            cr, tr = right
            frac = (cx - cl) / (cr - cl) if cr != cl else 0.5
            t = tl + frac * (tr - tl)
        elif left:
            t = left[1] + (cx - left[0])
        elif right:
            t = right[1] - (right[0] - cx)
        else:
            skipped.append(name)
            continue

        img_path = args.folder / name
        r = subprocess.run(
            [sys.executable, str(ROOT / "pipeline" / "add_image.py"),
             "--image", str(img_path), "--ep", str(ep), "--text", "", "--t", str(round(t, 2)),
             "--tags", "待確認", "--source", name],
            capture_output=True, text=True, encoding="utf-8")
        status = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
        print(f"  {name}  t={t:.1f}s  {status}")
        known[name] = t  # 讓後面待內插的項目也能拿這張當鄰居

    if skipped:
        print(f"沒有任何已知鄰居可以內插，跳過（{len(skipped)} 張，需手動 add_image.py 處理）：")
        for name in skipped:
            print(f"  {name}")


if __name__ == "__main__":
    main()
