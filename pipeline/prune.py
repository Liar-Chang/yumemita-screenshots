#!/usr/bin/env python3
"""刪圖同步：把你在檔案總管手動刪掉的圖片從索引移除，並記入排除清單。

流程：
    1. 打開 site\\public\\img\\e01（檔案總管、大圖示模式），把不要的 .webp 刪掉
    2. 跑本腳本（或雙擊專案根目錄的 刪圖同步.bat）
    3. 索引更新、刪掉的時間點寫入 素材/排除清單.json —— 之後重跑抽圖也不會復活
"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
PUBLIC = ROOT / "site" / "public"
INDEX = PUBLIC / "index" / "yumemita.json"
EXCLUDE = ROOT / "素材" / "排除清單.json"


def main():
    if not INDEX.exists():
        sys.exit(f"找不到索引：{INDEX}")
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    items = data["items"]

    excluded = []
    if EXCLUDE.exists():
        excluded = json.loads(EXCLUDE.read_text(encoding="utf-8-sig"))

    keep, removed = [], []
    for it in items:
        if (PUBLIC / it["img"]).exists():
            keep.append(it)
        else:
            removed.append(it)
            excluded.append({"ep": it["ep"], "t": it["t"], "text": it["text"][:20]})

    if not removed:
        print("沒有發現被刪除的圖片，索引無須變動。")
        return

    data["items"] = keep
    INDEX.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                     encoding="utf-8")
    EXCLUDE.parent.mkdir(parents=True, exist_ok=True)
    EXCLUDE.write_text(json.dumps(excluded, ensure_ascii=False, indent=1),
                       encoding="utf-8")

    print(f"已從索引移除 {len(removed)} 筆，索引剩 {len(keep)} 筆。")
    print(f"排除清單共 {len(excluded)} 筆 → {EXCLUDE}")
    for r in removed[:15]:
        print(f"  刪 第{r['ep']}集 {r['t']}s {r['text'] or '（場景）'}")
    if len(removed) > 15:
        print(f"  ...等共 {len(removed)} 筆")


if __name__ == "__main__":
    main()
