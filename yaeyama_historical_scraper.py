"""
yaeyama_historical_scraper.py
一回限りの実行スクリプト。

安栄観光 POST /condition/past から 2016-04-01〜昨日の全運航実績を取得し、
Open-Meteo アーカイブから対応する気象データとマージして
Google Sheets に書き込む。

学習データ規模: 約3,700日 × 7航路 ≒ 26,000件
"""

import os
import re
import json
import time
import requests
from datetime import date, timedelta
from bs4 import BeautifulSoup

# ============================================================
# 定数
# ============================================================

START_DATE    = date(2016, 4, 1)
ANEI_PAST_URL = "https://aneikankou.co.jp/condition/past"
BATCH_SIZE    = 700    # 100日 × 7航路 = 700行単位でSheets書き込み
REQUEST_SLEEP = 1.0    # Aneikanサーバーへの配慮（秒）

ROUTE_CONFIGS = {
    "route1": {"name": "大原（西表島東）", "lat": 24.28,       "lon": 124.13},
    "route2": {"name": "小浜島",           "lat": 24.37,       "lon": 124.15},
    "route3": {"name": "竹富島",           "lat": 24.36,       "lon": 124.10},
    "route4": {"name": "黒島",             "lat": 24.22,       "lon": 124.14},
    "route5": {"name": "上原（西表島北）", "lat": 24.40,       "lon": 123.86},
    "route6": {"name": "波照間島",         "lat": 24.165974,   "lon": 123.836266},
    "route7": {"name": "鳩間島",           "lat": 24.47,       "lon": 123.80},
}

SHEET_NAME = "yaeyama_operation_log"

SHEET_HEADERS = [
    "date", "recorded_at", "route_id", "route_name",
    "hs_bin1_operated", "hs_bin2_operated", "hs_bin3_operated",
    "hs_bin4_operated", "hs_bin5_operated", "hs_bin6_operated",
    "hs_bins_count", "hs_bins_text",
    "ferry_operated",
    "hs_cancel_reason", "ferry_cancel_reason",
    "caption_text", "update_time",
    "wave_max", "swell_max", "wind_max",
    "tmr_wave_max", "tmr_swell_max", "tmr_wind_max",
    "dayafter_wave_max",
    "hs_weather_cancel", "ferry_weather_cancel",
]


# ============================================================
# HTML解析（yaeyama_logger.py と同一ロジック）
# ============================================================

def _status_from_span(span):
    if span is None:
        return "―"
    classes = span.get("class", [])
    if "condition_item_circle"   in classes: return "◯"
    if "condition_item_triangle" in classes: return "△"
    if "conditon_item_times"     in classes: return "✕"   # サイト側タイポそのまま
    return span.get_text(strip=True)


def _cancel_reason(text):
    if any(w in text for w in ["機器", "エンジン", "トラブル", "故障", "点検", "整備"]):
        return "equipment"
    if "ドック" in text or "dock" in text.lower():
        return "dock"
    return "weather"


def _split_caption(caption_text):
    """caption_text をHS部分・フェリー部分に分割する"""
    ferry_markers = ["貨客船", "フェリーはてるま"]
    split_pos = len(caption_text)
    for marker in ferry_markers:
        pos = caption_text.find(marker)
        if 0 <= pos < split_pos:
            split_pos = pos
    return caption_text[:split_pos].strip(), caption_text[split_pos:].strip()


def _bin_operated(bins, idx):
    if idx >= len(bins):
        return None
    return 1 if bins[idx]["status"] == "◯" else 0


