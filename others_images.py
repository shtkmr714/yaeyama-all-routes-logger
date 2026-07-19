"""
八重山「その他3島」（竹富・波照間・鳩間）の投稿画像を、座間味・渡嘉敷・西表と同じ
洗練トーン（海グラデ背景・白カード・リスクバンド配色・下部の目安バー）で描画する。
島マップは入れない（3島まとめのため単一マップが馴染まない）。

iriomote_images の描画部品（_band / フォント / _pill 等）を再利用する。
"""
import os

from PIL import Image, ImageDraw

import iriomote_images as II

_band = II._band
_nj, _njb, _num, _en = II._nj, II._njb, II._num, II._en

W = 1254
_PHOTO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "photos")
BG_SHORT = os.path.join(_PHOTO_DIR, "others_short_bg.jpg")   # 竹富島の集落
BG_LONG = os.path.join(_PHOTO_DIR, "others_long_bg.jpg")     # 波照間島（日本最南端）
BG_WEATHER = os.path.join(_PHOTO_DIR, "others_weather_bg.jpg")  # 鳩間島
BG_IRIOMOTE_WEATHER = os.path.join(_PHOTO_DIR, "iriomote_weather_bg.jpg")  # 西表島（気象面用）


def _photo_bg(path, keep=0.55, top_dark=0.42, top_h=300):
    """写真を正方形にセンタークロップし、白カード・白文字が映えるよう暗く敷く。
    keep=元画像の残存率（小さいほど暗い）。上部はヘッダ可読性のため追加で暗くする。"""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        # 写真が無ければ海グラデにフォールバック
        return _ocean_bg()
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((W, W))
    im = Image.blend(im, Image.new("RGB", (W, W), (6, 24, 44)), 1 - keep)  # 全体を暗い紺へ寄せる
    # 上部ヘッダ帯を追加で暗く（白文字の可読性）
    mask = Image.new("L", (W, W), 0)
    md = ImageDraw.Draw(mask)
    for yy in range(top_h):
        md.line([(0, yy), (W, yy)], fill=int(top_dark * 255 * (1 - yy / top_h)))
    im = Image.composite(Image.new("RGB", (W, W), (0, 0, 0)), im, mask)
    return im


