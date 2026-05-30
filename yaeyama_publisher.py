"""
yaeyama_publisher.py
八重山全航路（route1/3/5/6/7）の欠航リスク予報をInstagramに投稿する。
yaeyama_logger.py の log_daily_records() から呼び出す。

投稿: 3枚カルーセル
  1枚目: 短期予報（明日・明後日 × 5航路）
  2枚目: 長期予報（3〜7日先）
  3枚目: 予報根拠データ（気象数値）

フォント・カラー・投稿フローは ferry-forecast（02_ferry-forecast/forecast_publisher.py）と統一。
"""

import os
import json
import math
import time
import base64
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

JST = ZoneInfo("Asia/Tokyo")

# ============================================================
# 設定
# ============================================================

MODEL_ROUTES = ["route1", "route3", "route5", "route6", "route7"]

ROUTE_INFO = {
    "route1": {"name": "大原（西表島東）", "short": "大原",  "lat": 24.28,      "lon": 124.13},
    "route3": {"name": "竹富島",           "short": "竹富島", "lat": 24.36,     "lon": 124.10},
    "route5": {"name": "上原（西表島北）", "short": "上原",  "lat": 24.40,      "lon": 123.86},
    "route6": {"name": "波照間島",         "short": "波照間", "lat": 24.165974, "lon": 123.836266},
    "route7": {"name": "鳩間島",           "short": "鳩間島", "lat": 24.47,     "lon": 123.80},
}

IMG_SIZE = (1080, 1080)


# ============================================================
# フォント
# ============================================================

def _find_noto_font(weights):
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
            for ext in [".ttc", ".otf", ".ttf"]:
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


FONT_REGULAR = _find_noto_font(["Regular"])
FONT_BOLD    = _find_noto_font(["Black", "Bold"])
FONT_MEDIUM  = _find_noto_font(["Medium", "Regular"])


def _load_font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ============================================================
# カラー
# ============================================================

def _get_bg_color(pct):
    """欠航リスク% → 背景色（hex）"""
    if pct is None or pct <= 30:
        return "#2E7D32"   # 深緑
    elif pct <= 60:
        return "#F9A825"   # 琥珀
    elif pct <= 80:
        return "#E65100"   # オレンジ
    else:
        return "#B71C1C"   # 深赤


def _get_risk_text_color(pct):
    """欠航リスク% → テキスト/バッジ色（hex）"""
    if pct is None or pct <= 30:
        return "#66FF80"   # 緑
    elif pct <= 60:
        return "#FFD54F"   # 琥珀
    elif pct <= 80:
        return "#FF8A50"   # オレンジ
    else:
        return "#FF6666"   # 赤


def _hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ============================================================
# モデル推論（sklearn不要）
# ============================================================

_MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yaeyama_cancel_model.json")