def _parse_route(soup, route_id, caution_text):
    r = {
        "hs_bins":             [],
        "ferry_operated":      None,
        "hs_cancel_reason":    "none",
        "ferry_cancel_reason": "none",
        "caption_text":        "",
    }

    route_title = soup.find("div", id=route_id)
    if not route_title:
        return r

    condition_item = route_title.parent

    caption_div  = condition_item.find("div", class_="conditon_item_caption")
    caption_text = caption_div.get_text(separator=" ", strip=True) if caption_div else ""
    r["caption_text"] = caption_text

    hs_caption, ferry_caption = _split_caption(caption_text)

    port_details = condition_item.find_all("div", class_="condition_item_port_detail")
    hs_bins = []
    if port_details:
        for row in port_details[0].find_all("div", class_="flexbox"):
            time_div   = row.find("div", class_="condition_item_port_detail_time")
            status_div = row.find("div", class_="condition_item_port_detail_status")
            if not time_div or not status_div:
                continue
            time_text = time_div.get_text(strip=True)
            if time_text == "―":
                continue
            status = _status_from_span(status_div.find("span"))
            hs_bins.append({"time": time_text, "status": status})
    r["hs_bins"] = hs_bins

    if any(b["status"] == "✕" for b in hs_bins):
        r["hs_cancel_reason"] = _cancel_reason(hs_caption or caption_text)
    elif any(b["status"] == "△" for b in hs_bins):
        if "通常運航" in hs_caption:
            r["hs_cancel_reason"] = "none"
        else:
            r["hs_cancel_reason"] = _cancel_reason(hs_caption or caption_text)
    else:
        r["hs_cancel_reason"] = "none"

    if route_id == "route6":
        ferry_keywords        = ["フェリーはてるま", "貨客"]
        ferry_cancel_keywords = ["運休", "欠航", "ドック"]
        if any(kw in caption_text for kw in ferry_keywords):
            if any(kw in ferry_caption for kw in ferry_cancel_keywords):
                r["ferry_operated"]      = 0
                r["ferry_cancel_reason"] = _cancel_reason(ferry_caption)
            else:
                r["ferry_operated"]      = 1
                r["ferry_cancel_reason"] = "none"
        else:
            if "フェリーはてるま" in caution_text:
                if any(kw in caution_text for kw in ferry_cancel_keywords):
                    r["ferry_operated"]      = 0
                    r["ferry_cancel_reason"] = _cancel_reason(caution_text)
                else:
                    r["ferry_operated"]      = 1
            else:
                r["ferry_operated"] = None
    else:
        r["ferry_operated"]      = None
        r["ferry_cancel_reason"] = "none"

    return r


# ============================================================
# 気象データ取得（年単位・一括キャッシュ）
# ============================================================

def _fetch_weather_year(lat, lon, year):
    """
    指定座標・年の気象データを取得し、
    {date_str: {"wave": float, "swell": float, "wind": float}} で返す。
    marine API (wave/swell) と archive API (wind) を両方取得する。
    """
    start = f"{year}-01-01"
    end   = f"{year}-12-31"
    by_date = {}

    try:
        marine = requests.get(
            f"https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={start}&end_date={end}"
            f"&hourly=wave_height,swell_wave_height",
            timeout=30
        ).json()

        times  = marine.get("hourly", {}).get("time", [])
        waves  = marine.get("hourly", {}).get("wave_height", [])
        swells = marine.get("hourly", {}).get("swell_wave_height", [])
        for t, w, s in zip(times, waves, swells):
            d = t[:10]
            if d not in by_date:
                by_date[d] = {"waves": [], "swells": [], "winds": []}
            if w is not None: by_date[d]["waves"].append(w)
            if s is not None: by_date[d]["swells"].append(s)

    except Exception as e:
        print(f"    [警告] marine API ({lat},{lon},{year}): {e}")

    try:
        weather = requests.get(
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={start}&end_date={end}"
            f"&hourly=wind_speed_10m&wind_speed_unit=ms",
            timeout=30
        ).json()

        w_times = weather.get("hourly", {}).get("time", [])
        winds   = weather.get("hourly", {}).get("wind_speed_10m", [])
        for t, w in zip(w_times, winds):
            d = t[:10]
            if d not in by_date:
                by_date[d] = {"waves": [], "swells": [], "winds": []}
            if w is not None:
                by_date[d]["winds"].append(w)

    except Exception as e:
        print(f"    [警告] archive API ({lat},{lon},{year}): {e}")

    result = {}
    for d, vals in by_date.items():
        result[d] = {
            "wave":  round(max(vals["waves"]),  2) if vals["waves"]  else None,
            "swell": round(max(vals["swells"]), 2) if vals["swells"] else None,
            "wind":  round(max(vals["winds"]),  2) if vals["winds"]  else None,
        }
    return result


def build_weather_db():
    """
    全航路・全年の気象データを一括取得してメモリに保持する。
    7ルート × 11年 × 2API = 約154リクエスト（数分）
    """
    print("気象データ一括取得開始...")
    weather_db = {}  # route_id -> {date_str: {wave, swell, wind}}
    today = date.today()

    for route_id, cfg in ROUTE_CONFIGS.items():
        weather_db[route_id] = {}
        for year in range(START_DATE.year, today.year + 1):
            yr_data = _fetch_weather_year(cfg["lat"], cfg["lon"], year)
            weather_db[route_id].update(yr_data)
            time.sleep(0.3)
        total = len(weather_db[route_id])
        print(f"  {route_id} ({cfg['name']}): {total} 日分")

    total_entries = sum(len(v) for v in weather_db.values())
    print(f"気象DB構築完了: {total_entries:,} エントリ")
    return weather_db


def _get_w(weather_db, route_id, d):
    """気象DBから日付dのデータを取得（なければ空dict）"""
    return weather_db.get(route_id, {}).get(d.strftime("%Y-%m-%d"), {})


# ============================================================
# 過去運航データ スクレイピング
# ============================================================

