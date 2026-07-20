#!/usr/bin/env python3
"""幫手動加入的截圖（索引裡有 source 欄位，代表是使用者自己截的圖）直接 OCR
出台詞文字，省去每張都要自己打字。

截圖存檔時已經統一轉成 1280 寬（跟 ocr_subs.py 校準用的 720p 座標系統一樣），
直接裁字幕帶區域丟 OCR 就好，不用像 ocr_subs.py 那樣重新掃一次整部影片。

用法：
    python ocr_images.py                 # 全部有 source 的項目都覆蓋成 OCR 結果
    python ocr_images.py --only-empty    # 只補目前沒有文字的，不覆蓋既有文字
    python ocr_images.py --ep 1          # 限定集數
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from ocr_subs import BAND_H, BAND_Y, clean_line, ocr_segment  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
PUBLIC = ROOT / "site" / "public"
INDEX_PATH = PUBLIC / "index" / "yumemita.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, default=None, help="只處理指定集數（不填就全部）")
    ap.add_argument("--only-empty", action="store_true", help="只補目前沒有文字的，不覆蓋既有文字")
    args = ap.parse_args()

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8-sig"))
    targets = [it for it in index["items"] if it.get("source")]
    if args.ep is not None:
        targets = [it for it in targets if it["ep"] == args.ep]
    if args.only_empty:
        targets = [it for it in targets if not it["text"].strip()]
    if not targets:
        print("沒有符合條件的項目。")
        return
    print(f"共 {len(targets)} 張要 OCR...")

    import logging
    logging.disable(logging.INFO)
    from rapidocr import RapidOCR
    ocr = RapidOCR()

    work = Path(tempfile.mkdtemp(prefix="ocrimg_"))
    updated, unchanged = 0, 0
    try:
        band_path = work / "band.png"
        for k, it in enumerate(targets, 1):
            img_path = PUBLIC / it["img"]
            im = Image.open(img_path).convert("RGB")
            w, h = im.size
            scale = h / 720
            band = im.crop((0, round(BAND_Y * scale), w, round((BAND_Y + BAND_H) * scale)))
            if scale != 1.0:
                band = band.resize((1280, BAND_H))
            band.save(band_path)
            text = clean_line(ocr_segment(ocr, band_path))
            if text and text != it["text"]:
                it["text"] = text
                updated += 1
            else:
                unchanged += 1
            if k % 20 == 0 or k == len(targets):
                print(f"  {k}/{len(targets)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：{updated} 張更新了文字，{unchanged} 張沒變化（畫面本來就沒字幕，"
          f"或字幕辨識不出來，仍需人工補）")


if __name__ == "__main__":
    main()