def _load_model():
    try:
        with open(_MODEL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [警告] モデル読み込み失敗: {e}")
        return None


def _predict_prob(model_params, wave, swell, wind, swell_period=None):
    if not model_params or wave is None or wind is None:
        return None
    mtype = model_params.get("model_type", "logistic")
    if mtype == "rule":
        p = model_params
        score = 0.0
        if wave >= p["wave_thr_high"]:
            score = p["prob_wave_high"]
        elif wave >= p["wave_thr_mid"]:
            score = p["prob_wave_mid"]
        elif wave >= p["wave_thr_mid"] * 0.8:
            score = p["prob_wave_mid"] * 0.5
        if wind >= p["wind_thr"]:
            score = min(score + p["prob_wind_add"], 0.95)
        return round(score, 3)
    else:  # logistic
        m = model_params
        feat_vals = {
            "wave_max": wave, "swell_max": swell,
            "wind_max": wind, "swell_period_max": swell_period,
        }
        try:
            vals = [feat_vals[f] for f in m["features"]]
            if any(v is None for v in vals):
                return None
            x_s = [(v - mu) / sc
                   for v, mu, sc in zip(vals, m["scaler_mean"], m["scaler_scale"])]
            z = m["intercept"] + sum(c * x for c, x in zip(m["coef"], x_s))
            return round(1.0 / (1.0 + math.exp(-z)), 3)
        except Exception:
            return None


# ============================================================
# 7日間予報取得
# ============================================================

_forecast_cache = {}   # (lat, lon) -> list of 7 day-dicts


def _fetch_7day(lat, lon):
    """
    指定座標の 7 日分（今日=day0〜day6）の
    max_wave / max_swell / max_wind を返す。
    [{date, max_wave, max_swell, max_wind}, ...]
    """
    key = (round(lat, 6), round(lon, 6))
    if key in _forecast_cache:
        return _forecast_cache[key]

    days = []
    try:
        marine_url = (
            f"https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wave_height,swell_wave_height"
            f"&timezone=Asia%2FTokyo&forecast_days=7"
        )
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wind_speed_10m"
            f"&wind_speed_unit=ms"
            f"&timezone=Asia%2FTokyo&forecast_days=7"
        )
        marine  = requests.get(marine_url,  timeout=15).json()
        weather = requests.get(weather_url, timeout=15).json()

        now = datetime.now(JST)
        for delta in range(7):
            target = (now + timedelta(days=delta)).strftime("%Y-%m-%d")

            def daily_max(data, key_name):
                times  = data.get("hourly", {}).get("time", [])
                values = data.get("hourly", {}).get(key_name, [])
                vals   = [v for t, v in zip(times, values)
                          if t.startswith(target) and v is not None]
                return round(max(vals), 2) if vals else None

            days.append({
                "date":      target,
                "max_wave":  daily_max(marine,  "wave_height"),
                "max_swell": daily_max(marine,  "swell_wave_height"),
                "max_wind":  daily_max(weather, "wind_speed_10m"),
            })

    except Exception as e:
        print(f"  [警告] 7日間予報取得エラー ({lat},{lon}): {e}")
        now = datetime.now(JST)
        for delta in range(7):
            days.append({
                "date":     (now + timedelta(days=delta)).strftime("%Y-%m-%d"),
                "max_wave": None, "max_swell": None, "max_wind": None,
            })

    _forecast_cache[key] = days
    return days


# ============================================================
# 予報データ構築
# ============================================================

def _build_forecast_data(models):
    """
    5航路 × 7日分の欠航確率を計算する。
    戻り値: {route_id: [prob_day0, prob_day1, ..., prob_day6]}
    """
    result = {}
    for rid in MODEL_ROUTES:
        info = ROUTE_INFO[rid]
        m_hs = (models or {}).get(rid, {}).get("hs")
        days = _fetch_7day(info["lat"], info["lon"])
        probs = []
        for d in days:
            p = _predict_prob(m_hs, d["max_wave"], d["max_swell"], d["max_wind"])
            probs.append(p)
        result[rid] = probs
        print(f"  [{rid}] 明日:{_pct(probs[1])}%  明後日:{_pct(probs[2])}%")
    return result


def _pct(prob):
    """確率 0〜1 → 整数 %。None → None"""
    if prob is None:
        return None
    return int(round(prob * 100))


def _max_pct(probs_by_route, day_indices):
    """複数航路・複数日の最大リスク%（Noneは無視）"""
    vals = []
    for rid in MODEL_ROUTES:
        for i in day_indices:
            p = probs_by_route.get(rid, [None] * 7)[i]
            if p is not None:
                vals.append(_pct(p))
    return max(vals) if vals else 0


# ============================================================
# 画像生成
# ============================================================

def _fonts():
    return {
        "title":    _load_font(FONT_BOLD,    42),
        "title_en": _load_font(FONT_MEDIUM,  24),
        "head":     _load_font(FONT_BOLD,    30),
        "head_en":  _load_font(FONT_REGULAR, 20),
        "route":    _load_font(FONT_MEDIUM,  26),
        "pct_big":  _load_font(FONT_BOLD,    72),
        "pct_med":  _load_font(FONT_BOLD,    56),
        "pct_sm":   _load_font(FONT_BOLD,    40),
        "label":    _load_font(FONT_REGULAR, 20),
        "bar":      _load_font(FONT_REGULAR, 22),
        "xs":       _load_font(FONT_REGULAR, 17),
        "sec":      _load_font(FONT_BOLD,    22),
        "val":      _load_font(FONT_MEDIUM,  20),
    }


