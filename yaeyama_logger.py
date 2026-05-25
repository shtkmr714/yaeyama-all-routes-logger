"""
yaeyama_logger.py
毎日8:15 JSTに実行。
安栄観光HPから八重山全航路（route1〜7）の運航情報を一括取得し、
Open-Meteo海洋データとともにGoogle Sheetsに記録する。

GitHub Actions から呼び出す。

便別運航状況:
  hs_bin1〜6_operated: 石垣発 各便（1=運航, 0=欠航, None=その便なし）
  最大便数: 竹富 6便 / 上原 4便 / 波照間・大原・小浜・黒島 3便 / 鳩間 2便
  ◯ → 1, △・✕ → 0
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

JST = ZoneInfo("Asia/Tokyo")

ANEI_CONDITION_URL = "https://aneikankou.co.jp/condition"

# ============================================================
# 航路定義
# ============================================================
# 天候データ取得ポイントの選定方針：
#   各航路の石垣港出港後、最も外洋に近い区間の座標を使用。
#   sheltered（内海）寄りの航路（竹富・小浜）は石垣港近海。
#   outer（外洋）寄りの航路（波照間・上原・鳩間）は各ルートの外洋部。
ROUTE_CONFIGS = {
    "route1": {
        "name":    "大原（西表島東）",
        "lat":     24.28,   # 石垣〜大原 航路中間（石西礁湖東縁）
        "lon":     124.13,
    },
    "route2": {
        "name":    "小浜島",
        "lat":     24.37,   # 石垣〜小浜 比較的近距離
        "lon":     124.15,
    },
    "route3": {
        "name":    "竹富島",
        "lat":     24.36,   # 石垣〜竹富 最短・ほぼ内海
        "lon":     124.10,
    },
    "route4": {
        "name":    "黒島",
        "lat":     24.22,   # 石垣〜黒島 南方向・やや外洋
        "lon":     124.14,
    },
    "route5": {
        "name":    "上原（西表島北）",
        "lat":     24.40,   # 石垣〜上原 北西方向・外洋区間あり
        "lon":     123.86,
    },
    "route6": {
        "name":    "波照間島",
        "lat":     24.165974,  # ユーザー指定：航路上・外洋最近接点
        "lon":     123.836266,
    },
    "route7": {
        "name":    "鳩間島",
        "lat":     24.47,   # 上原より北西・最も遠い外洋寄り
        "lon":     123.80,
    },
}

SHEET_NAME    = "yaeyama_operation_log"
SHEET_HEADERS = [
    "date", "recorded_at",
    "route_id", "route_name",
    # 高速船 便別運航状況（石垣発・各便）
    # ◯ → 1, △・✕ → 0, 該当便なし → None
    # 最大6便（竹富）、少ない路線は末尾がNone
    "hs_bin1_operated",   # 石垣発 第1便
    "hs_bin2_operated",   # 石垣発 第2便
    "hs_bin3_operated",   # 石垣発 第3便
    "hs_bin4_operated",   # 石垣発 第4便（上原・竹富など）
    "hs_bin5_operated",   # 石垣発 第5便（竹富など）
    "hs_bin6_operated",   # 石垣発 第6便（竹富のみ）
    "hs_bins_count",      # 本日の石垣発総便数
    "hs_bins_text",       # JSON: 全便の詳細 [{"time":"08:00","status":"◯"},...]
    # 貨客船（route6 波照間のみ: 1=運航, 0=欠航, None=対象外）
    "ferry_operated",
    # 欠航理由
    "hs_cancel_reason",     # weather/dock/equipment/none
    "ferry_cancel_reason",
    "caption_text",         # 安栄観光HP備考テキスト
    "update_time",          # HP更新時刻
    # 気象データ（当日）
    "wave_max",
    "swell_max",
    "wind_max",
    # 気象データ（翌日予報）
    "tmr_wave_max",
    "tmr_swell_max",
    "tmr_wind_max",
    # 気象データ（明後日予報）
    "dayafter_wave_max",
    # 派生
    "hs_weather_cancel",    # 高速船が1便以上気象欠航（0/1）
    "ferry_weather_cancel", # 貨客船が気象欠航（0/1）
]


# ============================================================
# 1. 安栄観光HP スクレイピング（全航路一括）
# ============================================================

def _status_from_span(span):
    """spanのクラスからステータス文字列を返す"""
    if span is None:
        return "―"
    classes = span.get("class", [])
    if "condition_item_circle"   in classes: return "◯"
    if "condition_item_triangle" in classes: return "△"
    if "conditon_item_times"     in classes: return "✕"   # サイト側タイポそのまま
    return span.get_text(strip=True)


def _cancel_reason(text):
    """
    テキストから欠航理由カテゴリを判定。
    この関数は欠航が確定した後にのみ呼ばれる想定のため、
    「通常運航」チェックは持たない（混在テキストで誤判定するため除去）。
    """
    if any(w in text for w in ["機器", "エンジン", "トラブル", "故障", "点検", "整備"]):
        return "equipment"
    if "ドック" in text or "dock" in text.lower():
        return "dock"
    return "weather"


def _split_caption(caption_text):
    """
    caption_text を高速船セクションと貨客船セクションに分割する。
    「貨客船」または「フェリーはてるま」が最初に出現する位置で分割。
    波照間航路では高速船欠航（海上時化）と貨客船欠航（ドック）が
    同一 caption_text に混在するため、各船種の欠航理由判定を分離する。
    """
    ferry_markers = ["貨客船", "フェリーはてるま"]
    split_pos = len(caption_text)
    for marker in ferry_markers:
        pos = caption_text.find(marker)
        if 0 <= pos < split_pos:
            split_pos = pos
    return caption_text[:split_pos].strip(), caption_text[split_pos:].strip()


def _bin_operated(bins, idx):
    """
    idx番目のbinの運航状態を 1/0 で返す（該当便なし → None）。
    ◯ → 1（運航）
    △ → 0（条件付き・不確実 = 欠航扱い）
    ✕ → 0（欠航）
    """
    if idx >= len(bins):
        return None
    return 1 if bins[idx]["status"] == "◯" else 0


def get_all_routes_operation_status():
    """
    安栄観光の運航状況ページから全航路（route1〜7）の情報を一括取得。

    戻り値: {
        "update_time":  "05:44",
        "caution_text": "...",
        "routes": {
            "route1": { hs_bins, ferry_operated, hs_cancel_reason,
                        ferry_cancel_reason, caption_text },
            ...
        }
    }
    """
    result = {
        "update_time":  "",
        "caution_text": "",
        "routes":       {rid: _empty_route() for rid in ROUTE_CONFIGS},
    }

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(ANEI_CONDITION_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 更新時刻
        update_div = soup.find("div", class_="condition_item_update")
        if update_div:
            m = re.search(r"(\d{2}:\d{2})", update_div.get_text())
            result["update_time"] = m.group(1) if m else ""

        # 全体注意書き
        caution_div = soup.find("div", class_="condition_item_caution")
        if caution_div:
            result["caution_text"] = caution_div.get_text(separator=" ", strip=True)

        # 各航路をパース
        for route_id in ROUTE_CONFIGS:
            route_result = _parse_route(soup, route_id, result["caution_text"])
            result["routes"][route_id] = route_result
            bins = route_result["hs_bins"]
            bin_summary = " ".join(
                f"{b['time']}:{b['status']}" for b in bins
            )
            print(f"  [{route_id}] {ROUTE_CONFIGS[route_id]['name']}: "
                  f"[{bin_summary}] ({route_result['hs_cancel_reason']}) "
                  f"貨客船={route_result['ferry_operated']}")

        print(f"  [安栄観光HP] 更新時刻: {result['update_time']}")

    except Exception as e:
        print(f"  [警告] 安栄観光HP取得エラー: {e}")

    return result


def _empty_route():
    return {
        "hs_bins":             [],
        "ferry_operated":      None,
        "hs_cancel_reason":    "none",
        "ferry_cancel_reason": "none",
        "caption_text":        "",
    }


def _parse_route(soup, route_id, caution_text):
    """soup から指定航路のステータスをパース"""
    r = _empty_route()

    route_title = soup.find("div", id=route_id)
    if not route_title:
        print(f"  [警告] {route_id} セクション未検出")
        return r

    condition_item = route_title.parent

    # 備考テキスト（タイポ注意: "conditon"）
    caption_div = condition_item.find("div", class_="conditon_item_caption")
    caption_text = caption_div.get_text(separator=" ", strip=True) if caption_div else ""
    r["caption_text"] = caption_text

    # caption_text をHS部分・フェリー部分に分割（波照間で混在する場合の対策）
    hs_caption, ferry_caption = _split_caption(caption_text)

    # 便別ステータス（石垣発 = port_details[0]）
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

    # HS欠航理由：hs_caption（フェリー部分を除いたテキスト）のみで判定。
    # caption_text 全体を使うとフェリーの「ドック」がHSの欠航理由に混入する。
    if any(b["status"] == "✕" for b in hs_bins):
        r["hs_cancel_reason"] = _cancel_reason(hs_caption or caption_text)
    elif any(b["status"] == "△" for b in hs_bins):
        # △は「通常運航」が明示されている場合は none（一部条件付き運航）
        if "通常運航" in hs_caption:
            r["hs_cancel_reason"] = "none"
        else:
            r["hs_cancel_reason"] = _cancel_reason(hs_caption or caption_text)
    else:
        r["hs_cancel_reason"] = "none"

    # 貨客船（フェリーはてるま）はroute6のみ。ferry_caption のみで判定。
    if route_id == "route6":
        ferry_keywords        = ["フェリーはてるま", "貨客"]
        ferry_cancel_keywords = ["運休", "欠航", "ドック"]
        # caption_text にフェリー情報があれば ferry_caption を使用
        if any(kw in caption_text for kw in ferry_keywords):
            if any(kw in ferry_caption for kw in ferry_cancel_keywords):
                r["ferry_operated"]      = 0
                r["ferry_cancel_reason"] = _cancel_reason(ferry_caption)
            else:
                r["ferry_operated"]      = 1
                r["ferry_cancel_reason"] = "none"
        else:
            # caption_text にフェリー情報がない場合は caution_text で補完
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
# 2. Open-Meteo 海洋・気象データ取得（座標ごとにキャッシュ）
# ============================================================

_weather_cache = {}   # (lat, lon) -> result dict


def get_weather_for_coord(lat, lon):
    """指定座標の当日〜明後日の最大波高・うねり・風速を返す（キャッシュ付き）"""
    key = (round(lat, 6), round(lon, 6))
    if key in _weather_cache:
        return _weather_cache[key]

    result = {
        "today_max_wave":    None,
        "today_max_swell":   None,
        "today_max_wind":    None,
        "tmr_max_wave":      None,
        "tmr_max_swell":     None,
        "tmr_max_wind":      None,
        "dayafter_max_wave": None,
    }

    try:
        marine_url = (
            f"https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wave_height,swell_wave_height"
            f"&timezone=Asia%2FTokyo&forecast_days=3"
        )
        marine_data = requests.get(marine_url, timeout=15).json()

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wind_speed_10m"
            f"&wind_speed_unit=ms"
            f"&timezone=Asia%2FTokyo&forecast_days=3"
        )
        weather_data = requests.get(weather_url, timeout=15).json()

        now = datetime.now(JST)

        def _daily_max(data, key, delta):
            target = (now + timedelta(days=delta)).strftime("%Y-%m-%d")
            times  = data.get("hourly", {}).get("time", [])
            values = data.get("hourly", {}).get(key, [])
            vals   = [v for t, v in zip(times, values)
                      if t.startswith(target) and v is not None]
            return round(max(vals), 2) if vals else None

        result["today_max_wave"]    = _daily_max(marine_data,  "wave_height",       0)
        result["today_max_swell"]   = _daily_max(marine_data,  "swell_wave_height", 0)
        result["today_max_wind"]    = _daily_max(weather_data, "wind_speed_10m",    0)
        result["tmr_max_wave"]      = _daily_max(marine_data,  "wave_height",       1)
        result["tmr_max_swell"]     = _daily_max(marine_data,  "swell_wave_height", 1)
        result["tmr_max_wind"]      = _daily_max(weather_data, "wind_speed_10m",    1)
        result["dayafter_max_wave"] = _daily_max(marine_data,  "wave_height",       2)

    except Exception as e:
        print(f"  [警告] Open-Meteo取得エラー ({lat},{lon}): {e}")

    _weather_cache[key] = result
    return result


# ============================================================
# 3. Google Sheets への書き込み
# ============================================================

def log_daily_records():
    """メイン関数。全航路のデータを収集しSheetsに7行追加する。"""
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID_YAEYAMA")
    svc_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not sheets_id or not svc_json:
        print("  [スキップ] 環境変数未設定（GOOGLE_SHEETS_ID_YAEYAMA / GOOGLE_SERVICE_ACCOUNT_JSON）")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=["https://spreadsheets.google.com/feeds",
                    "https://www.googleapis.com/auth/drive"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheets_id)

        try:
            ws = sh.worksheet(SHEET_NAME)
        except Exception:
            ws = sh.add_worksheet(title=SHEET_NAME, rows=10000, cols=len(SHEET_HEADERS))
            ws.append_row(SHEET_HEADERS)
            print(f"  新規シート作成: {SHEET_NAME}")

    except Exception as e:
        print(f"  [エラー] Sheets接続失敗: {e}")
        return

    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    # 重複チェック：今日の route1 行が既に存在するか
    try:
        col_date  = ws.col_values(1)   # date 列
        col_route = ws.col_values(3)   # route_id 列
        for d, r in zip(col_date, col_route):
            if d == today_str and r == "route1":
                print(f"  [スキップ] {today_str} の記録はすでに存在します")
                return
    except Exception as e:
        print(f"  [警告] 重複チェックエラー（続行）: {e}")

    print(f"\n[八重山ロガー] データ収集中（{today_str}）...")

    # 安栄観光HP 一括取得
    all_routes = get_all_routes_operation_status()

    # 全7航路の行を構築
    rows = []
    for route_id, cfg in ROUTE_CONFIGS.items():
        op      = all_routes["routes"][route_id]
        weather = get_weather_for_coord(cfg["lat"], cfg["lon"])
        bins    = op["hs_bins"]

        # 高速船 便別 1/0 変換（◯・△=1, ✕=0, 該当便なし=None）
        b = [_bin_operated(bins, i) for i in range(6)]

        # 気象欠航：1便以上✕かつ理由がweather
        has_cancel     = any(b[i] == 0 for i in range(len(bins)))
        hs_w_cancel    = 1 if has_cancel and op["hs_cancel_reason"] == "weather" else 0
        ferry_w_cancel = 1 if (op["ferry_operated"] == 0 and op["ferry_cancel_reason"] == "weather") else 0

        rows.append([
            today_str,
            now.strftime("%Y-%m-%d %H:%M"),
            route_id,
            cfg["name"],
            b[0], b[1], b[2], b[3], b[4], b[5],   # hs_bin1〜6_operated
            len(bins),                              # hs_bins_count
            json.dumps(bins, ensure_ascii=False),   # hs_bins_text
            op["ferry_operated"],
            op["hs_cancel_reason"],
            op["ferry_cancel_reason"],
            op["caption_text"][:300],
            all_routes["update_time"],
            weather["today_max_wave"],
            weather["today_max_swell"],
            weather["today_max_wind"],
            weather["tmr_max_wave"],
            weather["tmr_max_swell"],
            weather["tmr_max_wind"],
            weather["dayafter_max_wave"],
            hs_w_cancel,
            ferry_w_cancel,
        ])

    # Sheetsに一括書き込み
    try:
        for row in rows:
            ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"  ✅ Sheets記録完了: {today_str} / {len(rows)}航路")
    except Exception as e:
        print(f"  [エラー] Sheets書き込み失敗: {e}")


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print(f"Yaeyama All-Routes Logger: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    log_daily_records()
    print("\n完了。")
