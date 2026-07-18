#!/usr/bin/env python3
"""週更一鍵腳本：OCR（如需要）→ 抽圖 → 更新索引。

用法：
    python update_ep.py --video "D:\\下載\\第03話.mp4" --ep 3
    python update_ep.py --video ... --ep 3 --subs 已有字幕.srt              # 跳過 OCR
    python update_ep.py --video ... --ep 3 --exclude 355:510,1310:1420     # OP+ED 時間範圍

流程：
    1. 若未指定 --subs 且 素材/epNN.srt 不存在 → 跑 OCR（約 15 分鐘）
    2. 跑抽圖管線（約 5~10 分鐘）——OP、ED 每集時間都可能不同（別假設跟上一集
       一樣！），開跑前務必用字幕最長的幾句當線索、抽幾張畫面確認範圍後再用
       --exclude 指定；沒把握就先不排除，寧可留一點重複也不要誤刪真實劇情
    3. 提醒校對 素材/epNN_review.txt，改完可重跑抽圖
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run_step(name: str, cmd: list[str]) -> None:
    print(f"\n===== {name} =====")
    r = subprocess.run([sys.executable, "-u", *cmd])
    if r.returncode != 0:
        sys.exit(f"{name} 失敗（exit {r.returncode}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--ep", required=True, type=int)
    ap.add_argument("--subs", type=Path, default=None)
    ap.add_argument("--exclude", default="",
                     help='該集 OP/ED 時間範圍，格式 "起:訖,起:訖"（秒），例：355:510,1310:1420')
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"影片不存在：{args.video}")

    subs = args.subs or ROOT / "素材" / f"ep{args.ep:02d}.srt"
    if not subs.exists():
        run_step("OCR 字幕辨識", [
            str(ROOT / "pipeline" / "ocr_subs.py"),
            "--video", str(args.video), "--out", str(subs),
        ])

    extract_cmd = [
        str(ROOT / "pipeline" / "extract.py"),
        "--video", str(args.video), "--ep", str(args.ep), "--subs", str(subs),
    ]
    if args.exclude:
        extract_cmd += ["--exclude", args.exclude]
    run_step("抽圖與索引", extract_cmd)

    review = subs.with_name(subs.stem + "_review.txt")
    print(f"""
===== 完成 =====
1. 校對台詞：{review}
   （發現錯字 → 改 {subs.name} 後重跑本指令，同一集會整批重建，約幾分鐘）
2. 本機預覽：cd site && npm run dev
3. 部署：git add -A && git commit -m "第 {args.ep} 集" && git push
""")


if __name__ == "__main__":
    main()
