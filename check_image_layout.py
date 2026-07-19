"""画像レイアウトの軽量回帰検査（CI用）。

2026-07 に発生した「カード/サブ枠から文字・白背景がはみ出す」系の不具合を機械検出する。
本番と同じ描画関数を worst-case データで呼び、(1) 例外なく 1254² が出るか（スモーク）、
(2) 西表・長期の右パネル(大原)白背景がカード右端を越えて漏れていないか（ピクセル検査）を確認する。

実行: python check_image_layout.py  （失敗時 exit 1）
CI: .github/workflows/image_layout_check.yml が push 時に実行（fonts-noto-cjk 導入で本番相当）。
フォント非依存の検査（ピクセル漏れ）が主眼。日本語フォント不在の環境でもスモークは通る。
"""
import sys, tempfile, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image

import iriomote_images as II
import others_images as OI

FAILS = []
def check(cond, msg):
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        FAILS.append(msg)

def _row(dj, de, pct, susp=False):
    return {"date_ja": dj, "date_en": de, "pct": pct, "suspended": susp}

tmp = tempfile.mkdtemp()

# ── 西表 短期（ルール名ピル＋英語ラベルの溢れが起きた面）──
short_cards = [
    {"label_ja": "明日", "date_label": "7/20", "label_en": "TOMORROW", "headline_pct": 100,
     "routes": [
        {"name_ja": "上原航路", "name_en": "Uehara", "pct": 100, "suspended": False, "color": II.COL_UEHARA},
        {"name_ja": "大原航路", "name_en": "Ohara", "pct": 100, "suspended": True, "color": II.COL_OHARA}]},
    {"label_ja": "明後日", "date_label": "7/21", "label_en": "DAY AFTER", "headline_pct": 5,
     "routes": [
        {"name_ja": "上原航路", "name_en": "Uehara", "pct": 5, "suspended": False, "color": II.COL_UEHARA},
        {"name_ja": "大原航路", "name_en": "Ohara", "pct": 1, "suspended": False, "color": II.COL_OHARA}]},
]
p = os.path.join(tmp, "iri_short.png")
try:
    II.make_iriomote_short(short_cards, p)
    im = Image.open(p)
    check(im.size == (1254, 1254), f"iriomote_short renders 1254² (got {im.size})")
except Exception as e:
    check(False, f"iriomote_short raised: {e!r}")

# ── 西表 長期（右パネル白背景はみ出し＋単日表示＋運休行）──
ue = [_row("7/22(水)", "Wed", 8), _row("7/23(木)", "Thu", 4), _row("7/24(金)", "Fri", 100, True),
      _row("7/25(土)", "Sat", 5), _row("7/26(日)", "Sun", 100)]
oh = [_row("7/22(水)", "Wed", 1), _row("7/23(木)", "Thu", 1), _row("7/24(金)", "Fri", 1),
      _row("7/25(土)", "Sat", 1), _row("7/26(日)", "Sun", 100, True)]
period = {"start": "7/26", "end": "7/26", "start_en": "Jul 26", "end_en": "Jul 26",
          "uehara_max": 100, "ohara_max": 100, "has_risk": True}
p = os.path.join(tmp, "iri_long.png")
try:
    II.make_iriomote_long(period, ue, oh, p)
    im = Image.open(p).convert("RGB")
    check(im.size == (1254, 1254), f"iriomote_long renders 1254² (got {im.size})")
    # 右パネル(大原)カードの右端 ~1156。その外側 x∈[1160,1200], y∈[730,995] に
    # パネル白背景(PANEL_BG≈白)が漏れていないか。漏れると near-white のブロックが出る。
    px = im.load()
    near_white = 0
    for x in range(1160, 1201):
        for y in range(730, 996):
            r, g, b = px[x, y]
            if r > 245 and g > 245 and b > 243:
                near_white += 1
    check(near_white < 150, f"iriomote_long: no white panel spill past right card (near-white px={near_white}, allow<150)")
except Exception as e:
    check(False, f"iriomote_long raised: {e!r}")

# ── 単日と複数日レンジの両方が例外なく描けるか（単日ロジックの回帰）──
try:
    II.make_iriomote_long(dict(period, start="7/24", end="7/26", start_en="Jul 24", end_en="Jul 26"),
                          ue, oh, os.path.join(tmp, "iri_long_range.png"))
    check(True, "iriomote_long renders multi-day range without error")
except Exception as e:
    check(False, f"iriomote_long range raised: {e!r}")

# ── その他3島 長期（Suspended ラベルの右端接触が起きた面）──
def _island(nj, ne):
    return {"name_ja": nj, "name_en": ne,
            "rows": [_row("7/22(水)", "Wed", 100), _row("7/23(木)", "Thu", 100, True),
                     _row("7/24(金)", "Fri", 1), _row("7/25(土)", "Sat", 1), _row("7/26(日)", "Sun", 100)]}
o_islands = [_island("竹富", "Taketomi"), _island("波照間", "Hateruma"), _island("鳩間", "Hatoma")]
o_period = {"has_risk": True, "max_pct": 100, "start": "7/26", "end": "7/26",
            "start_en": "Jul 26", "end_en": "Jul 26"}
try:
    OI.make_others_long(o_period, o_islands, os.path.join(tmp, "others_long.png"))
    im = Image.open(os.path.join(tmp, "others_long.png"))
    check(im.size == (1254, 1254), f"others_long renders 1254² with suspended rows (got {im.size})")
except Exception as e:
    check(False, f"others_long raised: {e!r}")

print()
if FAILS:
    print(f"LAYOUT CHECK FAILED ({len(FAILS)} issue(s)):")
    for m in FAILS:
        print("  - " + m)
    sys.exit(1)
print("LAYOUT CHECK PASSED")