def scrape_past_date(target_date):
    """POST /condition/past で指定日の運航状況HTMLを取得してパース"""
    resp = requests.post(
        ANEI_PAST_URL,
        data={"date": target_date.strftime("%Y-%m-%d")},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    caution_div  = soup.find("div", class_="condition_item_caution")
    caution_text = caution_div.get_text(separator=" ", strip=True) if caution_div else ""

    update_div  = soup.find("div", class_="condition_item_update")
    update_time = ""
    if update_div:
        m = re.search(r"(\d{2}:\d{2})", update_div.get_text())
        update_time = m.group(1) if m else ""

    routes = {rid: _parse_route(soup, rid, caution_text) for rid in ROUTE_CONFIGS}
    return {"update_time": update_time, "routes": routes}


# ============================================================
# Google Sheets 接続
# ============================================================

def connect_sheets():
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID_YAEYAMA")
    svc_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheets_id or not svc_json:
        raise RuntimeError("環境変数 GOOGLE_SHEETS_ID_YAEYAMA / GOOGLE_SERVICE_ACCOUNT_JSON が未設定")

    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        json.loads(svc_json),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheets_id)

    try:
        ws = sh.worksheet(SHEET_NAME)
    except Exception:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=50000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
        print(f"新規シート作成: {SHEET_NAME}")

    return ws


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 60)
    print("Yaeyama Historical Scraper")
    print(f"対象期間: {START_DATE} 〜 昨日")
    print("=" * 60)

    # Sheets 接続
    ws = connect_sheets()

    # 既存データ（スキップ判定用）
    print("\n既存データ確認中...")
    existing = set()
    try:
        col_date  = ws.col_values(1)
        col_route = ws.col_values(3)
        for d, r in zip(col_date, col_route):
            if r == "route1" and d and d not in ("date", ""):
                existing.add(d)
    except Exception as e:
        print(f"  [警告] 既存データ確認エラー（続行）: {e}")
    print(f"  記録済み: {len(existing)} 日分")

    # 気象DB構築（年単位・全航路・一括）
    print()
    weather_db = build_weather_db()

    # スクレイピングループ
    today  = date.today()
    target = START_DATE
    batch  = []
    written = skipped = errors = 0

    print(f"\nスクレイピング開始...")

    while target < today:
        date_str = target.strftime("%Y-%m-%d")

        if date_str in existing:
            target  += timedelta(days=1)
            skipped += 1
            continue

        try:
            data = scrape_past_date(target)
        except Exception as e:
            print(f"  [エラー] {date_str}: {e}")
            target += timedelta(days=1)
            errors += 1
            time.sleep(3)
            continue

        for route_id, cfg in ROUTE_CONFIGS.items():
            op   = data["routes"][route_id]
            w    = _get_w(weather_db, route_id, target)
            tmr  = _get_w(weather_db, route_id, target + timedelta(days=1))
            day2 = _get_w(weather_db, route_id, target + timedelta(days=2))

            bins = op["hs_bins"]
            b    = [_bin_operated(bins, i) for i in range(6)]

            has_cancel  = any(x == 0 for x in b)
            hs_w_cancel = 1 if has_cancel and op["hs_cancel_reason"] == "weather" else 0
            fw_cancel   = 1 if op["ferry_operated"] == 0 and op["ferry_cancel_reason"] == "weather" else 0

            batch.append([
                date_str,
                f"{date_str} 08:15",    # 過去分は記録時刻が不明のため 08:15 を使用
                route_id,
                cfg["name"],
                b[0], b[1], b[2], b[3], b[4], b[5],
                len(bins),
                json.dumps(bins, ensure_ascii=False),
                op["ferry_operated"],
                op["hs_cancel_reason"],
                op["ferry_cancel_reason"],
                op["caption_text"][:300],
                data["update_time"],
                w.get("wave"),   w.get("swell"),   w.get("wind"),
                tmr.get("wave"), tmr.get("swell"),  tmr.get("wind"),
                day2.get("wave"),
                hs_w_cancel,
                fw_cancel,
            ])

        # バッチ書き込み
        if len(batch) >= BATCH_SIZE:
            ws.append_rows(batch, value_input_option="USER_ENTERED")
            written += len(batch)
            print(f"  [{date_str}] 書込+{len(batch)}行 累計{written:,}行 "
                  f"スキップ{skipped} エラー{errors}")
            batch = []
            time.sleep(3)    # Sheets API rate limit

        target += timedelta(days=1)
        time.sleep(REQUEST_SLEEP)

    # 残りを書き込み
    if batch:
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        written += len(batch)
        print(f"  [最終] 書込+{len(batch)}行 累計{written:,}行")

    print(f"\n{'=' * 60}")
    print(f"完了。書込: {written:,}行 / スキップ: {skipped:,}日 / エラー: {errors:,}日")
    print("=" * 60)


if __name__ == "__main__":
    main()