def make_image_short(probs_by_route, output_path):
    """
    画像①: 短期予報
    5航路 × 2日（明日/明後日）の欠航リスク%を表示。
    """
    now = datetime.now(JST)

    # 背景色: 明日・明後日の全航路の最大リスク
    max_risk = _max_pct(probs_by_route, [1, 2])
    img  = Image.new("RGB", IMG_SIZE, color=_hex_to_rgb(_get_bg_color(max_risk)))
    draw = ImageDraw.Draw(img)
    f    = _fonts()

    # ── タイトル ──────────────────────────────────────────
    draw.text((540, 44),  "八重山航路 欠航リスク予報",
              font=f["title"], fill="white", anchor="mm")
    draw.text((540, 88),  "Yaeyama Routes  /  Cancellation Risk Forecast",
              font=f["title_en"], fill=(255,255,255,200), anchor="mm")
    draw.line([(60, 110), (1020, 110)], fill=(255,255,255,80), width=1)

    # ── 日付ヘッダー ─────────────────────────────────────
    tmr      = now + timedelta(days=1)
    dayafter = now + timedelta(days=2)
    DAY_JA   = ["（月）","（火）","（水）","（木）","（金）","（土）","（日）"]
    tmr_label      = f"{tmr.month}/{tmr.day}{DAY_JA[tmr.weekday()]}"
    dayafter_label  = f"{dayafter.month}/{dayafter.day}{DAY_JA[dayafter.weekday()]}"

    COL_NAME  = 190    # route name column center
    COL_TMR   = 540    # tomorrow column center
    COL_DAY2  = 875    # day-after column center
    HDR_Y     = 140

    draw.text((COL_TMR,  HDR_Y),      "明日",       font=f["head"],    fill="white", anchor="mm")
    draw.text((COL_TMR,  HDR_Y + 34), tmr_label,    font=f["head_en"], fill=(255,255,255,200), anchor="mm")
    draw.text((COL_DAY2, HDR_Y),      "明後日",     font=f["head"],    fill="white", anchor="mm")
    draw.text((COL_DAY2, HDR_Y + 34), dayafter_label, font=f["head_en"], fill=(255,255,255,200), anchor="mm")

    # 縦区切り線
    draw.line([(360, 118), (360, 960)], fill=(255,255,255,50), width=1)
    draw.line([(710, 118), (710, 960)], fill=(255,255,255,50), width=1)

    # ── 航路行 ──────────────────────────────────────────
    ROW_TOP  = 200
    ROW_H    = 148   # 5 rows × 148 = 740, ends at 940
    HALF_H   = ROW_H // 2

    for idx, rid in enumerate(MODEL_ROUTES):
        info  = ROUTE_INFO[rid]
        probs = probs_by_route.get(rid, [None] * 7)
        pct1  = _pct(probs[1])   # tomorrow
        pct2  = _pct(probs[2])   # day after

        row_y = ROW_TOP + idx * ROW_H
        cy    = row_y + HALF_H

        # 薄い行区切り
        draw.line([(60, row_y), (1020, row_y)], fill=(255,255,255,35), width=1)

        # 航路名
        draw.text((COL_NAME, cy - 12), info["short"],
                  font=f["route"], fill="white", anchor="mm")

        # 明日 %
        if pct1 is not None:
            col1 = _get_risk_text_color(pct1)
            draw.text((COL_TMR, cy - 12), f"{pct1}%",
                      font=f["pct_med"], fill=col1, anchor="mm")
        else:
            draw.text((COL_TMR, cy - 12), "—",
                      font=f["pct_sm"], fill=(200,200,200), anchor="mm")

        # 明後日 %
        if pct2 is not None:
            col2 = _get_risk_text_color(pct2)
            draw.text((COL_DAY2, cy - 12), f"{pct2}%",
                      font=f["pct_med"], fill=col2, anchor="mm")
        else:
            draw.text((COL_DAY2, cy - 12), "—",
                      font=f["pct_sm"], fill=(200,200,200), anchor="mm")

    draw.line([(60, ROW_TOP + 5 * ROW_H), (1020, ROW_TOP + 5 * ROW_H)],
              fill=(255,255,255,35), width=1)

    # ── フッター ─────────────────────────────────────────
    FOOTER_Y = ROW_TOP + 5 * ROW_H + 20   # ≈ 960
    draw.text((540, FOOTER_Y + 18), "※AI予測・参考値。欠航判断は安栄観光公式をご確認ください。",
              font=f["xs"], fill=(255,255,255,140), anchor="mm")
    draw.text((540, FOOTER_Y + 38), "*AI estimates for reference. Check Anei Kanko official for cancellations.",
              font=f["xs"], fill=(255,255,255,110), anchor="mm")

    img.save(output_path)
    print(f"  画像①保存: {output_path}")


