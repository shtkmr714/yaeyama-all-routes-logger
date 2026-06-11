"""
yaeyama_model_trainer.py
航路別 波高単独 欠航予測モデルの学習スクリプト。

2026-06 改訂: 特徴量選択分析の結論に基づき「波高単独モデル」へ全面移行。
  うねり・風速は波高と強く相関（r=0.82〜0.89）し多変量で有意でない。
  旧3変数ロジスティックは多重共線性で係数符号が反転する航路があった
  （route7: 波高係数マイナス）。波高単独で全航路 CV-AUC が同等以上を確認済み。

各航路について欠航% = 1/(1+exp(-k*(wave - x0))) を最尤推定し、
yaeyama_cancel_model.json に model_type="wave_logistic" として保存する。

route2（小浜島）は波高・他変数いずれも欠航と無相関（AUC≈0.55）のため
モデル化を見送り（データ蓄積待ち）。entry["hs"]=None。

Output: yaeyama_cancel_model.json（リポジトリに commit される）
"""

import os
import json
import math
import warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")

# ============================================================
# 定数
# ============================================================

SHEET_NAME  = "yaeyama_operation_log"
OUTPUT_FILE = "yaeyama_cancel_model.json"

ROUTE_CONFIGS = {
    "route1": "大原（西表島東）",
    "route2": "小浜島",
    "route3": "竹富島",
    "route4": "黒島",
    "route5": "上原（西表島北）",
    "route6": "波照間島",
    "route7": "鳩間島",
}

# route2 はデータ蓄積待ちのためモデル化しない
SKIP_ROUTES = {"route2"}

MIN_POSITIVE = 10    # 正例がこれ未満ならモデル構築をスキップ


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

def _fit_wave_mle(wave, y):
    """波高単独ロジスティックを最尤推定（class_weightなし＝確率を実欠航率に較正）。
    戻り値 (x0, k)。x0=50%到達波高(m), k=急峻さ。"""
    wave = np.asarray(wave, float); y = np.asarray(y, int)
    def nll(p):
        z = np.clip(p[1] * (wave - p[0]), -30, 30)
        pr = np.clip(1 / (1 + np.exp(-z)), 1e-7, 1 - 1e-7)
        return -np.sum(y * np.log(pr) + (1 - y) * np.log(1 - pr))
    res = minimize(nll, [2.5, 3.0], method="Nelder-Mead",
                   options={"xatol": 1e-4, "fatol": 1e-4})
    return float(res.x[0]), float(res.x[1])


def _cv_auc_wave(wave, y, k=5):
    """波高単独モデルの層化k分割CV-AUC"""
    wave = np.asarray(wave, float); y = np.asarray(y, int)
    npos = int(y.sum()); nneg = len(y) - npos
    k = max(2, min(k, npos, nneg))
    if k < 2:
        return None, None
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=42)
    aucs = []
    for tr, te in skf.split(wave, y):
        if len(set(y[te])) < 2:
            continue
        x0, kk = _fit_wave_mle(wave[tr], y[tr])
        z = np.clip(kk * (wave[te] - x0), -30, 30)
        pr = 1 / (1 + np.exp(-z))
        aucs.append(roc_auc_score(y[te], pr))
    if not aucs:
        return None, None
    return float(np.mean(aucs)), float(np.std(aucs))


def train_wave_only(wave, y, route_id):
    """
    波高単独ロジスティックモデルを学習し JSON 保存用 dict を返す。
    """
    wave = np.asarray(wave, float); y = np.asarray(y, int)
    n_pos = int(y.sum()); n_total = len(y)
    cancel_rate = n_pos / n_total if n_total else 0.0
    print(f"    サンプル: {n_total:,}  正例（欠航）: {n_pos}  欠航率: {cancel_rate:.1%}")

    if n_pos < MIN_POSITIVE:
        print(f"    ⚠ 正例数不足（{n_pos} < {MIN_POSITIVE}）→ スキップ")
        return None

    x0, kk = _fit_wave_mle(wave, y)
    cv_auc, cv_std = _cv_auc_wave(wave, y)
    print(f"    変曲点(50%)={x0:.2f}m  急峻さ={kk:.2f}  "
          f"CV-AUC={cv_auc:.3f}±{cv_std:.3f}" if cv_auc is not None
          else f"    変曲点(50%)={x0:.2f}m  急峻さ={kk:.2f}")
    for w in (2.0, 2.5, 3.0, 3.5):
        z = max(-30, min(30, kk * (w - x0)))
        print(f"      波{w:.1f}m → {round(100/(1+math.exp(-z)))}%")

    return {
        "model_type":        "wave_logistic",
        "route_id":          route_id,
        "target":            "hs_weather_cancel",
        "features":          ["wave_max"],
        "wave_inflection":   round(x0, 4),
        "wave_steepness":    round(kk, 4),
        "train_samples":     n_total,
        "positive_samples":  n_pos,
        "cancellation_rate": round(cancel_rate, 4),
        "cv_auc":            round(cv_auc, 4) if cv_auc is not None else None,
        "cv_auc_std":        round(cv_std, 4) if cv_std is not None else None,
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

        entry = {"route_name": route_name}

        # route2 はデータ蓄積待ちのためモデル化しない
        if route_id in SKIP_ROUTES:
            print(f"  → スキップ（波高と欠航が無相関・データ蓄積待ち）")
            entry["hs"] = None
            all_models[route_id] = entry
            continue

        df_r = df[df["route_id"] == route_id].copy()
        # 波高あり・hs_bins_count>0・dock/equipment除外 の行に限定
        df_hs = df_r[df_r["wave_max"].notna()]
        df_hs = df_hs[df_hs["hs_bins_count"].fillna(0) > 0]
        df_hs = df_hs.dropna(subset=["hs_weather_cancel"])
        if "hs_cancel_reason" in df_hs.columns:
            df_hs = df_hs[~df_hs["hs_cancel_reason"].astype(str).str.lower()
                          .isin(["dock", "equipment"])]
        print(f"  分析対象行（波高あり・weather対象）: {len(df_hs):,}")

        print(f"\n  [HS 欠航モデル（波高単独）]")
        if len(df_hs) > 0:
            wave = df_hs["wave_max"].values.astype(float)
            y_hs = df_hs["hs_weather_cancel"].values.astype(int)
            entry["hs"] = train_wave_only(wave, y_hs, route_id)
        else:
            print("    データなし")
            entry["hs"] = None

        all_models[route_id] = entry

    # ── JSON 保存 ──────────────────────────────────────────
    print(f"\n\nJSON 保存: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_models, f, ensure_ascii=False, indent=2)

    # ── サマリー ──────────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"{'Route':<8} {'n':>6} {'欠航率':>7} {'変曲点':>7} {'急峻さ':>6} {'CV-AUC':>8}  評価")
    print("-" * 64)
    for rid, route_data in all_models.items():
        m = route_data.get("hs")
        if not m:
            print(f"{rid:<8} {'—':>6} {'—':>7} {'—':>7} {'—':>6} {'—':>8}  （モデルなし）")
            continue
        auc = m.get("cv_auc") or 0
        quality = "✅" if auc >= 0.80 else ("⚠" if auc >= 0.65 else "❌")
        print(f"{rid:<8} {m['train_samples']:>6,} {m['cancellation_rate']:>7.1%} "
              f"{m['wave_inflection']:>6.2f}m {m['wave_steepness']:>6.2f} "
              f"{auc:>8.3f}  {quality}")
    print("=" * 64)
    print(f"\n保存完了: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
