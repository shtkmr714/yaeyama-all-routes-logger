"""
西表島（石垣島⇔西表島：上原航路・大原航路）専用の画像生成。

八重山の投稿を「島ごと」に分解し、西表島だけ座間味系の洗練テンプレで独立投稿するための描画。
固定テンプレ（assets/templates/Format_iriomote_short.png / Format_iriomote_long.png）を下地にし、
可変部（日付・%・バー）だけを白塗りして再描画する（座間味 make_image_short と同じ方式）。

注意: 八重山モデルはルート別に「日次1値」の欠航%しか出さない（座間味と違いAM/PMの実データがない）。
そのため短期カードのサブボックスは AM/PM ではなく「その日の欠航%を1つ」表示する。
"""
import os

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_TPL_DIR = os.path.join(_HERE, "assets", "templates")
_FONT_DIR = os.path.join(_HERE, "assets", "fonts")

TPL_SHORT = os.path.join(_TPL_DIR, "Format_iriomote_short.png")
TPL_LONG = os.path.join(_TPL_DIR, "Format_iriomote_long.png")

# ルート色（テンプレの上原=緑ピル / 大原=紺ピルに合わせる）
COL_UEHARA = (27, 94, 32)     # #1B5E20
COL_OHARA = (13, 71, 161)     # #0D47A1
CARD_WHITE = (255, 255, 255)
LABEL_GRAY = (70, 70, 72)
NOTICE_GRAY = (120, 124, 130)


