"""
yaeyama_model_trainer.py
航路別欠航予測ロジスティック回帰モデルの学習スクリプト（一回限り）。

Google Sheets から yaeyama_operation_log を読み込み、
航路 × HS/フェリー 別にモデルを学習して yaeyama_cancel_model.json に保存する。

Output: yaeyama_cancel_model.json（リポジトリに commit される）
"""

import os
import json
import math
import warnings
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")

# ============================================================
# 定数
# ============================================================

SHEET_NAME  = "yaeyama_operation_log"
OUTPUT_FILE = "yaeyama_cancel_model.json"

FEATURES = ["wave_max", "swell_max", "wind_max"]   # 当日実測値（予測時はforecastを代入）

ROUTE_CONFIGS = {
    "route1": "大原（西表島東）",
    "route2": "小浜島",
    "route3": "竹富島",
    "route4": "黒島",
    "route5": "上原（西表島北）",
    "route6": "波照間島",
    "route7": "鳩間島",
}

MIN_POSITIVE = 20    # 正例がこれ未満ならモデル構築をスキップ


# ============================================================
# Sheets 接続
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
    return gc.open_by_key(sheets_id).worksheet(SHEET_NAME)


# ============================================================
# データ読み込み
# ============================================================

def load_dataframe(ws):
    print("シートからデータ読み込み中...")
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    print(f"  総行数: {len(df):,}")

    num_cols = [
        "wave_max", "swell_max", "wind_max",
        "tmr_wave_max", "tmr_swell_max", "tmr_wind_max",
        "dayafter_wave_max",
        "hs_weather_cancel", "ferry_weather_cancel",
        "hs_bins_count", "ferry_operated",
        "hs_bin1_operated", "hs_bin2_operated", "hs_bin3_operated",
        "hs_bin4_operated", "hs_bin5_operated", "hs_bin6_operated",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ============================================================
# モデル学習
# ============================================================

def train_logistic(X, y, route_id, target_name):
    """
    StandardScaler + LogisticRegression を学習し、
    モデルパラメータ辞書（JSON保存用）を返す。
    """
    n_pos   = int(y.sum())
    n_total = len(y)
    cancel_rate = n_pos / n_total if n_total > 0 else 0.0

    print(f"    サンプル: {n_total:,}  正例（欠航）: {n_pos}  欠航率: {cancel_rate:.1%}")

    if n_pos < MIN_POSITIVE:
        print(f"    ⚠ 正例数不足（{n_pos} < {MIN_POSITIVE}）→ スキップ")
        return None

    # --- 学習 ---
    scaler = StandardScaler()
    X_s    = scaler.fit_transform(X)

    # class_weight='balanced' で少数クラス（欠航）を強調
    clf = LogisticRegression(C=1.0, class_weight="balanced",
                             max_iter=1000, random_state=42)
    clf.fit(X_s, y)

    # --- CV AUC（5-fold） ---
    pipe = Pipeline([
        ("sc",  StandardScaler()),
        ("clf", LogisticRegression(C=1.0, class_weight="balanced",
                                   max_iter=1000, random_state=42)),
    ])
    cv     = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(pipe, X, y, cv=cv, scoring="roc_auc")
    cv_auc = float(scores.mean())
    cv_std = float(scores.std())

    print(f"    CV AUC: {cv_auc:.3f} ± {cv_std:.3f}")

    # --- 係数の解釈（特徴量重要度） ---
    for feat, coef in zip(FEATURES, clf.coef_[0]):
        print(f"      {feat}: {coef:+.3f}")

    return {
        "route_id":           route_id,
        "target":             target_name,
        "features":           FEATURES,
        "scaler_mean":        scaler.mean_.tolist(),
        "scaler_scale":       scaler.scale_.tolist(),
        "coef":               clf.coef_[0].tolist(),
        "intercept":          float(clf.intercept_[0]),
        "threshold":          0.5,
        "train_samples":      n_total,
        "positive_samples":   n_pos,
        "cancellation_rate":  round(cancel_rate, 4),
        "cv_auc":             round(cv_auc, 4),
        "cv_auc_std":         round(cv_std, 4),
    }


# ============================================================
# モデル適用（sklearn 不要の inference 用）
# ============================================================

def predict_cancel_prob(model_params, wave, swell, wind):
    """
    保存した JSON パラメータから欠航確率（0〜1）を計算。
    sklearn 不要。ferry_alert.py / forecast_publisher.py から呼び出し可能。

    使用例:
        with open("yaeyama_cancel_model.json") as f:
            models = json.load(f)
        prob = predict_cancel_prob(models["route6"]["hs"], wave=2.5, swell=1.8, wind=12.0)
    """
    m    = model_params
    vals = [wave, swell, wind]
    x_s  = [(v - mu) / sc
            for v, mu, sc in zip(vals, m["scaler_mean"], m["scaler_scale"])]
    z    = m["intercept"] + sum(c * x for c, x in zip(m["coef"], x_s))
    return 1.0 / (1.0 + math.exp(-z))


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 60)
    print("Yaeyama Cancel Model Trainer")
    print("=" * 60)

    ws = connect_sheets()
    df = load_dataframe(ws)

    all_models = {}

    for route_id, route_name in ROUTE_CONFIGS.items():
        print(f"\n{'='*50}")
        print(f"[{route_id}] {route_name}")

        df_r  = df[df["route_id"] == route_id].copy()
        # 気象データが揃っている行のみ
        mask  = df_r[FEATURES].notna().all(axis=1)
        df_r  = df_r[mask]
        print(f"  気象データあり行: {len(df_r):,}")

        entry = {"route_name": route_name}

        # ── HS 欠航モデル ──────────────────────────────────
        print(f"\n  [HS 欠航モデル]")
        df_hs = df_r[df_r["hs_bins_count"].fillna(0) > 0].dropna(
            subset=["hs_weather_cancel"])
        if len(df_hs) > 0:
            X_hs = df_hs[FEATURES].values.astype(float)
            y_hs = df_hs["hs_weather_cancel"].values.astype(int)
            entry["hs"] = train_logistic(X_hs, y_hs, route_id, "hs_weather_cancel")
        else:
            print("    データなし")
            entry["hs"] = None

        # ── フェリー欠航モデル（ferry_operatedデータがある航路のみ）──
        df_fw = df_r[df_r["ferry_operated"].notna()].dropna(
            subset=["ferry_weather_cancel"])
        if len(df_fw) > 0 and df_fw["ferry_weather_cancel"].notna().sum() > 0:
            print(f"\n  [フェリー欠航モデル]")
            X_fw = df_fw[FEATURES].values.astype(float)
            y_fw = df_fw["ferry_weather_cancel"].values.astype(int)
            entry["ferry"] = train_logistic(X_fw, y_fw, route_id, "ferry_weather_cancel")

        all_models[route_id] = entry

    # ── JSON 保存 ──────────────────────────────────────────
    print(f"\n\nJSON 保存: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_models, f, ensure_ascii=False, indent=2)

    # ── サマリー ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"{'Route':<8} {'対象':<6} {'n':>6} {'欠航率':>7} {'CV AUC':>8}  評価")
    print("-" * 50)
    for rid, route_data in all_models.items():
        for mtype in ["hs", "ferry"]:
            m = route_data.get(mtype)
            if not m:
                continue
            quality = ("✅" if m["cv_auc"] >= 0.80
                       else "⚠" if m["cv_auc"] >= 0.65
                       else "❌")
            print(f"{rid:<8} {mtype:<6} {m['train_samples']:>6,} "
                  f"{m['cancellation_rate']:>7.1%} {m['cv_auc']:>8.3f}  {quality}")
    print("=" * 60)
    print(f"\n保存完了: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
