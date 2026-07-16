#!/usr/bin/env python3
"""從內嵌字幕影片 OCR 出 .srt 字幕檔（木棉花白字黑邊樣式）。

策略（速度關鍵）：
  1. ffmpeg 一次掃出整部影片的字幕帶裁切圖（2fps）
  2. 快速像素遮罩（白字＋深色描邊）偵測「字幕內容何時出現/變化/消失」→ 分段
  3. 每段只 OCR 一次（取中間幀），依位置過濾掉工作人員字幕等雜訊
  4. 輸出 .srt ＋ 校對用 review.txt

用法：
    python ocr_subs.py --video 素材/ep01.mp4 --out 素材/ep01.srt
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------- 參數（依 720p 木棉花樣式校準） ----------

FPS = 2.0              # 取樣頻率（每秒張數）
BAND_Y, BAND_H = 570, 150   # 字幕帶（720p 座標）
WHITE_THR = 215        # 白色文字像素門檻（R/G/B 皆須超過）
DARK_THR = 90          # 描邊深色門檻
MIN_TEXT_PX = 220      # 判定「有字幕」的最少文字像素數
MASK_SIM_THR = 0.80    # 相鄰取樣遮罩相似度低於此值 → 視為換句（太低會把相鄰兩句合併）
CENTER_X_RATIO = 0.45  # OCR 結果只保留中心 x 落在畫面中間這個比例內的框（OP 標題卡的製作名單偏左/右，要擋掉）
FUZZY_MERGE = 0.85     # 相鄰段落文字相似度超過此值 → 合併


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


# ---------- 第 1 步：批次裁出字幕帶 ----------

def dump_bands(video: Path, work: Path) -> list[Path]:
    print(f"  [1/4] ffmpeg 掃描字幕帶（{FPS}fps）...")
    out_pattern = work / "band_%06d.png"
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video),
         "-vf", f"fps={FPS},crop=1280:{BAND_H}:0:{BAND_Y}",
         str(out_pattern)],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg 失敗：{r.stderr[-500:]}")
    frames = sorted(work.glob("band_*.png"))
    print(f"        共 {len(frames)} 張（{len(frames)/FPS/60:.1f} 分鐘）")
    return frames


# ---------- 第 2 步：文字遮罩與分段 ----------

def text_mask(img: Image.Image) -> np.ndarray:
    """白色像素且鄰近有深色描邊 → 字幕文字遮罩。"""
    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    white = (a > WHITE_THR).all(axis=2)
    dark = (a < DARK_THR).all(axis=2)
    near_dark = dark.copy()
    for dy in (-2, -1, 1, 2):
        near_dark |= np.roll(dark, dy, axis=0)
    for dx in (-2, -1, 1, 2):
        near_dark |= np.roll(dark, dx, axis=1)
    return white & near_dark


def mask_signature(mask: np.ndarray) -> np.ndarray:
    """降採樣成粗網格向量，供相似度比較。"""
    h, w = mask.shape
    gh, gw = 10, 80
    sig = mask[: h - h % gh, : w - w % gw]
    sig = sig.reshape(gh, h // gh, gw, w // gw).sum(axis=(1, 3))
    return sig.astype(np.float32)


def sig_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 1.0 if na == nb else 0.0
    return float((a * b).sum() / (na * nb))


def segment(frames: list[Path]) -> list[dict]:
    """回傳 [{i0, i1}]（取樣索引閉區間，內容視為同一句）。"""
    print("  [2/4] 像素遮罩分段...")
    segs, cur, prev_sig = [], None, None
    for i, f in enumerate(frames):
        m = text_mask(Image.open(f))
        has = int(m.sum()) >= MIN_TEXT_PX
        sig = mask_signature(m) if has else None
        if has and cur is None:
            cur = {"i0": i, "i1": i, "sig0": sig}
        elif has and cur is not None:
            # 同時比對前一幀與段落起點：兩句字幕交叉淡變的「橋接幀」會讓相鄰比對
            # 一路都過關（A→混合→B 每步都像），但跟段落起點比就會露餡
            if (sig_similarity(prev_sig, sig) >= MASK_SIM_THR
                    and sig_similarity(cur["sig0"], sig) >= MASK_SIM_THR - 0.08):
                cur["i1"] = i
            else:
                segs.append(cur)
                cur = {"i0": i, "i1": i, "sig0": sig}
        elif not has and cur is not None:
            segs.append(cur)
            cur = None
        prev_sig = sig
        if (i + 1) % 500 == 0:
            print(f"        {i+1}/{len(frames)}")
    if cur is not None:
        segs.append(cur)
    print(f"        偵測到 {len(segs)} 個字幕段")
    return segs


# ---------- 第 3 步：OCR ----------

def ocr_segment(ocr, frame: Path) -> str:
    r = ocr(str(frame))
    if r.txts is None or len(r.txts) == 0:
        return ""
    W = 1280
    keep = []
    for box, txt, score in zip(r.boxes, r.txts, r.scores):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = (min(xs) + max(xs)) / 2
        if abs(cx - W / 2) > W * CENTER_X_RATIO / 2:
            continue  # 偏離中央 → 工作人員字幕等雜訊
        if score < 0.55:
            continue
        keep.append((min(ys), txt))
    if not keep:
        return ""
    keep.sort()  # 由上而下（雙行字幕）
    # 只取最底部的文字群：字幕永遠貼底，OP 置中的工作人員名字位置較高
    bottom = keep[-1][0]
    keep = [(y, t) for y, t in keep if y >= bottom - 62]
    text = " ".join(t for _, t in keep)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------- 文字清理（VTuber 題材畫面雜訊多：直播 UI、文件、看板） ----------

CJK_RE = re.compile(r"[一-鿿]")
KANA_RE = re.compile(r"[぀-ヿ]")
CREDIT_TOKEN_RE = re.compile(r"^(作詞|作曲|編曲|主題歌|插入歌|音樂|音楽)")


def kana_ratio(s: str) -> float:
    return len(KANA_RE.findall(s)) / max(1, len(s))


def clean_line(text: str) -> str:
    """去掉 OCR 撈到的畫面雜訊：開頭/結尾的純英數碎片、日文畫面文字污染。"""
    toks = text.split(" ")
    # OP/ED 標題卡的「作曲田淵智也」這類 credit+人名連寫 token
    toks = [tk for tk in toks if not (CREDIT_TOKEN_RE.match(tk) and len(tk) <= 8)]
    while toks and not CJK_RE.search(toks[0]):
        toks.pop(0)  # 開頭純英數碎片（直播 UI 圖示誤認）
    out: list[str] = []
    for i, tk in enumerate(toks):
        if i > 0 and KANA_RE.search(tk):
            break  # 繁中官方字幕不含假名 → 後面是畫面上的日文文件/看板，截斷
        out.append(tk)
    while out and not CJK_RE.search(out[-1]):
        out.pop()
    t = " ".join(out).strip()
    if not CJK_RE.search(t):
        return ""
    if kana_ratio(t) >= 0.15:
        return ""  # 整行都是日文畫面文字（ED 名單、簡介卡等）
    if re.match(r"^(作詞|作曲|編曲|歌唱?|演唱|主題歌|插入歌)\s*[:：]?\s*\S{1,8}$", t):
        return ""  # OP/ED 的製作名單行（「作詞 田淵智也」這類），非台詞
    return t


# ---------- 第 4 步：合併與輸出 ----------

def fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def to_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()
    if not args.video.exists():
        sys.exit(f"影片不存在：{args.video}")
    out = args.out or args.video.with_suffix(".srt")

    work = Path(tempfile.mkdtemp(prefix="ocrsubs_"))
    try:
        frames = dump_bands(args.video, work)
        segs = segment(frames)

        print("  [3/4] OCR 各段（首次載入模型需數秒）...")
        import logging
        logging.disable(logging.INFO)
        from rapidocr import RapidOCR
        ocr = RapidOCR()
        events = []
        for k, s in enumerate(segs, 1):
            # 首選段落中間幀；若剛好落在字幕淡入淡出的過渡幀（辨識失敗），改試其他幀
            i0, i1 = s["i0"], s["i1"]
            mid = (i0 + i1) // 2
            candidates = list(dict.fromkeys(
                [mid, i0 + (i1 - i0) // 3, i1 - (i1 - i0) // 3, i0, i1]))
            text = ""
            for ci in candidates:
                text = clean_line(ocr_segment(ocr, frames[ci]))
                if text:
                    break
            if text:
                events.append({
                    "start": s["i0"] / FPS,
                    "end": (s["i1"] + 1) / FPS,
                    "text": text,
                })
            if k % 40 == 0 or k == len(segs):
                print(f"        {k}/{len(segs)}")

        # 相鄰同文字段合併（換景造成的遮罩抖動會切開同一句）
        print("  [4/4] 合併與輸出...")
        merged = []
        for e in events:
            if merged and fuzzy(merged[-1]["text"], e["text"]) >= FUZZY_MERGE \
                    and e["start"] - merged[-1]["end"] <= 1.2:
                merged[-1]["end"] = e["end"]
            else:
                merged.append(dict(e))

        lines = []
        for n, e in enumerate(merged, 1):
            lines.append(f"{n}\n{to_srt_time(e['start'])} --> {to_srt_time(e['end'])}\n{e['text']}\n")
        out.write_text("\n".join(lines), encoding="utf-8")

        review = out.with_name(out.stem + "_review.txt")
        review.write_text(
            "\n".join(f"[{to_srt_time(e['start'])[:-4]}] {e['text']}" for e in merged),
            encoding="utf-8")
        print(f"完成：{len(merged)} 句 → {out}")
        print(f"校對檔：{review}")
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
