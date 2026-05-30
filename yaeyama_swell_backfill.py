"""
yaeyama_swell_backfill.py
既存の yaeyama_operation_log シートに 2 列を追加し、
Open-Meteo archive / marine API から過去データを遡及取得して埋める（一回限り）。

追加カラム（シートの末尾に追加）:
  swell_period_max  - 当日の最大うねり周期（秒）
  wind_dir_dominant - 当日の平均風向（度: 0=北, 90=東, 180=南, 270=西）

安全方針:
  - 既存列には一切手を触れない（新列のみ追加）
  - 既に値が入っている行はスキップ
  - 書き込みは新列の範囲のみをバッチ更新
"""

import os
import json
import time
import math
import requests
import numpy as np
from datetime import datetime

SHEET_NAME = "yaeyama_operation_log"

ROUTE_COORDS = {
    "route1": (24.28,       124.13),
    "route2": (24.37,       124.15),
    "route3": (24.36,       124.10),
    "route4": (24.22,       124.14),
    "route5": (24.40,       123.86),
    "route6": (24.165974,   123.836266),
    "route7": (24.47,       123.80),
}

NEW_COLS = ["swell_period_max", "wind_dir_dominant"]


# ============================================================
# ユーティリティ
# ============================================================

def col_letter(n):
    """1-indexed 列番号 → スプレッドシート列名 (例: 1→A, 26→Z, 27→AA)"""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def circular_mean_deg(angles):
    """角度のリスト（度）→ 円形平均（度）"""
    rads = [a * math.pi / 180 for a in angles]
    s = sum(math.sin(r) for r in rads) / len(rads)
    c = sum(math.cos(r) for r in rads) / len(rads)
    return round(math.degrees(math.atan2(s, c)) % 360, 1)


# ============================================================
# Sheets 接続
# ============================================================

def connect_sheets():
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID_YAEYAMA")
    svc_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheets_id or not svc_json:
        raise RuntimeError("環境変数未設定: GOOGLE_SHEETS_ID_YAEYAMA / GOOGLE_SERVICE_ACCOUNT_JSON")
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(svc_json),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheets_id).worksheet(SHEET_NAME)


# ============================================================
# Open-Meteo 取得（年単位キャッシュ）
# ============================================================