# ── フォント ──
def _noto(weights):
    """apt の fonts-noto-cjk を探す。yaeyama_publisher._find_noto_font と同じ堅牢な探索。"""
    search_dirs = [
        "/usr/share/fonts/opentype/noto",
        "/usr/share/fonts/noto-cjk",
        "/usr/share/fonts/truetype/noto",
        "/usr/share/fonts/noto",
        "/usr/local/share/fonts/noto",
        "/usr/share/fonts/opentype",
        "/usr/share/fonts/truetype",
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for w in weights:
            for ext in (".ttc", ".otf", ".ttf"):
                p = os.path.join(d, f"NotoSansCJK-{w}{ext}")
                if os.path.exists(p):
                    return p
    try:
        import subprocess
        out = subprocess.check_output(
            ["fc-list", ":lang=ja", "--format=%{file}\n"],
            text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            line = line.strip()
            if line and "Noto" in line and "Sans" in line:
                return line
    except Exception:
        pass
    return None


_NOTO_BOLD = _noto(["Black", "Bold"])
_NOTO_MED = _noto(["Medium", "Regular"])
_INTER = os.path.join(_FONT_DIR, "Inter-var.ttf")
_MANROPE = os.path.join(_FONT_DIR, "Manrope-var.ttf")


def _font(path, size, variation=None):
    try:
        f = ImageFont.truetype(path, size)
        if variation:
            try:
                f.set_variation_by_name(variation)
            except Exception:
                pass
        return f
    except Exception:
        return ImageFont.load_default()


def _nj(sz):   return _font(_NOTO_MED, sz)                 # 日本語・混在
def _njb(sz):  return _font(_NOTO_BOLD, sz)                # 日本語太字
def _num(sz):  return _font(_MANROPE, sz, "Bold")         # 数字%
def _en(sz):   return _font(_INTER, sz, "Medium")         # 英語


# ── リスクバンド（下部の目安バーに一致）──
def _band(pct):
    """pct(0-100) → (和名, 英名, バー色, テキスト色)。"""
    if pct is None:
        return ("—", "N/A", (150, 150, 150), (110, 110, 110))
    if pct < 10:
        return ("低い", "LOW", (30, 126, 52), (30, 126, 52))
    if pct < 30:
        return ("やや低い", "LOW-MID", (124, 179, 66), (95, 140, 45))
    if pct < 50:
        return ("やや高い", "MID", (249, 190, 20), (176, 132, 0))
    if pct < 80:
        return ("高い", "HIGH", (245, 124, 0), (216, 108, 0))
    return ("非常に高い", "VERY HIGH", (211, 47, 47), (211, 47, 47))


def _cancel_icon(draw, cx, cy, r, color):
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=color, width=4)
    o = int(r * 0.5)
    draw.line([(cx - o, cy - o), (cx + o, cy + o)], fill=color, width=4)
    draw.line([(cx - o, cy + o), (cx + o, cy - o)], fill=color, width=4)


def _dashed_rrect(draw, box, radius, color, width=3, dash=13, gap=9):
    x0, y0, x1, y1 = box
    def seg(a, b, horiz):
        length = (b[0] - a[0]) if horiz else (b[1] - a[1])
        n = int(abs(length) // (dash + gap))
        step = dash + gap
        for i in range(n + 1):
            s = i * step
            if horiz:
                xs = a[0] + s
                draw.line([(xs, a[1]), (min(xs + dash, b[0]), a[1])], fill=color, width=width)
            else:
                ys = a[1] + s
                draw.line([(a[0], ys), (a[0], min(ys + dash, b[1]))], fill=color, width=width)
    seg((x0 + radius, y0), (x1 - radius, y0), True)
    seg((x0 + radius, y1), (x1 - radius, y1), True)
    seg((x0, y0 + radius), (x0, y1 - radius), False)
    seg((x1, y0 + radius), (x1, y1 - radius), False)


# ============================================================
# 短期（明日・明後日 × 上原/大原）
# ============================================================
IRI_CARDS = [(519, 63, 857, 967), (895, 63, 1233, 967)]


def make_iriomote_short(cards, output_path):
    """cards: 長さ2のリスト。各要素:
      {label_ja:'明日', date_label:'6/12', label_en:'TOMORROW',
       headline_pct:int, routes:[{name_ja:'上原航路',name_en:'Uehara Route',
                                   pct:int, suspended:bool, color:(r,g,b)}, {大原...}]}"""
    try:
        img = Image.open(TPL_SHORT).convert("RGB")
    except Exception as e:
        print(f"  [警告] 西表短期テンプレ読込失敗（{e}）→ 白背景で代替")
        img = Image.new("RGB", (1254, 1254), "white")
    draw = ImageDraw.Draw(img)

    f_badge = _nj(31)
    f_en = _en(27)
    f_big = _num(150)
    f_pct = _num(70)
    f_riskjp = _nj(29)
    f_risken = _en(23)
    f_pill = _njb(24)
    f_route_en = _en(22)
    f_boat = _nj(24)
    f_val = _num(50)
    f_susp = _njb(34)
    f_susp_en = _en(17)

    def big_pct(cx, cy, pct, color):
        num = str(pct)
        nb = draw.textbbox((0, 0), num, font=f_big)
        pb = draw.textbbox((0, 0), "%", font=f_pct)
        nw, pw = nb[2] - nb[0], pb[2] - pb[0]
        gap = 6
        x0 = cx - (nw + gap + pw) // 2
        draw.text((x0, cy), num, font=f_big, fill=color, anchor="lm")
        draw.text((x0 + nw + gap, cy + 30), "%", font=f_pct, fill=color, anchor="lm")

    def route_box(box, route):
        bx0, by0, bx1, by1 = box
        cx = (bx0 + bx1) // 2
        col = route["color"]
        if route.get("suspended"):
            draw.rounded_rectangle(box, radius=18, fill=(238, 240, 242))
            _dashed_rrect(draw, box, 18, NOTICE_GRAY, width=3, dash=13, gap=9)
            # ヘッダピル
            _pill(draw, bx0 + 20, by0 + 16, route["name_ja"], route["name_en"], col, f_pill, f_route_en)
            mid_y = by0 + (by1 - by0) // 2 + 20
            sw = draw.textbbox((0, 0), "運休", font=f_susp)[2]
            r = 17
            gw = r * 2 + 10 + sw
            gx = cx - gw // 2
            _cancel_icon(draw, gx + r, mid_y, r, (90, 96, 104))
            draw.text((gx + r * 2 + 10, mid_y), "運休", font=f_susp, fill=(60, 64, 70), anchor="lm")
            draw.text((cx, mid_y + 30), "Suspended", font=f_susp_en, fill=NOTICE_GRAY, anchor="mm")
            return
        # 通常
        tint = tuple(int(c + (255 - c) * 0.86) for c in col)
        draw.rounded_rectangle(box, radius=18, fill=tint)
        _pill(draw, bx0 + 20, by0 + 16, route["name_ja"], route["name_en"], col, f_pill, f_route_en)
        draw.text((bx0 + 26, by0 + 78), "高速船  High-speed boat", font=f_boat, fill=LABEL_GRAY, anchor="lm")
        pct = route["pct"]
        _band_col = _band(pct)[2]
        draw.text((bx0 + 26, by0 + 128), f"{pct}%", font=f_val, fill=col, anchor="lm")

    for (x0, y0, x1, y1), day in zip(IRI_CARDS, cards):
        if not day:
            continue
        cx = (x0 + x1) // 2
        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=30, fill=CARD_WHITE)

        head = day["headline_pct"]
        bj, be, bcol, btxt = _band(head)

        # 日付バッジ
        draw.rounded_rectangle([(cx - 96, 90), (cx + 96, 145)], radius=13, fill=btxt)
        draw.text((cx, 117), f"{day['label_ja']}  {day['date_label']}", font=f_badge, fill="white", anchor="mm")
        draw.text((cx, 184), day["label_en"].upper(), font=f_en, fill=btxt, anchor="mm")

        # 巨大%
        big_pct(cx, 300, head, btxt)

        # 区切り線
        draw.line([(x0 + 42, 448), (x1 - 42, 448)], fill=(214, 216, 220), width=2)
        # リスク文言
        draw.text((cx, 488), f"欠航リスク：{bj}", font=f_riskjp, fill=btxt, anchor="mm")
        draw.text((cx, 524), f"{be} RISK", font=f_risken, fill=btxt, anchor="mm")

        # 2ルートのサブボックス（左右を広げてゆとりを持たせる）
        route_box((x0 + 14, 583, x1 - 14, 748), day["routes"][0])
        route_box((x0 + 14, 783, x1 - 14, 948), day["routes"][1])

    img.save(output_path)
    return output_path


def _pill(draw, x, y, name_ja, name_en, color, f_ja, f_en):
    """左寄せの丸ピル（島名=色ピル白文字）＋右にグレー英語。"""
    pad = 12
    tb = draw.textbbox((0, 0), name_ja, font=f_ja)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pill = [(x, y), (x + tw + pad * 2, y + th + 16)]
    draw.rounded_rectangle(pill, radius=9, fill=color)
    draw.text((x + pad, y + 8 + th // 2), name_ja, font=f_ja, fill="white", anchor="lm")
    draw.text((x + tw + pad * 2 + 12, y + 8 + th // 2), name_en, font=f_en, fill=(90, 94, 100), anchor="lm")


# ============================================================
# 長期（3〜7日先の7日間 × 上原/大原）
# ============================================================
PANEL_BG = (252, 252, 251)

# リスク期間ボックス（右上・白パネル）の可変部
LT_DATE_C = (865, 314)       # 「6/7 〜 6/13」中央
LT_DATE_EN = (865, 378)      # 「Jun 7 – Jun 13」中央
LT_PCT_L = (702, 542)        # 上原の最大%中央
LT_PCT_R = (1037, 542)       # 大原の最大%中央

# 7日バーパネル（左=上原 / 右=大原）
LT_PANEL_L = (108, 720, 612, 998)
LT_PANEL_R = (652, 720, 1152, 998)
LT_ROW_Y0 = 742
LT_ROW_BOTTOM = 976   # 最終行の中心。この範囲(742〜976)に行数ぶんを均等配置する。
LT_ROW_STEP = 39      # 7行時の実測ステップ（後方互換の目安）


def _bar(draw, x0, x1, y, pct, suspended, f_pct, f_susp, f_susp_en):
    """横バー1本。track=黒 / fill=バンド色。運休=ハッチ＋赤字。"""
    h = 15
    top, bot = y - h // 2, y + h // 2
    if suspended:
        draw.rounded_rectangle([(x0, top), (x1, bot)], radius=7, fill=(225, 227, 229))
        # 斜線ハッチ
        for hx in range(x0, x1, 12):
            draw.line([(hx, bot), (min(hx + 8, x1), top)], fill=(176, 180, 184), width=2)
        draw.text((x1 + 22, y - 8), "運休", font=f_susp, fill=(211, 47, 47), anchor="lm")
        draw.text((x1 + 22, y + 14), "Suspended", font=f_susp_en, fill=(211, 47, 47), anchor="lm")
        return
    draw.rounded_rectangle([(x0, top), (x1, bot)], radius=7, fill=(20, 20, 22))
    col = _band(pct)[2]
    if pct and pct > 0:
        fw = int((x1 - x0) * min(pct, 100) / 100)
        fw = max(fw, 10)
        draw.rounded_rectangle([(x0, top), (x0 + fw, bot)], radius=7, fill=col)
    txt_col = _band(pct)[3] if (pct or 0) >= 30 else (60, 64, 70)
    draw.text((x1 + 22, y), f"{pct}%", font=f_pct, fill=txt_col, anchor="lm")


def make_iriomote_long(period, uehara, ohara, output_path):
    """period: {start,end,start_en,end_en, uehara_max, ohara_max}
       uehara/ohara: 長さ7のリスト [{date_ja:'6/7(土)', date_en:'Sat', pct:int, suspended:bool}]"""
    try:
        img = Image.open(TPL_LONG).convert("RGB")
    except Exception as e:
        print(f"  [警告] 西表長期テンプレ読込失敗（{e}）→ 白背景で代替")
        img = Image.new("RGB", (1254, 1254), "white")
    draw = ImageDraw.Draw(img)

    f_dates = _num(84)
    f_dates_en = _en(26)
    f_max = _num(78)
    f_maxpct = _num(40)
    f_row_ja = _nj(20)
    f_row_en = _en(16)
    f_barpct = _num(26)
    f_susp = _njb(20)
    f_susp_en = _en(13)

    # ── リスク期間ボックス：日付と最大%を白塗り→再描画 ──
    draw.rectangle([(598, 276), (1140, 352)], fill="white")   # 「6/7〜6/13」
    draw.rectangle([(690, 360), (1040, 398)], fill="white")   # 「Jun 7 – Jun 13」
    draw.rectangle([(612, 498), (836, 588)], fill="white")    # 上原 最大%
    draw.rectangle([(942, 498), (1164, 588)], fill="white")   # 大原 最大%

    NOCONCERN = (46, 125, 50)   # 座間味と同じ緑
    if period.get("has_risk"):
        # リスク期間の日付は重症度連動色（座間味・渡嘉敷と同挙動。全期間の最大%のバンド色）。
        overall_max = max(period.get("uehara_max", 0), period.get("ohara_max", 0))
        date_col = _band(overall_max)[3]
        # 「6/7 〜 6/13」: 数字はManrope、区切り「〜」はNoto（Manropeに無く豆腐化するため）で合成
        f_sep = _njb(70)
        s1, s2 = period["start"], period["end"]
        if s1 == s2:
            # リスク日が1日：その日だけを中央表示（「X 〜 X」にしない）
            w1 = draw.textbbox((0, 0), s1, font=f_dates)[2]
            draw.text((LT_DATE_C[0] - w1 // 2, LT_DATE_C[1]), s1,
                      font=f_dates, fill=date_col, anchor="lm")
            en_text = f"Around {period['start_en']}"
        else:
            sep = "  〜  "
            w1 = draw.textbbox((0, 0), s1, font=f_dates)[2]
            ws = draw.textbbox((0, 0), sep, font=f_sep)[2]
            w2 = draw.textbbox((0, 0), s2, font=f_dates)[2]
            total = w1 + ws + w2
            x = LT_DATE_C[0] - total // 2
            cy = LT_DATE_C[1]
            draw.text((x, cy), s1, font=f_dates, fill=date_col, anchor="lm")
            draw.text((x + w1, cy), sep, font=f_sep, fill=date_col, anchor="lm")
            draw.text((x + w1 + ws, cy), s2, font=f_dates, fill=date_col, anchor="lm")
            en_text = f"Around {period['start_en']} - {period['end_en']}"
        draw.text(LT_DATE_EN, en_text, font=f_dates_en, fill=(90, 100, 120), anchor="mm")
    else:
        # リスクが低い期間は日付を出さず「懸念なし」を表示（座間味・渡嘉敷と同挙動）
        cxm = LT_DATE_C[0]
        text = "懸念なし  No Significant Risk"
        size = 52
        while size > 30:
            fnt = _njb(size)
            if draw.textbbox((0, 0), text, font=fnt)[2] <= 540:
                break
            size -= 2
        draw.text((cxm, (LT_DATE_C[1] + LT_DATE_EN[1]) // 2), text,
                  font=_njb(size), fill=NOCONCERN, anchor="mm")

    def big_maxpct(center, pct):
        cx, cy = center
        col = _band(pct)[3]   # 最大%も重症度連動色（座間味と同挙動）
        num = str(pct)
        nb = draw.textbbox((0, 0), num, font=f_max)
        pb = draw.textbbox((0, 0), "%", font=f_maxpct)
        nw, pw = nb[2] - nb[0], pb[2] - pb[0]
        gap = 4
        x0 = cx - (nw + gap + pw) // 2
        draw.text((x0, cy), num, font=f_max, fill=col, anchor="lm")
        draw.text((x0 + nw + gap, cy + 18), "%", font=f_maxpct, fill=col, anchor="lm")

    big_maxpct(LT_PCT_L, period["uehara_max"])
    big_maxpct(LT_PCT_R, period["ohara_max"])

    # ── 7日バーパネル：本文を白塗り→再描画（ヘッダは残す）──
    # 角丸パネルからはみ出さないよう、角丸＋左右わずかにインセットで塗る。
    for (px0, py0, px1, py1) in (LT_PANEL_L, LT_PANEL_R):
        draw.rounded_rectangle([(px0 + 3, py0), (px1 - 3, py1)], radius=24, fill=PANEL_BG)

    n_rows = max(len(uehara), len(ohara), 1)
    row_step = (LT_ROW_BOTTOM - LT_ROW_Y0) / (n_rows - 1) if n_rows > 1 else 0

    def render_panel(panel, rows):
        px0 = panel[0]
        date_x = px0 + 4
        en_x = px0 + 108
        bar_x0 = px0 + 150
        bar_x1 = px0 + 400
        for i, r in enumerate(rows):
            y = int(LT_ROW_Y0 + i * row_step)
            draw.text((date_x, y), r["date_ja"], font=f_row_ja, fill=(40, 44, 50), anchor="lm")
            draw.text((en_x, y), r["date_en"], font=f_row_en, fill=(120, 124, 130), anchor="lm")
            _bar(draw, bar_x0, bar_x1, y, r.get("pct"), r.get("suspended", False),
                 f_barpct, f_susp, f_susp_en)

    render_panel(LT_PANEL_L, uehara)
    render_panel(LT_PANEL_R, ohara)

    img.save(output_path)
    return output_path