def make_image_longterm(probs_by_route, output_path):
    """
    画像②: 長期予報（3〜7日先）
    各日の最大欠航リスク（全5航路の max）を横棒グラフで表示。
    """
    now = datetime.now(JST)

    # 日付ラベル生成（day3〜day7）
    DAY_JA = ["月","火","水","木","金","土","日"]
    lt_days = []
    for delta in range(3, 8):
        d   = now + timedelta(days=delta)
        pct = _max_pct(probs_by_route, [delta]) if delta < 7 else None
        lt_days.append({
            "date":  d.strftime("%Y-%m-%d"),
            "label": f"{d.month}/{d.day}({DAY_JA[d.weekday()]})",
            "pct":   pct,
        })

    max_risk = max((d["pct"] for d in lt_days if d["pct"] is not None), default=0)

    img  = Image.new("RGB", IMG_SIZE, color=_hex_to_rgb(_get_bg_color(max_risk)))
    draw = ImageDraw.Draw(img)
    f    = _fonts()

    # タイトル
    draw.text((540, 44),  "長期予報（3〜7日先）",
              font=f["title"], fill="white", anchor="mm")
    draw.text((540, 88),  "Yaeyama Routes  /  Long-term Risk Forecast  (3-7 days ahead)",
              font=f["title_en"], fill=(255,255,255,200), anchor="mm")
    draw.line([(60, 112), (1020, 112)], fill=(255,255,255,80), width=1)

    # リスク概要
    has_risk = max_risk >= 30
    if has_risk:
        risk_days = [d["label"] for d in lt_days if d["pct"] is not None and d["pct"] >= 30]
        period_str = "  ".join(risk_days) if risk_days else "—"
        draw.text((540, 162), "注意が必要な期間  /  Risk Period",
                  font=f["head"], fill=(255,255,255,200), anchor="mm")
        draw.text((540, 238), period_str,
                  font=f["head"],    fill="white", anchor="mm")
        draw.text((540, 296), f"最大欠航リスク  Max Risk:  {max_risk}%",
                  font=f["head_en"], fill=(255,255,255,190), anchor="mm")
    else:
        draw.text((540, 230), "懸念なし  /  No Significant Risk",
                  font=f["head"], fill="white", anchor="mm")

    draw.line([(60, 324), (1020, 324)], fill=(255,255,255,60), width=1)

    # 横棒グラフ（5日分）
    draw.text((540, 350), "各日の最大欠航リスク  /  Max Daily Risk (All Routes)",
              font=f["head_en"], fill=(255,255,255,180), anchor="mm")

    BAR_TOP   = 390
    BAR_H     = 42
    ROW_SP    = 100
    BAR_LEFT  = 200
    BAR_MAX_W = 680   # px = 100%
    PCT_X     = BAR_LEFT + BAR_MAX_W + 20

    for i, d in enumerate(lt_days):
        y   = BAR_TOP + i * ROW_SP
        pct = d["pct"] if d["pct"] is not None else 0

        # 日付ラベル
        draw.text((BAR_LEFT - 10, y + BAR_H // 2), d["label"],
                  font=f["bar"], fill="white", anchor="rm")

        # 背景バー（薄い）
        draw.rectangle([(BAR_LEFT, y), (BAR_LEFT + BAR_MAX_W, y + BAR_H)],
                       fill=(0, 0, 0, 55))

        # 実バー
        bar_w = int(BAR_MAX_W * pct / 100)
        if bar_w > 0:
            bar_color = _hex_to_rgb(_get_bg_color(pct))
            # 少し明るくする
            bar_color = tuple(min(255, int(c * 1.35)) for c in bar_color)
            draw.rectangle([(BAR_LEFT, y), (BAR_LEFT + bar_w, y + BAR_H)],
                           fill=bar_color)

        # %ラベル
        pct_str = f"{pct}%" if d["pct"] is not None else "—"
        draw.text((PCT_X, y + BAR_H // 2), pct_str,
                  font=f["bar"], fill=_get_risk_text_color(pct), anchor="lm")

    # 注記
    draw.line([(60, 920), (1020, 920)], fill=(255,255,255,50), width=1)
    draw.text((540, 944), "対象航路: 大原・竹富島・上原・波照間・鳩間  /  Route1, 3, 5, 6, 7",
              font=f["xs"], fill=(255,255,255,155), anchor="mm")
    draw.text((540, 966), "※AI予測・参考値。公式確認は安栄観光HPまで。",
              font=f["xs"], fill=(255,255,255,130), anchor="mm")
    draw.text((540, 986), "*AI-based estimates. Check Anei Kanko official website for cancellations.",
              font=f["xs"], fill=(255,255,255,100), anchor="mm")

    img.save(output_path)
    print(f"  画像②保存: {output_path}")


def make_image_weatherdata(probs_by_route, output_path):
    """
    画像③: 予報根拠データ
    5航路の明日の気象数値（波高・うねり・風速）を一覧表示。
    """
    now = datetime.now(JST)
    tmr = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    img  = Image.new("RGB", IMG_SIZE, color=_hex_to_rgb("#0A1628"))
    draw = ImageDraw.Draw(img)
    f    = _fonts()

    # タイトル
    draw.text((540, 56),  "予報根拠データ  /  Forecast Data",
              font=f["title"], fill="white", anchor="mm")
    draw.text((540, 96),  f"明日 {(now + timedelta(days=1)).month}/{(now + timedelta(days=1)).day}  の気象予測",
              font=f["head_en"], fill=(255,255,255,180), anchor="mm")
    draw.line([(60, 116), (1020, 116)], fill="#334E7A", width=2)

    # ヘッダー行
    COL_ROUTE = 60
    COL_WAVE  = 420
    COL_SWELL = 630
    COL_WIND  = 840
    HDR_Y     = 136

    draw.rectangle([(60, HDR_Y), (1020, HDR_Y + 46)], fill="#1A3057")
    for x, label in [
        (COL_ROUTE, "航路  /  Route"),
        (COL_WAVE,  "波高  Wave (m)"),
        (COL_SWELL, "うねり Swell (m)"),
        (COL_WIND,  "風速  Wind (m/s)"),
    ]:
        draw.text((x + 4, HDR_Y + 23), label, font=f["xs"], fill="#7EB3F5", anchor="lm")

    # 各航路行
    ROW_TOP = HDR_Y + 54
    ROW_H   = 100

    def _fmt(val):
        return f"{val:.1f}" if val is not None else "—"

    for idx, rid in enumerate(MODEL_ROUTES):
        info = ROUTE_INFO[rid]
        days = _fetch_7day(info["lat"], info["lon"])
        # day1 = tomorrow
        d = days[1] if len(days) > 1 else {}
        wave  = d.get("max_wave")
        swell = d.get("max_swell")
        wind  = d.get("max_wind")

        probs = probs_by_route.get(rid, [None] * 7)
        pct1  = _pct(probs[1])

        row_y = ROW_TOP + idx * ROW_H
        cy    = row_y + ROW_H // 2

        # 交互着色
        if idx % 2 == 1:
            draw.rectangle([(60, row_y), (1020, row_y + ROW_H)], fill=(255,255,255,12))

        # 区切り線
        draw.line([(60, row_y), (1020, row_y)], fill=(255,255,255,25), width=1)

        # 航路名
        draw.text((COL_ROUTE + 4, cy - 10), info["short"],
                  font=_load_font(FONT_BOLD, 24), fill="#BBDEFB", anchor="lm")
        # リスク %（小さめ）
        if pct1 is not None:
            risk_col = _get_risk_text_color(pct1)
            draw.text((COL_ROUTE + 4, cy + 16), f"欠航リスク {pct1}%",
                      font=f["xs"], fill=risk_col, anchor="lm")

        # 数値
        for x, val, threshold, unit in [
            (COL_WAVE,  wave,  2.5, "m"),
            (COL_SWELL, swell, 2.0, "m"),
            (COL_WIND,  wind,  12,  "m/s"),
        ]:
            txt = _fmt(val)
            col = "#FF8A80" if (val is not None and val >= threshold) else "#E0E0E0"
            draw.text((x + 4, cy), txt, font=_load_font(FONT_BOLD, 28), fill=col, anchor="lm")

    # 情報源セクション
    SRC_Y = ROW_TOP + 5 * ROW_H + 20
    draw.line([(60, SRC_Y), (1020, SRC_Y)], fill="#334E7A", width=1)
    draw.rectangle([(60, SRC_Y + 8), (1020, SRC_Y + 52)], fill="#1A3057")
    draw.text((80, SRC_Y + 30), "【情報源 / Sources】  Open-Meteo Marine API  /  安栄観光 aneikankou.co.jp",
              font=f["xs"], fill="#7EB3F5", anchor="lm")

    # フッター
    draw.line([(60, 990), (1020, 990)], fill="#334E7A", width=1)
    draw.text((540, 1010), "※欠航判断は安栄観光が行います。本データはAI予測の参考値です。",
              font=f["xs"], fill="#546E7A", anchor="mm")
    draw.text((540, 1032), "*Cancellation is determined by Anei Kanko. AI estimates for reference only.",
              font=f["xs"], fill="#455A64", anchor="mm")
    draw.text((540, 1056), f"生成: {now.strftime('%Y-%m-%d %H:%M')} JST",
              font=f["xs"], fill="#37474F", anchor="mm")

    img.save(output_path)
    print(f"  画像③保存: {output_path}")


# ============================================================
# Instagram 投稿
# ============================================================

def _upload_image_to_github(image_path, filename):
    """画像を GitHub の images/ へアップロードし GitHub Pages URL を返す"""
    token = os.environ.get("GITHUB_TOKEN")
    repo  = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN / GITHUB_REPOSITORY が未設定")

    with open(image_path, "rb") as fh:
        content = base64.b64encode(fh.read()).decode()

    target_path = f"images/{filename}"
    api_url     = f"https://api.github.com/repos/{repo}/contents/{target_path}"
    headers     = {"Authorization": f"token {token}",
                   "Accept": "application/vnd.github.v3+json"}

    existing = requests.get(api_url, headers=headers)
    sha = existing.json().get("sha") if existing.status_code == 200 else None

    data = {"message": f"Auto: update {filename}", "content": content, "branch": "main"}
    if sha:
        data["sha"] = sha

    resp = requests.put(api_url, json=data, headers=headers)
    if resp.status_code in (200, 201):
        owner    = repo.split("/")[0]
        repo_name = repo.split("/")[1]
        return f"https://{owner}.github.io/{repo_name}/{target_path}"
    raise RuntimeError(f"GitHub upload failed: {resp.status_code} {resp.text[:200]}")


def _post_to_instagram(image_paths, caption):
    """Instagram にカルーセル投稿（3枚）"""
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    user_id      = os.environ.get("INSTAGRAM_USER_ID")

    if not access_token or not user_id:
        print("  [スキップ] INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID 未設定")
        return False

    try:
        # Step1: GitHub Pages へ画像アップロード
        print("  [Instagram] 画像をGitHubにアップロード中...")
        image_urls = []
        for path in image_paths:
            url = _upload_image_to_github(path, os.path.basename(path))
            image_urls.append(url)
            print(f"    {url}")

        # GitHub Pages のビルド完了待ち
        print("  [Instagram] GitHub Pages ビルド待機（90秒）...")
        time.sleep(90)

        # Step2: 各画像のカルーセルアイテムコンテナを作成
        media_ids = []
        for img_url in image_urls:
            resp = requests.post(
                f"https://graph.facebook.com/v25.0/{user_id}/media",
                params={
                    "image_url":        img_url,
                    "is_carousel_item": "true",
                    "access_token":     access_token,
                }
            )
            data = resp.json()
            if "id" not in data:
                print(f"  [エラー] メディアコンテナ作成失敗: {data}")
                return False
            media_ids.append(data["id"])
            print(f"  メディアコンテナ: {data['id']}")

        # Step3: カルーセルコンテナを作成
        resp = requests.post(
            f"https://graph.facebook.com/v25.0/{user_id}/media",
            params={
                "media_type":   "CAROUSEL",
                "children":     ",".join(media_ids),
                "caption":      caption,
                "access_token": access_token,
            }
        )
        data = resp.json()
        if "id" not in data:
            print(f"  [エラー] カルーセルコンテナ作成失敗: {data}")
            return False
        carousel_id = data["id"]
        print(f"  カルーセルコンテナ: {carousel_id}")

        print("  [Instagram] カルーセル処理待機（30秒）...")
        time.sleep(30)

        # Step4: 投稿を公開
        resp = requests.post(
            f"https://graph.facebook.com/v25.0/{user_id}/media_publish",
            params={"creation_id": carousel_id, "access_token": access_token}
        )
        data = resp.json()
        if "id" not in data:
            print(f"  [エラー] 投稿失敗: {data}")
            return False

        print(f"  ✅ Instagram投稿完了: post_id={data['id']}")
        return True

    except Exception as e:
        print(f"  [警告] Instagram投稿エラー: {e}")
        return False


# ============================================================
# キャプション生成
# ============================================================

def _build_caption(probs_by_route, now):
    """Instagram 用キャプション（日英混在）"""
    tmr = now + timedelta(days=1)
    DAY_JA = ["月","火","水","木","金","土","日"]
    tmr_label = f"{tmr.month}/{tmr.day}({DAY_JA[tmr.weekday()]})"

    lines = [
        f"🚢 八重山航路 欠航リスク予報 {tmr_label}",
        "",
    ]

    for rid in MODEL_ROUTES:
        info  = ROUTE_INFO[rid]
        probs = probs_by_route.get(rid, [None] * 7)
        pct1  = _pct(probs[1])
        if pct1 is None:
            continue
        if pct1 >= 70:
            icon = "🔴"
        elif pct1 >= 40:
            icon = "🟡"
        else:
            icon = "🟢"
        lines.append(f"{icon} {info['short']}: {pct1}%")

    lines += [
        "",
        "📊 詳細は画像スワイプでご確認ください。",
        "⚠️ AI予測・参考値。欠航判断は安栄観光公式HPをご確認ください。",
        "",
        "#八重山 #石垣島 #西表島 #波照間島 #竹富島 #欠航予報",
        "#YaeyamaIslands #OkinawaFerry #JapanTravel #IslandHopping",
    ]

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================

def run_yaeyama_publisher(route_data_list=None, cancel_models=None):
    """
    yaeyama_logger.py から呼び出すエントリーポイント。
    route_data_list: [(route_id, op, weather), ...] — 既取得データ（未使用・将来拡張用）
    cancel_models:   yaeyama_cancel_model.json の内容（None の場合は内部で読み込み）
    """
    now = datetime.now(JST)
    print(f"\n{'='*50}")
    print(f"Yaeyama Publisher: {now.strftime('%Y-%m-%d %H:%M')}")
    print("="*50)

    # モデル読み込み
    if cancel_models is None:
        cancel_models = _load_model()

    # 7日間予報 & 欠航確率計算
    print("\n[P1] 7日間予報取得・欠航確率計算中...")
    probs_by_route = _build_forecast_data(cancel_models)

    # 画像生成
    print("\n[P2] 画像生成中...")
    output_dir = "/tmp/yaeyama_images"
    os.makedirs(output_dir, exist_ok=True)
    ts = now.strftime("%Y%m%d_%H%M")

    paths = [
        f"{output_dir}/ya_img1_short_{ts}.png",
        f"{output_dir}/ya_img2_longterm_{ts}.png",
        f"{output_dir}/ya_img3_weatherdata_{ts}.png",
    ]
    make_image_short(probs_by_route,       paths[0])
    make_image_longterm(probs_by_route,    paths[1])
    make_image_weatherdata(probs_by_route, paths[2])

    # キャプション生成
    caption = _build_caption(probs_by_route, now)
    print(f"\n[P3] キャプション:\n{caption}")

    # Instagram 投稿
    print("\n[P4] Instagram 投稿中...")
    _post_to_instagram(paths, caption)

    print("\n✅ Yaeyama Publisher 完了")


if __name__ == "__main__":
    run_yaeyama_publisher()
