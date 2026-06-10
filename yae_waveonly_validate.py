"""
yae_waveonly_validate.py
八重山 全7航路について「波高単独モデル」の精度を交差検証で検証し、
現行モデル（logistic 3変数 / rule）と比較する。本実装前の検証用。
"""
import os, json, math, warnings
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
warnings.filterwarnings("ignore")
JST = ZoneInfo("Asia/Tokyo")

ROUTES = {
    "route1":"大原","route2":"小浜","route3":"竹富","route4":"黒島",
    "route5":"上原","route6":"波照間","route7":"鳩間",
}

def connect():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds)
def to_f(v):
    try: return float(v)
    except: return None

def fit_wave(wave,y):
    from scipy.optimize import minimize
    wave=np.array(wave,float); y=np.array(y,int)
    def nll(p):
        z=np.clip(p[1]*(wave-p[0]),-30,30); pr=np.clip(1/(1+np.exp(-z)),1e-7,1-1e-7)
        return -np.sum(y*np.log(pr)+(1-y)*np.log(1-pr))
    r=minimize(nll,[2.5,3.0],method="Nelder-Mead",options={"xatol":1e-4,"fatol":1e-4})
    return r.x

def cv_auc_wave(wave, y, k=5):
    """波高単独モデルの層化k分割CV-AUC"""
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score
    wave=np.array(wave,float); y=np.array(y,int)
    if y.sum()<k or (len(y)-y.sum())<k: k=max(2,min(y.sum(),len(y)-y.sum()))
    if k<2: return None
    skf=StratifiedKFold(n_splits=k,shuffle=True,random_state=42)
    aucs=[]
    for tr,te in skf.split(wave,y):
        if len(set(y[te]))<2: continue
        x0,kk=fit_wave(wave[tr],y[tr])
        pr=1/(1+np.exp(-np.clip(kk*(wave[te]-x0),-30,30)))
        aucs.append(roc_auc_score(y[te],pr))
    return (np.mean(aucs),np.std(aucs),len(aucs)) if aucs else None

def cv_auc_multi(X, y, k=5):
    """3変数ロジスティックのCV-AUC（現行相当）"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score
    X=np.array(X,float); y=np.array(y,int)
    if y.sum()<k or (len(y)-y.sum())<k: k=max(2,min(y.sum(),len(y)-y.sum()))
    if k<2: return None
    skf=StratifiedKFold(n_splits=k,shuffle=True,random_state=42)
    aucs=[]
    for tr,te in skf.split(X,y):
        if len(set(y[te]))<2: continue
        sc=StandardScaler().fit(X[tr])
        m=LogisticRegression(class_weight="balanced",max_iter=1000).fit(sc.transform(X[tr]),y[tr])
        pr=m.predict_proba(sc.transform(X[te]))[:,1]
        aucs.append(roc_auc_score(y[te],pr))
    return (np.mean(aucs),np.std(aucs)) if aucs else None

if __name__=="__main__":
    print(f"八重山 波高単独モデル 精度検証  {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    gc=connect()
    rows=gc.open_by_key(os.environ["GOOGLE_SHEETS_ID_YAEYAMA"]).worksheet("yaeyama_operation_log").get_all_records()

    by_route={r:[] for r in ROUTES}
    for r in rows:
        rid=str(r.get("route_id",""))
        if rid not in by_route: continue
        wave=to_f(r.get("wave_max")); wind=to_f(r.get("wind_max")); swell=to_f(r.get("swell_max"))
        hsw=r.get("hs_weather_cancel")
        if wave is None or wind is None or hsw in (None,""): continue
        if str(r.get("hs_cancel_reason","none")).lower() in ("dock","equipment"): continue
        by_route[rid].append({"wave":wave,"swell":swell or 0.0,"wind":wind,"y":1 if str(hsw)=="1" else 0})

    print(f"\n{'航路':<14}{'n':>6}{'欠航':>6}{'欠航率':>7}  {'波単独CV-AUC':>16}  {'3変数CV-AUC':>14}  判定")
    print("-"*78)
    summary=[]
    for rid,nm in ROUTES.items():
        v=by_route[rid]; n=len(v); pos=sum(d["y"] for d in v)
        if n<10 or pos<5:
            print(f"{rid} {nm:<8}{n:>6}{pos:>6}{'-':>7}  {'データ不足':>16}")
            continue
        wave=[d["wave"] for d in v]; y=[d["y"] for d in v]
        X=[[d["wave"],d["swell"],d["wind"]] for d in v]
        w=cv_auc_wave(wave,y); m=cv_auc_multi(X,y)
        x0,kk=fit_wave(wave,y)
        wtxt=f"{w[0]:.3f}±{w[1]:.3f}" if w else "—"
        mtxt=f"{m[0]:.3f}±{m[1]:.3f}" if m else "—"
        # 判定: 波単独が3変数と同等以上(-0.02許容)なら波単独で十分
        verdict=""
        if w and m:
            diff=w[0]-m[0]
            verdict = "波単独で十分" if diff>=-0.02 else f"3変数優位({diff:+.3f})"
        print(f"{rid} {nm:<8}{n:>6}{pos:>6}{pos/n:>6.0%}  {wtxt:>16}  {mtxt:>14}  {verdict}")
        summary.append((rid,nm,x0,kk,n,pos,w,m))

    print(f"\n{'='*78}")
    print("  波高単独フィット結果（航路別の変曲点・急峻さ）")
    print(f"{'='*78}")
    for rid,nm,x0,kk,n,pos,w,m in summary:
        print(f"\n  {rid} {nm}: 変曲点(50%)={x0:.2f}m  急峻さ={kk:.2f}")
        for ww in [2.0,2.5,3.0,3.5,4.0]:
            print(f"      波{ww:.1f}m → {round(100/(1+math.exp(-kk*(ww-x0)))):3d}%", end="")
        print()