def fetch_year_weather(lat, lon, year):
    """
    1年分の (date → swell_period_max), (date → wind_dir_dominant) を返す。
    Open-Meteo marine archive + archive API を使用。
    """
    today = datetime.now()
    start = f"{year}-01-01"
    end   = f"{year}-12-31" if year < today.year else today.strftime("%Y-%m-%d")

    swell_by_date = {}
    wind_dir_by_date = {}

    # ── Marine API: swell_wave_period ─────────────────────
    try:
        marine_url = (
            f"https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=swell_wave_period"
            f"&start_date={start}&end_date={end}"
            f"&timezone=Asia/Tokyo"
        )
        data = requests.get(marine_url, timeout=10).json()
        times   = data.get("hourly", {}).get("time", [])
        periods = data.get("hourly", {}).get("swell_wave_period", [])
        for t, p in zip(times, periods):
            if p is not None:
                d = t[:10]
                swell_by_date.setdefault(d, []).append(p)
        swell_by_date = {d: round(max(v), 2) for d, v in swell_by_date.items()}
    except Exception as e:
        print(f"    [警告] marine API ({year} {lat},{lon}): {e}")

    # ── Archive API: wind_direction_10m ───────────────────
    try:
        archive_url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wind_direction_10m"
            f"&start_date={start}&end_date={end}"
            f"&timezone=Asia/Tokyo"
        )
        data = requests.get(archive_url, timeout=10).json()
        times = data.get("hourly", {}).get("time", [])
        dirs  = data.get("hourly", {}).get("wind_direction_10m", [])
        for t, d in zip(times, dirs):
            if d is not None:
                date = t[:10]
                wind_dir_by_date.setdefault(date, []).append(d)
        wind_dir_by_date = {d: circular_mean_deg(v) for d, v in wind_dir_by_date.items()}
    except Exception as e:
        print(f"    [警告] archive API ({year} {lat},{lon}): {e}")

    return swell_by_date, wind_dir_by_date


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 60)
    print("Yaeyama Swell Period Backfill")
    print("=" * 60)

    ws = connect_sheets()
    print("\nシートデータ読み込み中...")
    all_values = ws.get_all_values()
    header = all_values[0]
    rows   = all_values[1:]
    n_rows = len(rows)
    print(f"  ヘッダー列数: {len(header)} / データ行数: {n_rows:,}")

    # ── 新列の位置を決定 ──────────────────────────────────
    col_indices = {}
    header_extended = list(header)
    for col in NEW_COLS:
        if col in header_extended:
            col_indices[col] = header_extended.index(col)
            print(f"  列 '{col}' は既存（index={col_indices[col]}）")
        else:
            col_indices[col] = len(header_extended)
            header_extended.append(col)
            print(f"  列 '{col}' を追加（index={col_indices[col]}）")

    n_new_cols   = len(NEW_COLS)
    first_new_idx = col_indices[NEW_COLS[0]]   # 新列の開始インデックス（0-based）
    first_new_col = col_letter(first_new_idx + 1)  # 1-based → A1 記法
    last_new_col  = col_letter(first_new_idx + n_new_cols)

    # ── シートの列数を拡張（既存26列→28列）──────────────────
    current_cols = ws.col_count
    needed_cols  = first_new_idx + n_new_cols  # 0-based index + count = 必要な列数
    if current_cols < needed_cols:
        print(f"\n  シート列数を拡張: {current_cols} → {needed_cols}")
        ws.resize(rows=ws.row_count, cols=needed_cols)

    # ヘッダー行の新列部分を更新
    print(f"\n  ヘッダー更新: {first_new_col}1:{last_new_col}1")
    ws.update(
        [[col for col in NEW_COLS]],
        f"{first_new_col}1:{last_new_col}1"
    )

    # ── 列インデックスを特定（元のヘッダーから） ──────────
    date_col  = header.index("date")    if "date"     in header else 0
    route_col = header.index("route_id") if "route_id" in header else 2

    # ── 気象キャッシュ: {(route_id, year): (swell_by_date, wind_dir_by_date)} ──
    weather_cache = {}

    def get_weather(route_id, date_str):
        if not date_str or route_id not in ROUTE_COORDS:
            return None, None
        year = int(date_str[:4])
        key  = (route_id, year)
        if key not in weather_cache:
            lat, lon = ROUTE_COORDS[route_id]
            print(f"  Fetching Open-Meteo: {route_id} {year} ({lat},{lon})...")
            sp, wd = fetch_year_weather(lat, lon, year)
            weather_cache[key] = (sp, wd)
            time.sleep(0.5)
        sp_map, wd_map = weather_cache[key]
        return sp_map.get(date_str), wd_map.get(date_str)

    # ── 各行の新列値を計算 ────────────────────────────────
    print(f"\n気象データ計算中...")
    new_col_values = []
    skipped = 0
    filled  = 0

    for i, row in enumerate(rows):
        if i % 2000 == 0:
            print(f"  {i:,}/{n_rows:,} 処理中...")

        # 既存行の新列値を取得（列が足りない場合は空文字）
        sp_existing = row[col_indices["swell_period_max"]] if len(row) > col_indices["swell_period_max"] else ""
        wd_existing = row[col_indices["wind_dir_dominant"]] if len(row) > col_indices["wind_dir_dominant"] else ""

        # 既入力済みならそのまま使用
        if sp_existing != "":
            new_col_values.append([sp_existing, wd_existing])
            skipped += 1
            continue

        date_str = row[date_col]  if len(row) > date_col  else ""
        route_id = row[route_col] if len(row) > route_col else ""

        sp, wd = get_weather(route_id, date_str)
        new_col_values.append([
            round(sp, 2) if sp is not None else "",
            round(wd, 1) if wd is not None else "",
        ])
        if sp is not None:
            filled += 1

    print(f"\n  計算完了: 新規埋め={filled:,} / スキップ(既入力)={skipped:,}")

    # ── シートに書き込み（新列のみ・バッチ更新） ──────────
    print(f"\nシートへの書き込み: {first_new_col}2:{last_new_col}{n_rows+1}")
    batch_size = 1000
    for start in range(0, n_rows, batch_size):
        end   = min(start + batch_size, n_rows)
        batch = new_col_values[start:end]
        range_str = f"{first_new_col}{start+2}:{last_new_col}{end+1}"
        ws.update(batch, range_str)
        print(f"  {end:,}/{n_rows:,} 行書き込み済み")
        time.sleep(1)

    print(f"\n✅ 完了: {filled:,} 行に swell_period_max / wind_dir_dominant を追加")
    print("=" * 60)


if __name__ == "__main__":
    main()