# ── 海グラデ背景（写真読込失敗時のフォールバック）──
def _ocean_bg(size=(W, W)):
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    top, bot = (6, 124, 190), (1, 58, 116)
    for yy in range(h):
        t = yy / h
        d.line([(0, yy), (w, yy)],
               fill=tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
    return img


# ── 下部リスク5段階ガイド（座間味 _draw_risk_guide と同じ）──
_GUIDE_ITEMS = [
    ("0-10%", "低い", "LOW", (46, 125, 50)),
    ("10-30%", "やや低い", "LOW-MID", (104, 159, 56)),
    ("30-50%", "やや高い", "MID", (240, 160, 0)),
    ("50-80%", "高い", "HIGH", (230, 81, 0)),
    ("80-100%", "非常に高い", "VERY HIGH", (178, 28, 28)),
]


def _risk_guide(draw, cx, y):
    f_pct, f_ja, f_en = _en(15), _nj(15), _en(12)
    span, n = 1090, len(_GUIDE_ITEMS)
    x0, step = cx - span // 2, span // len(_GUIDE_ITEMS)
    for i, (rng, ja, en, col) in enumerate(_GUIDE_ITEMS):
        ix = x0 + i * step + 14
        draw.ellipse([ix, y - 12, ix + 24, y + 12], fill=col)
        tx = ix + 36
        draw.text((tx, y - 18), rng, font=f_pct, fill=(40, 50, 70), anchor="lm")
        draw.text((tx, y + 2), ja, font=f_ja, fill=(40, 50, 70), anchor="lm")
        draw.text((tx, y + 20), en, font=f_en, fill=(120, 130, 150), anchor="lm")


def _header(draw, title_ja, title_en, line_ja, line_en):
    cx = W // 2
    draw.text((cx, 44), "FERRY CANCELLATION RISK", font=_en(20), fill=(210, 232, 250), anchor="mm")
    draw.text((cx, 96), title_ja, font=_njb(58), fill="white", anchor="mm")
    draw.text((cx, 146), title_en, font=_nj(25), fill=(206, 226, 245), anchor="mm")
    draw.text((cx, 198), line_ja, font=_njb(34), fill="white", anchor="mm")
    draw.text((cx, 234), line_en, font=_en(22), fill=(190, 214, 240), anchor="mm")


ISLAND_PILL = (13, 71, 161)   # 島名ピル（紺・統一）
CARD_WHITE = (255, 255, 255)
LABEL_GRAY = (70, 70, 72)


# ============================================================
# 短期（明日・明後日 × 3島）
# ============================================================
OTH_CARDS = [(60, 276, 615, 1052), (639, 276, 1194, 1052)]


def make_others_short(cards, output_path):
    """cards: 長さ2。各要素 {label_ja,date_label,label_en,headline_pct,
       islands:[{name_ja,name_en,pct,suspended}, x3]}"""
    img = _photo_bg(BG_SHORT)
    draw = ImageDraw.Draw(img)
    _header(draw, "フェリー欠航予測", "AIによる欠航リスク予測",
            "八重山（竹富・波照間・鳩間）",
            "Yaeyama (Taketomi / Hateruma / Hatoma)")

    f_badge = _njb(30)
    f_en = _en(26)
    f_big = _num(120)
    f_pct = _num(56)
    f_riskjp = _njb(27)
    f_risken = _en(22)
    f_isl = _njb(28)
    f_isl_en = _en(16)
    f_val = _num(48)
    f_susp = _njb(26)

    def big_pct(cx, cy, pct, color):
        num = str(pct)
        nw = draw.textbbox((0, 0), num, font=f_big)[2]
        pw = draw.textbbox((0, 0), "%", font=f_pct)[2]
        gap = 6
        x0 = cx - (nw + gap + pw) // 2
        draw.text((x0, cy), num, font=f_big, fill=color, anchor="lm")
        draw.text((x0 + nw + gap, cy + 26), "%", font=f_pct, fill=color, anchor="lm")

    for (x0, y0, x1, y1), day in zip(OTH_CARDS, cards):
        if not day:
            continue
        cx = (x0 + x1) // 2
        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=30, fill=CARD_WHITE)

        head = day["headline_pct"]
        bj, be, bcol, btxt = _band(head)

        draw.rounded_rectangle([(cx - 108, y0 + 34), (cx + 108, y0 + 90)], radius=13, fill=btxt)
        draw.text((cx, y0 + 62), f"{day['label_ja']}  {day['date_label']}",
                  font=f_badge, fill="white", anchor="mm")
        draw.text((cx, y0 + 128), day["label_en"].upper(), font=f_en, fill=btxt, anchor="mm")
        big_pct(cx, y0 + 226, head, btxt)
        draw.line([(x0 + 44, y0 + 320), (x1 - 44, y0 + 320)], fill=(214, 216, 220), width=2)
        draw.text((cx, y0 + 360), f"欠航リスク：{bj}", font=f_riskjp, fill=btxt, anchor="mm")
        draw.text((cx, y0 + 394), f"{be} RISK", font=f_risken, fill=btxt, anchor="mm")

        # 3島の行（島名＝左 / 欠航% ＝右。はみ出さないシンプル構成）
        rows_top = y0 + 440
        row_h = 112
        for i, isl in enumerate(day["islands"]):
            ry0 = rows_top + i * row_h
            rbox = (x0 + 26, ry0, x1 - 26, ry0 + row_h - 18)
            rcy = (rbox[1] + rbox[3]) // 2
            pct = isl["pct"]
            suspended = isl.get("suspended")
            if suspended:
                draw.rounded_rectangle(rbox, radius=16, fill=(238, 240, 242))
                II._dashed_rrect(draw, rbox, 16, (120, 124, 130), width=3, dash=13, gap=9)
            else:
                draw.rounded_rectangle(rbox, radius=16, fill=(244, 247, 250))
            # 左: 島名（JP太字＋EN）。左の色ドットで島を識別。
            dot = _band(pct)[2] if not suspended else (150, 150, 150)
            draw.ellipse([(rbox[0] + 22, rcy - 9), (rbox[0] + 40, rcy + 9)], fill=dot)
            draw.text((rbox[0] + 56, rcy - 13), isl["name_ja"], font=f_isl, fill=(30, 40, 60), anchor="lm")
            draw.text((rbox[0] + 56, rcy + 16), isl["name_en"], font=f_isl_en, fill=(120, 128, 140), anchor="lm")
            # 右: 値
            if suspended:
                draw.text((rbox[2] - 28, rcy), "運休 Suspended", font=f_susp, fill=(211, 47, 47), anchor="rm")
            else:
                draw.text((rbox[2] - 28, rcy), f"{pct}%", font=f_val, fill=_band(pct)[3], anchor="rm")

    # 目安バー
    gy = 1075
    draw.rounded_rectangle([(40, gy), (1214, gy + 92)], radius=18, fill=(248, 250, 252))
    draw.text((W // 2, gy + 26), "欠航リスクの目安  RISK LEVEL GUIDE",
              font=_njb(22), fill=(40, 50, 70), anchor="mm")
    _risk_guide(draw, W // 2, gy + 64)

    draw.text((W // 2, 1190), "※AI予測・参考値。欠航判断は安栄観光公式HPをご確認ください。",
              font=_nj(16), fill=(225, 235, 248), anchor="mm")
    draw.text((W // 2, 1216), "*AI estimates. Check Anei Kanko official for cancellations.",
              font=_en(15), fill=(190, 210, 235), anchor="mm")

    img.save(output_path)
    return output_path


# ============================================================
# 長期（3〜7日先の5日間 × 3島）
# ============================================================
OTH_PANELS = [(40, 566, 420, 1016), (437, 566, 817, 1016), (834, 566, 1214, 1016)]
OTH_PANEL_COL = [(27, 94, 32), (13, 71, 161), (120, 60, 150)]  # 竹富=緑 波照間=紺 鳩間=紫


def make_others_long(period, islands, output_path):
    """period: {has_risk, start,end,start_en,end_en, max_pct}
       islands: [{name_ja,name_en,rows:[{date_ja,date_en,pct,suspended} x5]} x3]"""
    img = _photo_bg(BG_LONG)
    draw = ImageDraw.Draw(img)
    cx = W // 2

    # ヘッダ
    draw.text((cx, 48), "フェリー欠航可能性 長期予報（3〜7日先）", font=_njb(42), fill="white", anchor="mm")
    draw.text((cx, 92), "Ferry Cancellation Risk  /  Long-term Forecast (3-7 days ahead)",
              font=_en(21), fill=(206, 226, 245), anchor="mm")
    draw.text((cx, 132), "八重山（竹富・波照間・鳩間）  Yaeyama (Taketomi / Hateruma / Hatoma)",
              font=_nj(23), fill=(190, 214, 240), anchor="mm")

    # リスク期間ボックス（中央・白カード）
    bx = (327, 186, 927, 500)
    draw.rounded_rectangle(bx, radius=22, fill=(248, 250, 252))
    bcx = (bx[0] + bx[2]) // 2
    draw.text((bcx, 226), "欠航リスク期間  Risk Period", font=_nj(24), fill=(90, 100, 120), anchor="mm")
    draw.line([(bx[0] + 40, 268), (bx[2] - 40, 268)], fill=(225, 228, 234), width=2)
    if period.get("has_risk"):
        col = _band(period.get("max_pct", 0))[3]
        f_dates = _num(70)
        f_sep = _njb(58)
        s1, s2 = period["start"], period["end"]
        sep = "  〜  "
        w1 = draw.textbbox((0, 0), s1, font=f_dates)[2]
        ws = draw.textbbox((0, 0), sep, font=f_sep)[2]
        x = bcx - (w1 + ws + draw.textbbox((0, 0), s2, font=f_dates)[2]) // 2
        draw.text((x, 330), s1, font=f_dates, fill=col, anchor="lm")
        draw.text((x + w1, 330), sep, font=f_sep, fill=col, anchor="lm")
        draw.text((x + w1 + ws, 330), s2, font=f_dates, fill=col, anchor="lm")
        draw.text((bcx, 392), f"{period['start_en']} – {period['end_en']}",
                  font=_en(24), fill=(90, 100, 120), anchor="mm")
        b = _band(period.get("max_pct", 0))
        draw.text((bcx, 452), f"最大 {period.get('max_pct', 0)}%  Max Risk",
                  font=_njb(30), fill=b[3], anchor="mm")
    else:
        text = "懸念なし  No Significant Risk"
        size = 46
        while size > 30 and draw.textbbox((0, 0), text, font=_njb(size))[2] > (bx[2] - bx[0] - 60):
            size -= 2
        draw.text((bcx, 350), text, font=_njb(size), fill=(46, 125, 50), anchor="mm")
        draw.text((bcx, 452), f"最大 {period.get('max_pct', 0)}%  Max Risk",
                  font=_njb(28), fill=(46, 125, 50), anchor="mm")

    # 3島パネル
    f_ph = _njb(24)
    f_ph_en = _en(16)
    f_row_ja = _nj(19)
    f_row_en = _en(14)
    f_barpct = _num(24)
    f_susp = _njb(18)
    f_susp_en = _en(12)
    for (px0, py0, px1, py1), isl, pcol in zip(OTH_PANELS, islands, OTH_PANEL_COL):
        draw.rounded_rectangle([(px0, py0), (px1, py1)], radius=20, fill=(248, 250, 252))
        pcx = (px0 + px1) // 2
        # 島名ヘッダピル
        draw.rounded_rectangle([(px0 + 20, py0 + 18), (px1 - 20, py0 + 66)], radius=12, fill=pcol)
        draw.text((pcx, py0 + 34), isl["name_ja"], font=f_ph, fill="white", anchor="mm")
        draw.text((pcx, py0 + 54), isl["name_en"], font=f_ph_en, fill=(230, 236, 244), anchor="mm")
        rows = isl["rows"]
        top, bottom = py0 + 108, py1 - 34
        step = (bottom - top) / (len(rows) - 1) if len(rows) > 1 else 0
        bar_x0, bar_x1 = px0 + 128, px0 + 262   # bar短縮でラベル(運休/Suspended/%)の右端はみ出し防止
        for i, r in enumerate(rows):
            y = int(top + i * step)
            draw.text((px0 + 24, y), r["date_ja"], font=f_row_ja, fill=(40, 44, 50), anchor="lm")
            draw.text((px0 + 24, y + 20), r["date_en"], font=f_row_en, fill=(120, 124, 130), anchor="lm")
            II._bar(draw, bar_x0, bar_x1, y, r.get("pct"), r.get("suspended", False),
                    f_barpct, f_susp, f_susp_en)

    # 目安バー
    gy = 1050
    draw.rounded_rectangle([(40, gy), (1214, gy + 92)], radius=18, fill=(248, 250, 252))
    draw.text((cx, gy + 26), "欠航リスクの目安  RISK LEVEL GUIDE", font=_njb(22), fill=(40, 50, 70), anchor="mm")
    _risk_guide(draw, cx, gy + 64)
    draw.text((cx, 1170), "※AI予測・参考値。欠航判断は安栄観光公式HPをご確認ください。",
              font=_nj(16), fill=(225, 235, 248), anchor="mm")
    draw.text((cx, 1196), "*AI estimates. Check Anei Kanko official for cancellations.",
              font=_en(15), fill=(190, 210, 235), anchor="mm")

    img.save(output_path)
    return output_path


# ============================================================
# 気象データ面（写真背景＋白カードのデータ表）
# ============================================================
def make_weather(bg_path, title_line_ja, title_line_en, rows, now, output_path,
                 wave_thr=2.5, swell_thr=2.0, wind_thr=12.0):
    """rows: [{name_ja, name_en, days:[{max_wave,max_swell,max_wind} x8(index0..7)]}]
    短期(明日/明後日)の波高・うねり・風速 と 3〜7日先の波高 を白カードで表示する。"""
    from datetime import timedelta
    DAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
    DAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    img = _photo_bg(bg_path)
    draw = ImageDraw.Draw(img)
    cx = W // 2

    draw.text((cx, 48), "予報根拠データ  Forecast Data", font=_njb(44), fill="white", anchor="mm")
    draw.text((cx, 92), "気象データに基づくリスク根拠  /  Weather basis for the forecast",
              font=_nj(20), fill=(206, 226, 245), anchor="mm")
    draw.text((cx, 128), f"{title_line_ja}   {title_line_en}",
              font=_nj(22), fill=(190, 214, 240), anchor="mm")

    HI = (211, 47, 47)      # 閾値超の値
    NORMAL = (40, 50, 70)   # 通常の値
    HEAD = (90, 100, 120)
    SUBHEAD = (120, 130, 150)

    def fmt(v):
        return f"{v:.1f}" if v is not None else "—"

    def val_color(v, thr):
        return HI if (v is not None and v >= thr) else NORMAL

    n = len(rows)
    tmr, dayafter = now + timedelta(days=1), now + timedelta(days=2)

    # ── セクションA: 明日/明後日 × 波高/うねり/風速（白カード）──
    ca = (40, 168, 1214, 706)
    draw.rounded_rectangle(ca, radius=22, fill=(250, 251, 252))
    name_x = 70
    gA_x, gB_x = 300, 764          # 各日グループ左端
    cw = 150                       # 各指標列幅
    f_dhead = _njb(23)
    f_dhead_en = _en(15)
    f_col = _nj(17)
    f_col_en = _en(13)
    f_name = _njb(24)
    f_name_en = _en(15)
    f_val = _num(26)

    # 日グループ見出し
    for gx, dt, ja in [(gA_x, tmr, "明日"), (gB_x, dayafter, "明後日")]:
        gcx = gx + int(1.5 * cw)
        draw.text((gcx, ca[1] + 34), f"{ja}  {dt.month}/{dt.day}（{DAY_JA[dt.weekday()]}）",
                  font=f_dhead, fill=(30, 45, 70), anchor="mm")
        draw.text((gcx, ca[1] + 58), f"{MON_EN[dt.month-1]} {dt.day} ({DAY_EN[dt.weekday()]})",
                  font=f_dhead_en, fill=SUBHEAD, anchor="mm")
    # 列見出し
    col_hy = ca[1] + 96
    draw.text((name_x, col_hy), "航路 / 島", font=f_col, fill=HEAD, anchor="lm")
    for gx in (gA_x, gB_x):
        for ci, (ja, en) in enumerate([("波高", "Wave"), ("うねり", "Swell"), ("風速", "Wind")]):
            ccx = gx + ci * cw + cw // 2
            draw.text((ccx, col_hy - 8), ja, font=f_col, fill=HEAD, anchor="mm")
            draw.text((ccx, col_hy + 12), en, font=f_col_en, fill=SUBHEAD, anchor="mm")
    draw.line([(ca[0] + 24, col_hy + 28), (ca[2] - 24, col_hy + 28)], fill=(226, 230, 236), width=2)
    # データ行
    rtop = col_hy + 46
    rh = (ca[3] - 26 - rtop) // max(n, 1)
    for i, r in enumerate(rows):
        ry = rtop + i * rh
        rcy = ry + rh // 2
        if i % 2 == 1:
            draw.rounded_rectangle([(ca[0] + 16, ry), (ca[2] - 16, ry + rh)], radius=12, fill=(241, 245, 249))
        draw.text((name_x, rcy - 12), r["name_ja"], font=f_name, fill=(30, 40, 60), anchor="lm")
        draw.text((name_x, rcy + 16), r["name_en"], font=f_name_en, fill=SUBHEAD, anchor="lm")
        for gx, delta in [(gA_x, 1), (gB_x, 2)]:
            d = r["days"][delta] if delta < len(r["days"]) else {}
            for ci, (key, thr) in enumerate([("max_wave", wave_thr), ("max_swell", swell_thr), ("max_wind", wind_thr)]):
                v = d.get(key)
                draw.text((gx + ci * cw + cw // 2, rcy), fmt(v), font=f_val,
                          fill=val_color(v, thr), anchor="mm")

    # ── セクションB: 3〜7日先の波高（白カード）──
    cb = (40, 724, 1214, 1096)
    draw.rounded_rectangle(cb, radius=22, fill=(250, 251, 252))
    draw.text(((cb[0] + cb[2]) // 2, cb[1] + 34), "3〜7日先の波高  Wave Height Outlook (3-7 days ahead, m)",
              font=_njb(21), fill=(30, 45, 70), anchor="mm")
    days5 = [now + timedelta(days=d) for d in range(3, 8)]
    bname_x = 70
    bcol_x0 = 300
    bcw = (cb[2] - 40 - bcol_x0) // 5
    bhy = cb[1] + 74
    for ci, dt in enumerate(days5):
        bcx = bcol_x0 + ci * bcw + bcw // 2
        draw.text((bcx, bhy - 8), f"{dt.month}/{dt.day}（{DAY_JA[dt.weekday()]}）", font=_nj(16), fill=HEAD, anchor="mm")
        draw.text((bcx, bhy + 12), f"{MON_EN[dt.month-1]} {dt.day}", font=_en(12), fill=SUBHEAD, anchor="mm")
    draw.line([(cb[0] + 24, bhy + 28), (cb[2] - 24, bhy + 28)], fill=(226, 230, 236), width=2)
    brtop = bhy + 44
    brh = (cb[3] - 24 - brtop) // max(n, 1)
    for i, r in enumerate(rows):
        ry = brtop + i * brh
        rcy = ry + brh // 2
        if i % 2 == 1:
            draw.rounded_rectangle([(cb[0] + 16, ry), (cb[2] - 16, ry + brh)], radius=12, fill=(241, 245, 249))
        draw.text((bname_x, rcy), r["name_ja"], font=_njb(20), fill=(30, 40, 60), anchor="lm")
        for ci, delta in enumerate(range(3, 8)):
            d = r["days"][delta] if delta < len(r["days"]) else {}
            v = d.get("max_wave")
            draw.text((bcol_x0 + ci * bcw + bcw // 2, rcy), fmt(v), font=_num(24),
                      fill=val_color(v, wave_thr), anchor="mm")

    # 情報源・免責
    draw.text((cx, 1128), "【情報源】Open-Meteo Marine API  /  安栄観光 aneikankou.co.jp",
              font=_nj(16), fill=(225, 235, 248), anchor="mm")
    draw.text((cx, 1158), "※欠航判断は安栄観光が行います。本データはAI予測の参考値です。",
              font=_nj(15), fill=(210, 226, 245), anchor="mm")
    draw.text((cx, 1184), f"生成: {now.strftime('%Y-%m-%d %H:%M')} JST",
              font=_nj(13), fill=(180, 202, 228), anchor="mm")

    img.save(output_path)
    return output_path
