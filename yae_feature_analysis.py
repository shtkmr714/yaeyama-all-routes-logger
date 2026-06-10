"""
yae_feature_analysis.py
八重山の欠航予測について、データ妥当性＋特徴量分析を行う。
1. 有効サンプル数（気象あり×実績あり）を航路別・全体で集計
2. 十分なら: 単変量AUC / 相関 / VIF / 多変量有意性 / 波高単独フィット
八重山は wind_dir_dominant（風向）も記録されているため風向の増分価値も確認する。
"""
import os, json, math
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
JST = ZoneInfo("Asia/Tokyo")

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

def auc(x,y):
    x=np.array(x,float); y=np.array(y,int)
    pos=x[y==1]; neg=x[y==0]
    if len(pos)==0 or len(neg)==0: return float("nan")
    w=sum(np.sum(p>neg)+0.5*np.sum(p==neg) for p in pos)
    return w/(len(pos)*len(neg))
def corr(a,b):
    a=np.array(a,float); b=np.array(b,float)
    if a.std()==0 or b.std()==0: return float("nan")
    return float(np.corrcoef(a,b)[0,1])
def fit_wave(wave,y):
    from scipy.optimize import minimize
    wave=np.array(wave,float); y=np.array(y,int)
    def nll(p):
        z=np.clip(p[1]*(wave-p[0]),-30,30); pr=np.clip(1/(1+np.exp(-z)),1e-7,1-1e-7)
        return -np.sum(y*np.log(pr)+(1-y)*np.log(1-pr))
    return minimize(nll,[2.0,4.0],method="Nelder-Mead",options={"xatol":1e-4,"fatol":1e-4}).x

if __name__=="__main__":
    print(f"八重山 特徴量分析  {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    gc=connect()
    rows=gc.open_by_key(os.environ["GOOGLE_SHEETS_ID_YAEYAMA"]).worksheet("yaeyama_operation_log").get_all_records()
    print(f"  総行数 {len(rows)}")

    recs=[]
    for r in rows:
        wave=to_f(r.get("wave_max"))
        if wave is None: continue   # 気象データありに限定
        # 高速船の気象欠航フラグ
        hsw=r.get("hs_weather_cancel")
        if hsw in (None,""): continue
        recs.append({
            "route":str(r.get("route_id","")),
            "wave":wave,"swell":to_f(r.get("swell_max")) or 0.0,
            "wind":to_f(r.get("wind_max")),
            "speriod":to_f(r.get("swell_period_max")),
            "wdir":to_f(r.get("wind_dir_dominant")),
            "hs_reason":str(r.get("hs_cancel_reason","none")).lower(),
            "y":1 if str(hsw)=="1" else 0,
        })
    recs=[r for r in recs if r["wind"] is not None]
    print(f"  有効サンプル（気象×実績）: {len(recs)} 行")

    # 航路別の集計
    print(f"\n  --- 航路別 サンプル数・気象欠航数 ---")
    routes={}
    for r in recs:
        routes.setdefault(r["route"],{"n":0,"c":0})
        routes[r["route"]]["n"]+=1; routes[r["route"]]["c"]+=r["y"]
    for rid in sorted(routes):
        v=routes[rid]
        print(f"    {rid}: n={v['n']:4d}  気象欠航={v['c']:3d}日  欠航率={v['c']/v['n']:.0%}")

    # dock/equip除外して分析
    v=[r for r in recs if r["hs_reason"] not in ("dock","equipment")]
    y=[r["y"] for r in v]
    nev=sum(y)
    print(f"\n  分析対象（dock/equip除外）: n={len(v)}  気象欠航={nev}日")
    print(f"  EPV（1変数={nev} / 3変数={nev/3:.1f}, 目安≥10）")
    if nev < 20:
        print("  ⚠️ イベント数不足。安定した特徴量選択は困難。")

    wave=[r["wave"] for r in v]; swell=[r["swell"] for r in v]; wind=[r["wind"] for r in v]

    print(f"\n  --- 単変量AUC ---")
    for nm,x in [("波高",wave),("うねり",swell),("風速",wind)]:
        print(f"    {nm}: AUC={auc(x,y):.3f}")
    # 風向は周期量なのでsin/cosでAUCは出さず参考のみ
    speriod=[r["speriod"] for r in v if r["speriod"] is not None]
    if speriod and len(speriod)==len(v):
        print(f"    うねり周期: AUC={auc([r['speriod'] for r in v],y):.3f}")

    print(f"\n  --- 相関 ---")
    print(f"    波高×うねり: {corr(wave,swell):+.2f}")
    print(f"    波高×風速:   {corr(wave,wind):+.2f}")

    # 多変量（statsmodels）
    try:
        import statsmodels.api as sm
        X=np.column_stack([wave,swell,wind]); Xs=(X-X.mean(0))/X.std(0)
        m=sm.Logit(np.array(y),sm.add_constant(Xs)).fit(disp=0,maxiter=200)
        print(f"\n  --- 多変量ロジスティック（標準化係数）---")
        for nm,c,p in zip(["切片","波高","うねり","風速"],m.params,m.pvalues):
            sig="***" if p<0.01 else("**" if p<0.05 else("*" if p<0.1 else " 有意でない"))
            print(f"    {nm:5s}: 係数={c:+.2f} p={p:.3f} {sig}")
        print(f"    McFadden R²={m.prsquared:.3f}")
    except Exception as e:
        print(f"  [多変量スキップ] {e}")

    # 波高単独フィット
    if nev>=10:
        x0,k=fit_wave(wave,y)
        print(f"\n  --- 波高単独フィット ---")
        print(f"    変曲点(50%)={x0:.2f}m  急峻さ={k:.2f}")
        for w in [1.5,2.0,2.5,3.0,3.5,4.0]:
            print(f"    波{w:.1f}m → {round(100/(1+math.exp(-k*(w-x0)))):3d}%")
        print(f"\n  実測（波高0.5m刻み）")
        b={}
        for r in v:
            bk=int(r["wave"]*2)/2; b.setdefault(bk,{"n":0,"c":0}); b[bk]["n"]+=1; b[bk]["c"]+=r["y"]
        for bk in sorted(b):
            if b[bk]["n"]<3: continue
            print(f"    波{bk:.1f}m: 実欠航率={b[bk]['c']/b[bk]['n']:.0%} (n={b[bk]['n']})")
