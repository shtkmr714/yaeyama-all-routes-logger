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

# route2（小浜島）専用: swell_period_max を追加（wave_maxは欠航と無相関）
ROUTE2_FEATURES = ["wave_max", "swell_max", "wind_max", "swell_period_max"]

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

# ルールベースモデルの閾値（threshold_analyzerの結果から設定）
# model_type = "rule" の場合に使用
RULE_BASED_PARAMS = {
    "route1": {
        "model_type":      "rule",
        "route_id":        "route1",
        "target":          "hs_weather_cancel",
        "features":        ["wave_max", "wind_max"],
        "wave_thr_high":   4.50,   # この閾値以上: 高確率欠航（精度85%）
        "wave_thr_mid":    3.25,   # F1最大の閾値（精度64%）
        "wind_thr":        12.0,   # F1最大の閾値（精度58%）
        "prob_wave_high":  0.80,
        "prob_wave_mid":   0.50,
        "prob_wind_add":   0.20,
        "f1_wave":         0.621,
        "f1_wind":         0.647,
        "note":            "正例15件でロジスティック回帰スキップ。threshold_analyzerの結果に基づくルールベース。",
    },
    "route3": {
        "model_type":      "rule",
        "route_id":        "route3",
        "target":          "hs_weather_cancel",
        "features":        ["wave_max", "wind_max"],
        "wave_thr_high":   3.75,   # この閾値以上: 精度100%（必ず欠航）
        "wave_thr_mid":    2.50,   # 中程度リスク
        "wind_thr":        12.0,   # F1最大の閾値（精度47%）
        "prob_wave_high":  0.90,
        "prob_wave_mid":   0.35,
        "prob_wind_add":   0.20,
        "f1_wave":         0.435,
        "f1_wind":         0.486,
        "note":            "正例18件でロジスティック回帰スキップ。wave>=3.75は精度100%（必ず欠航）。",
    },
}


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
        "swell_period_max", "wind_dir_dominant",
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

def train_logistic(X, y, route_id, target_name, features=None):
    """
    StandardScaler + LogisticRegression を学習し、
    モデルパラメータ辞書（JSON保存用）を返す。
    features: 使用する特徴量名リスト（省略時はグローバルの FEATURES）
    """
    if features is None:
        features = FEATURES
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
    for feat, coef in zip(features, clf.coef_[0]):
        print(f"      {feat}: {coef:+.3f}")

    return {
        "route_id":           route_id,
        "target":             target_name,
        "features":           features,
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

def predict_cancel_prob(model_params, wave, swell, wind, swell_period=None):
    """
    保存した JSON パラメータから欠航確率（0〜1）を計算。
    sklearn 不要。ferry_alert.py / forecast_publisher.py から呼び出し可能。

    route2 など swell_period_max を使うモデルは swell_period を渡すこと。

    使用例:
        with open("yaeyama_cancel_model.json") as f:
            models = json.load(f)
        # 通常（3特徴量）
        prob = predict_cancel_prob(models["route6"]["hs"], wave=2.5, swell=1.8, wind=12.0)
        # route2（4特徴量）
        prob = predict_cancel_prob(models["route2"]["hs"], wave=1.2, swell=0.8, wind=8.0, swell_period=14.0)
    """
    m         = model_params
    feat_vals = {
        "wave_max":        wave,
        "swell_max":       swell,
        "wind_max":        wind,
        "swell_period_max": swell_period,
    }
    vals = [feat_vals[f] for f in m["features"]]
    x_s  = [(v - mu) / sc
            for v, mu, sc in zip(vals, m["scaler_mean"], m["scaler_scale"])]
    z    = m["intercept"] + sum(c * x for c, x in zip(m["coef"], x_s))
    return 1.0 / (1.0 + math.exp(-z))


# ============================================================
# ルールベース推論（sklearn 不要）
# ============================================================

def predict_cancel_prob_rule(model_params, wave, swell, wind):
    """
    ルールベースモデルの欠航確率推論関数（sklearn不要）。
    正例不足でロジスティック回帰をスキップした航路に使用。

    使用例:
        with open("yaeyama_cancel_model.json") as f:
            models = json.load(f)
        m = models["route1"]["hs"]
        if m and m.get("model_type") == "rule":
            prob = predict_cancel_prob_rule(m, wave=3.5, swell=2.0, wind=13.0)
    """
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
        # 航路固有の特徴量セットを決定
        route_features = ROUTE2_FEATURES if route_id == "route2" else FEATURES
        # 基本気象データ（3列）が揃っている行のみ（route2はさらに swell_period_max も要求）
        mask  = df_r[route_features].notna().all(axis=1)
        df_r  = df_r[mask]
        print(f"  気象データあり行: {len(df_r):,}  使用特徴量: {route_features}")

        entry = {"route_name": route_name}

        # ── HS 欠航モデル ──────────────────────────────────
        print(f"\n  [HS 欠航モデル]")

        # ルールベース対象航路はロジスティック回帰をスキップしてルールを適用
        if route_id in RULE_BASED_PARAMS:
            print(f"    → ルールベースモデルを使用（正例不足）")
            entry["hs"] = RULE_BASED_PARAMS[route_id]
        else:
            df_hs = df_r[df_r["hs_bins_count"].fillna(0) > 0].dropna(
                subset=["hs_weather_cancel"])
            if len(df_hs) > 0:
                X_hs = df_hs[route_features].values.astype(float)
                y_hs = df_hs["hs_weather_cancel"].values.astype(int)
                entry["hs"] = train_logistic(X_hs, y_hs, route_id, "hs_weather_cancel",
                                             features=route_features)
            else:
                print("    データなし")
                entry["hs"] = None

        # ── フェリー欠航モデル: AUC低（0.644）のため廃止。HSモデルで代替。──
        # route6の ferry_weather_cancel は欠航率68%と異常に高く、
        # 気象以外の要因（定期メンテ等）が混在している可能性があるため除外。

        all_models[route_id] = entry

    # ── JSON 保存 ──────────────────────────────────────────
    print(f"\n\nJSON 保存: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_models, f, ensure_ascii=False, indent=2)

    # ── サマリー ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"{'Route':<8} {'対象':<6} {'種別':<6} {'n':>6} {'欠航率':>7} {'スコア':>8}  評価")
    print("-" * 58)
    for rid, route_data in all_models.items():
        m = route_data.get("hs")
        if not m:
            continue
        mtype = m.get("model_type", "logistic")
        if mtype == "rule":
            f1 = max(m.get("f1_wave", 0), m.get("f1_wind", 0))
            quality = "📏" if f1 >= 0.40 else "⚠"
            print(f"{rid:<8} {'hs':<6} {'rule':<6} {'—':>6} {'—':>7} {f1:>8.3f}  {quality} (F1)")
        else:
            quality = ("✅" if m["cv_auc"] >= 0.80
                       else "⚠" if m["cv_auc"] >= 0.65
                       else "❌")
            print(f"{rid:<8} {'hs':<6} {'lr':<6} {m['train_samples']:>6,} "
                  f"{m['cancellation_rate']:>7.1%} {m['cv_auc']:>8.3f}  {quality} (AUC)")
    print("=" * 60)
    print(f"\n保存完了: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
