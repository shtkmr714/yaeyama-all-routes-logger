"""
yaeyama_threshold_analyzer.py
route1・route3（正例不足でモデルスキップ）および route2（AUC低）の
欠航日 vs 運航日の気象値分布を分析。
ルールベース閾値候補を提示する（wave_max を中心に F1 最大閾値を推奨）。
"""

import os
import json
import numpy as np
import pandas as pd

SHEET_NAME = "yaeyama_operation_log"
ROUTES = {
    "route1": "大原（西表島東）",
    "route2": "小浜島",
    "route3": "竹富島",
}
FEATURES = ["wave_max", "swell_max", "wind_max"]


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
# 分析
# ============================================================

def analyze_route(df_r, route_id, route_name):
    df_cancel = df_r[df_r["hs_weather_cancel"] == 1]
    df_op     = df_r[df_r["hs_weather_cancel"] == 0]

    print(f"\n{'='*60}")
    print(f"[{route_id}] {route_name}")
    print(f"  総データ: {len(df_r):,} | 欠航: {len(df_cancel)} | 運航: {len(df_op):,}")

    if len(df_cancel) == 0:
        print("  欠航データなし → スキップ")
        return

    # ── 分布サマリー ──────────────────────────────────────
    print()
    header = f"  {'特徴量':<14}{'指標':>8}{'欠航日':>10}{'運航日':>10}"
    print(header)
    print("  " + "-" * 42)
    for feat in FEATURES:
        cv = df_cancel[feat].dropna()
        ov = df_op[feat].dropna()
        for stat_name, cv_val, ov_val in [
            ("min",    cv.min(),           ov.min()),
            ("p25",    cv.quantile(0.25),  ov.quantile(0.25)),
            ("median", cv.median(),        ov.median()),
            ("p75",    cv.quantile(0.75),  ov.quantile(0.75)),
            ("max",    cv.max(),           ov.max()),
        ]:
            print(f"  {feat:<14}{stat_name:>8}  {cv_val:>8.2f}  {ov_val:>8.2f}")
        print()

    # ── wave_max 閾値ごとの Precision / Recall ───────────
    print(f"  【wave_max 閾値候補】")
    print(f"  {'閾値':>6}  {'欠航捕捉率':>9}  {'精度':>6}  {'F1':>6}  TP  FP")
    cv_wave = df_cancel["wave_max"].dropna()
    ov_wave = df_op["wave_max"].dropna()
    thresholds = np.arange(0.5, 6.0, 0.25)
    best_thrs = []
    for thr in thresholds:
        tp = int((cv_wave >= thr).sum())
        fp = int((ov_wave >= thr).sum())
        recall    = tp / len(cv_wave) if len(cv_wave) > 0 else 0.0
        precision = tp / (tp + fp)    if (tp + fp) > 0    else 0.0
        f1        = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0
        if 0.02 <= recall:
            print(f"  {thr:>6.2f}  {recall:>9.1%}  {precision:>6.1%}  {f1:>6.3f}  {tp:>3} {fp:>4}")
            best_thrs.append((thr, recall, precision, f1))

    if best_thrs:
        best = max(best_thrs, key=lambda x: x[3])
        print(f"\n  ★ 推奨閾値（F1最大）: wave_max >= {best[0]:.2f} "
              f"（捕捉率={best[1]:.0%}  精度={best[2]:.0%}  F1={best[3]:.3f}）")

    # ── wind_max 閾値候補 ─────────────────────────────────
    print(f"\n  【wind_max 閾値候補】")
    print(f"  {'閾値':>6}  {'欠航捕捉率':>9}  {'精度':>6}  {'F1':>6}  TP  FP")
    cv_wind = df_cancel["wind_max"].dropna()
    ov_wind = df_op["wind_max"].dropna()
    wind_thrs = np.arange(5, 25, 1.0)
    best_wind = []
    for thr in wind_thrs:
        tp = int((cv_wind >= thr).sum())
        fp = int((ov_wind >= thr).sum())
        recall    = tp / len(cv_wind) if len(cv_wind) > 0 else 0.0
        precision = tp / (tp + fp)    if (tp + fp) > 0    else 0.0
        f1        = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0
        if 0.02 <= recall:
            print(f"  {thr:>6.1f}  {recall:>9.1%}  {precision:>6.1%}  {f1:>6.3f}  {tp:>3} {fp:>4}")
            best_wind.append((thr, recall, precision, f1))

    if best_wind:
        best_w = max(best_wind, key=lambda x: x[3])
        print(f"\n  ★ 推奨閾値（F1最大）: wind_max >= {best_w[0]:.1f} "
              f"（捕捉率={best_w[1]:.0%}  精度={best_w[2]:.0%}  F1={best_w[3]:.3f}）")


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 60)
    print("Yaeyama Threshold Analyzer")
    print("=" * 60)

    ws = connect_sheets()
    print("シートからデータ読み込み中...")
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    print(f"  総行数: {len(df):,}")

    for col in FEATURES + ["hs_weather_cancel"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for route_id, route_name in ROUTES.items():
        df_r = df[
            (df["route_id"] == route_id) &
            df[FEATURES].notna().all(axis=1) &
            df["hs_weather_cancel"].notna()
        ].copy()
        analyze_route(df_r, route_id, route_name)

    print("\n\n" + "=" * 60)
    print("分析完了")


if __name__ == "__main__":
    main()
