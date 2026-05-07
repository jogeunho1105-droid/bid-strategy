# ╔══════════════════════════════════════════════════════════════╗
# ║  투찰전략 분석 시스템 v2.0 — 5가지 개선 로드맵 전체 적용   ║
# ║  개선1: 발주처별 최적 가중치 (r5×0.25+r10×0.20+μ×0.55)   ║
# ║  개선2: 업체수 참고 표시 (보정 미적용 — 상관계수 낮음)     ║
# ║  개선3: 발주처 신뢰도 등급 A/B/C/D 표시                   ║
# ║  개선4: 기초금액 구간별 보정 안내                          ║
# ║  개선5: 6개월 drift 보정 자동 반영                        ║
# ╚══════════════════════════════════════════════════════════════╝

import streamlit as st
import pandas as pd
import numpy as np
import xlrd, io, os, json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime

plt.rcParams.update({"font.family":"DejaVu Sans","axes.unicode_minus":False})

st.set_page_config(page_title="투찰전략 분석 시스템", page_icon="📊", layout="wide")
st.markdown("""
<style>
.main-header{background:linear-gradient(135deg,#1a2744,#243260);color:white;
    padding:20px 30px;border-radius:10px;margin-bottom:20px}
.val-box{border-radius:8px;padding:10px 15px;font-weight:bold;
    font-size:1.1em;text-align:center;margin:4px 0}
.val-pattern{background:#dbeafe;color:#1d4ed8}
.val-similar{background:#dcfce7;color:#15803d}
.val-trend{background:#fef9c3;color:#854d0e}
.val-rec{background:#f3e8ff;color:#7c3aed}
.grade-a{background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:4px;font-weight:bold}
.grade-b{background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:4px;font-weight:bold}
.grade-c{background:#fef9c3;color:#854d0e;padding:2px 8px;border-radius:4px;font-weight:bold}
.grade-d{background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;font-weight:bold}
</style>""", unsafe_allow_html=True)

DATA_DIR     = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "history.pkl")
PATTERN_FILE = os.path.join(DATA_DIR, "pattern_stats.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ── 개선4: 기초금액 구간별 보정값 ──────────────────────────────
AMT_BRACKETS = {
    "~0.5억":  {"range":(0,   0.5),  "adj": +0.0013, "note":"소형"},
    "0.5~1억": {"range":(0.5, 1.0),  "adj": +0.0045, "note":"소형"},
    "1~2억":   {"range":(1.0, 2.0),  "adj": +0.0424, "note":"유리 ↑"},
    "2~5억":   {"range":(2.0, 5.0),  "adj": -0.0295, "note":"보수적 ↓"},
    "5~10억":  {"range":(5.0, 10.0), "adj": +0.0398, "note":"대형"},
    "10억+":   {"range":(10.0,9999), "adj": +0.0373, "note":"대형"},
}

def get_amt_info(base_억):
    for label, info in AMT_BRACKETS.items():
        lo, hi = info["range"]
        if lo <= base_억 < hi:
            return label, info["adj"], info["note"]
    return "미정", 0.0, ""

# ── 낙찰이력 ──────────────────────────────────────────────────
@st.cache_data
def load_history():
    if os.path.exists(HISTORY_FILE):
        return pd.read_pickle(HISTORY_FILE)
    return None

@st.cache_data
def load_pattern_stats():
    if os.path.exists(PATTERN_FILE):
        with open(PATTERN_FILE) as f:
            return json.load(f)
    return {}

def save_history(df):
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_pickle(HISTORY_FILE)
    st.cache_data.clear()

def save_pattern_stats(stats):
    with open(PATTERN_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    st.cache_data.clear()

# ── 개선1+3+5: 패턴 분석 (최적가중치 + 신뢰도 + drift 보정) ──
def analyze_pattern(org, df_c, pattern_stats):
    """개선1: 최적가중치 / 개선3: 신뢰도등급 / 개선5: drift보정"""
    # 저장된 패턴통계 우선 사용
    if org in pattern_stats:
        st = pattern_stats[org]
        return {
            "pred":         st.get("pred", 0),
            "conservative": st.get("conservative", 0),
            "aggressive":   st.get("aggressive", 0),
            "trend":        st.get("trend", "→횡보"),
            "pattern":      st.get("pattern", "무작위패턴").replace("패턴",""),
            "autocorr":     st.get("autocorr", 0),
            "last_val":     st.get("last_val", 0),
            "r5":           st.get("r5", 0),
            "r10":          st.get("r10", 0),
            "mean":         st.get("mean", 0),
            "std":          st.get("std", 0.5),
            "n":            st.get("n", 0),
            "w5":           st.get("w5", 0.25),
            "w10":          st.get("w10", 0.20),
            "wm":           st.get("wm", 0.55),
            "grade":        st.get("grade", "C"),
            "mae":          st.get("mae", 0.5),
            "drift6m":      st.get("drift6m", 0),
            "trend_boost":  st.get("trend_boost", 0),
            "drift_note":   st.get("drift_note", ""),
            "recent10":     st.get("recent10", []),
            "all_vals":     None,
            "source":       "패턴통계DB"
        }

    # 직접 계산 (패턴통계 없는 발주처)
    if df_c is None: return None
    sub = df_c[df_c["발주기관"]==org]["예가/기초(0%)"].values
    if len(sub) < 5: return None
    n = len(sub)
    mean = np.mean(sub); std = np.std(sub)
    r5   = np.mean(sub[-5:])
    r10  = np.mean(sub[-10:]) if n>=10 else mean
    ac   = float(np.corrcoef(sub[:-1],sub[1:])[0,1]) if n>=3 else 0
    coef = float(np.polyfit(np.arange(min(20,n)),sub[-min(20,n):],1)[0])
    trend   = "↑상승" if coef>0.02 else "↓하락" if coef<-0.02 else "→횡보"
    pattern = "연속성" if ac>0.2 else "반전" if ac<-0.2 else "무작위"
    # 개선1: 최적 가중치 적용
    w5,w10,wm = 0.25,0.20,0.55
    pred = w5*r5 + w10*r10 + wm*mean
    lv = float(sub[-1])
    adj = (lv*abs(ac)*0.2 if pattern=="연속성" else
           -lv*abs(ac)*0.3 if pattern=="반전" else 0.0)
    pred_final = round(pred+adj, 4)
    # 개선3: 신뢰도 등급
    errs = [abs((0.25*np.mean(sub[:i][-5:])+0.20*(np.mean(sub[:i][-10:]) if i>=10 else np.mean(sub[:i]))+0.55*np.mean(sub[:i]))-sub[i])
            for i in range(min(10,n//2),n)]
    mae = np.mean(errs) if errs else 0.5
    grade = "A" if mae<0.35 else "B" if mae<0.45 else "C" if mae<0.55 else "D"
    return {
        "pred": pred_final, "conservative": round(pred_final-std*0.4,4),
        "aggressive": round(pred_final+std*0.4,4),
        "trend": trend, "pattern": pattern, "autocorr": round(ac,4),
        "last_val": round(lv,4), "r5": round(r5,4), "r10": round(r10,4),
        "mean": round(mean,4), "std": round(std,4), "n": n,
        "w5": w5, "w10": w10, "wm": wm,
        "grade": grade, "mae": round(mae,4),
        "drift6m": 0.0, "trend_boost": 0.0, "drift_note": "",
        "recent10": [round(float(v),4) for v in sub[-10:]],
        "all_vals": sub.tolist(), "source": "직접계산"
    }

def analyze_similar(name, base_원, df_c):
    """② 유사표본 분석"""
    if df_c is None or base_원<=0: return None
    kws = [kw for kw in ["PD","VLF","감리","진단","설계","측정","PQ"] if kw in name]
    if not kws: kws = ["감리"]
    mask = pd.Series([False]*len(df_c), index=df_c.index)
    for kw in kws: mask = mask | df_c["공고명"].str.contains(kw, na=False)
    sim = df_c[mask&(df_c["기초금액"]>=base_원*0.5)&(df_c["기초금액"]<=base_원*1.5)]
    if len(sim)<3:
        sim = df_c[mask&(df_c["기초금액"]>=base_원*0.3)&(df_c["기초금액"]<=base_원*2.0)]
    if len(sim)<3: return None
    vals = sim["예가/기초(0%)"].values; n = len(vals)
    weights = np.linspace(0.5, 1.5, n)
    co = sim["업체수"].mean() if "업체수" in sim.columns else None
    return {
        "pred": round(float(np.average(vals,weights=weights)),4),
        "n": n, "mean": round(float(np.mean(vals)),4),
        "std": round(float(np.std(vals)),4),
        "avg_companies": round(float(co),1) if co else None,
        "keywords": kws
    }

def analyze_trend(org, df_c):
    """③ 최근 트렌드 분석"""
    if df_c is None: return None
    sub = df_c[df_c["발주기관"]==org]
    vals = sub["예가/기초(0%)"].values
    if len(vals)<5: return None
    rn = max(5,len(vals)//4); recent=vals[-rn:]; older=vals[:-rn]
    rm = float(np.mean(recent)); om = float(np.mean(older)) if len(older)>0 else rm
    drift = rm-om; r3 = vals[-3:] if len(vals)>=3 else vals
    co = sub["업체수"].tail(rn).mean() if "업체수" in sub.columns else None
    return {
        "pred": round(rm+drift*0.3,4), "recent_mean": round(rm,4),
        "drift": round(drift,4), "recent_n": rn,
        "recent3_mean": round(float(np.mean(r3)),4),
        "avg_companies": round(float(co),1) if co else None
    }

def recommend_range(a1, a2, a3):
    vals = [v["pred"] for v in [a1,a2,a3] if v]
    if not vals: return None, None
    mv=np.mean(vals); sv=np.std(vals) if len(vals)>1 else 0.1
    return round(mv-sv*0.5,4), round(mv+sv*0.5,4)

def convergence_score(a1, a2, a3):
    """3가지 예측값 수렴도 계산 — 개선3 신뢰도 지표"""
    vals = [v["pred"] for v in [a1,a2,a3] if v]
    if len(vals)<2: return None, "데이터부족"
    std_v = np.std(vals)
    if std_v < 0.05:   return std_v, "★★★ 높음"
    elif std_v < 0.10: return std_v, "★★☆ 보통"
    elif std_v < 0.20: return std_v, "★☆☆ 낮음"
    else:               return std_v, "⚠️ 분산큼"

def parse_xls(file_bytes):
    wb = xlrd.open_workbook(file_contents=file_bytes, ignore_workbook_corruption=True)
    ws = wb.sheets()[0]; headers=[ws.cell_value(1,c) for c in range(ws.ncols)]
    bids=[]
    for r in range(2,ws.nrows):
        row={headers[c]:ws.cell_value(r,c) for c in range(ws.ncols)}
        if not row.get("번호"): continue
        base=float(row.get("기초금액") or 0)
        bids.append({"no":int(row["번호"]),"name":row.get("공고명",""),
                     "bid_no":row.get("공고번호",""),"base":base,
                     "base_억":round(base/1e8,4) if base else 0,
                     "deadline":row.get("투찰마감",""),"org":row.get("발주기관",""),
                     "region":row.get("지역","")})
    return bids

# ── 영문 변환 ─────────────────────────────────────────────────
def tr_trend(t):
    return {"↑상승":"Up","↓하락":"Down","→횡보":"Flat"}.get(t,t)
def tr_pattern(p):
    return {"연속성":"Momentum","반전":"Reversal","무작위":"Random"}.get(p,p)
def tr_org(org):
    return (org.replace("한국전력공사 ","KEPCO ").replace("본부","")
               .replace("한국철도공사 회계통합센터","KORAIL")
               .replace("조달청","PPS").replace("국군재정관리단","MND"))

# ── 흐름 차트 ─────────────────────────────────────────────────
def make_flow_chart(a1, a2, a3, lo, hi, org_raw):
    all_v = (a1.get("all_vals") or []) if a1 else []
    if not all_v:
        # recent10만 있을 때
        all_v = a1.get("recent10",[]) if a1 else []
    if not all_v: return None

    org_en = tr_org(org_raw)
    show_n = min(30,len(all_v)); recent=all_v[-show_n:]
    x=np.arange(1,show_n+1); next_x=show_n+1; mean_v=a1["mean"]

    fig=plt.figure(figsize=(13,5.5),facecolor="#f8fafc")
    gs=fig.add_gridspec(2,1,height_ratios=[2.6,1.0],hspace=0.04)
    ax=fig.add_subplot(gs[0]); ax_l=fig.add_subplot(gs[1])
    ax.set_facecolor("#ffffff"); ax_l.set_facecolor("#f8fafc"); ax_l.axis("off")

    ax.axhline(0,color="#94a3b8",lw=1.0,alpha=0.8,zorder=1)
    if lo is not None and hi is not None:
        ax.axhspan(lo,hi,alpha=0.12,color="#7c3aed",zorder=1)
        ax.axhline(lo,color="#7c3aed",lw=0.7,ls=":",alpha=0.5,zorder=1)
        ax.axhline(hi,color="#7c3aed",lw=0.7,ls=":",alpha=0.5,zorder=1)
    ax.axhline(mean_v,color="#f59e0b",lw=1.3,ls="--",alpha=0.85,zorder=2)
    ma5=[np.mean(recent[max(0,i-4):i+1]) for i in range(show_n)]
    ax.plot(x,ma5,color="#6366f1",lw=1.6,ls="--",alpha=0.75,zorder=3)
    bar_c=["#10b981" if v>=0 else "#ef4444" for v in recent]
    ax.bar(x,recent,color=bar_c,alpha=0.42,width=0.65,zorder=2)
    ax.plot(x,recent,color="#1a2744",lw=1.6,marker="o",ms=3.5,zorder=4)
    for i in range(max(0,show_n-10),show_n):
        v=recent[i]
        ax.annotate(f"{v:+.3f}",xy=(x[i],v),xytext=(0,7 if v>=0 else -11),
                    textcoords="offset points",ha="center",fontsize=6.5,
                    color="#059669" if v>=0 else "#dc2626",fontweight="bold")
    ax.axvline(show_n+0.5,color="#7c3aed",lw=1.4,ls=":",alpha=0.75)

    preds=[]
    if a1: preds.append(("(1)Pattern",a1["pred"],"#1d4ed8","D",10))
    if a2: preds.append(("(2)Similar",a2["pred"],"#15803d","s",10))
    if a3: preds.append(("(3)Trend",  a3["pred"],"#92400e","^",10))
    xoff=[-0.32,0.0,0.32]
    for idx,(lbl,pv,pc,mk,ms) in enumerate(preds):
        px=next_x+xoff[idx]
        ax.plot([px],[pv],color=pc,marker=mk,ms=ms,zorder=7,
                markeredgecolor="white",markeredgewidth=1.1)
        ax.annotate(f"{pv:+.4f}%",xy=(px,pv),xytext=(20,0),
                    textcoords="offset points",ha="left",fontsize=8,color=pc,
                    fontweight="bold",arrowprops=dict(arrowstyle="->",color=pc,lw=1.1))
    if lo is not None and hi is not None:
        bx=next_x+1.3
        ax.annotate("",xy=(bx,lo),xytext=(bx,hi),
                    arrowprops=dict(arrowstyle="<->",color="#7c3aed",lw=2.0))
        ax.text(bx+0.15,(lo+hi)/2,f"Rec.\n{lo:+.4f}\n~{hi:+.4f}",
                fontsize=7.5,color="#7c3aed",fontweight="bold",va="center",ha="left",
                bbox=dict(boxstyle="round,pad=0.3",fc="#f3e8ff",ec="#7c3aed",alpha=0.9))
    ax_r=ax.twinx(); ax_r.set_ylim(ax.get_ylim())
    tks=[mean_v]; tlbls=[f"Avg:{mean_v:+.3f}"]
    if lo is not None: tks+=[lo,hi]; tlbls+=[f"Lo:{lo:+.3f}",f"Hi:{hi:+.3f}"]
    ax_r.set_yticks(tks); ax_r.set_yticklabels(tlbls,fontsize=7,color="#64748b")
    ax.set_xlim(0.3,next_x+2.8)
    ax.set_xticks(list(x)+[next_x])
    ax.set_xticklabels([f"-{show_n-i}" for i in range(show_n)]+["Pred"],fontsize=7)
    ax.set_ylabel("Pred/Base(0%) %",fontsize=8)
    ax.tick_params(labelsize=7.5); ax.grid(axis="y",alpha=0.18,ls="--")

    # 개선3: 신뢰도등급 타이틀에 표시
    grade = a1.get("grade","?"); mae = a1.get("mae",0)
    grade_str = f" | Grade:{grade}(MAE:{mae:.3f}%)"
    drift_str = f" | Drift:{a1.get('drift6m',0):+.4f}" if abs(a1.get('drift6m',0))>0.03 else ""
    ax.set_title(
        f"{org_en}  |  Last {show_n} results  |  Trend:{tr_trend(a1['trend'])}"
        f"  |  Pattern:{tr_pattern(a1['pattern'])}  |  n={a1['n']}"
        f"{grade_str}{drift_str}",
        fontsize=9,fontweight="bold",color="#1a2744",pad=8)

    # 범례 패널
    ax_l.set_xlim(0,1); ax_l.set_ylim(0,1)
    ax_l.add_patch(mpatches.FancyBboxPatch(
        (0.005,0.03),0.990,0.94,boxstyle="round,pad=0.01",
        facecolor="#ffffff",edgecolor="#cbd5e1",linewidth=1.0,
        transform=ax_l.transAxes))
    col_defs=[(0.01,"Chart Legend","#1d4ed8"),(0.265,"Prediction","#15803d"),
              (0.515,"Pattern Detail","#7c3aed"),(0.765,"Market Info","#92400e")]
    for cx,htxt,hcol in col_defs:
        ax_l.add_patch(mpatches.FancyBboxPatch(
            (cx+0.002,0.80),0.238,0.16,boxstyle="round,pad=0.005",
            facecolor=hcol,alpha=0.12,edgecolor="none",transform=ax_l.transAxes))
        ax_l.text(cx+0.012,0.885,htxt,fontsize=9,fontweight="bold",
                  color=hcol,va="center",transform=ax_l.transAxes)
    for lx in [0.255,0.505,0.755]:
        ax_l.plot([lx,lx],[0.04,0.97],color="#e2e8f0",lw=1.0,transform=ax_l.transAxes)

    a2n=a2["n"] if a2 else "-"
    a2co=f"{a2['avg_companies']} firms" if a2 and a2.get('avg_companies') else "-"
    # 개선3: 신뢰도등급 / 개선5: drift 정보 추가
    drift_info = f"Drift:{a1.get('drift6m',0):+.4f}%" if abs(a1.get('drift6m',0))>0.03 else "Drift:stable"
    grade_color={"A":"#15803d","B":"#1d4ed8","C":"#854d0e","D":"#991b1b"}.get(grade,"#475569")

    items=[
        (0.01,"line","#1a2744","","Actual bid result",   f"Last {show_n} results"),
        (0.01,"line","#6366f1","","Moving Avg MA(5)",     "5-case moving average"),
        (0.01,"line","#f59e0b","","Overall Average",      f"Avg:{mean_v:+.4f}%"),
        (0.01,"band","#7c3aed","","Recommended Zone",     f"{lo:+.4f}% ~ {hi:+.4f}%" if lo else "-"),
        (0.265,"mark","#1d4ed8","D","(1) Pattern",        f"w={a1['w5']}/{a1['w10']}/{a1['wm']}  ->  {a1['pred']:+.4f}%"),
        (0.265,"mark","#15803d","s","(2) Similar",        f"n={a2n}  ->  {a2['pred']:+.4f}%" if a2 else "No data"),
        (0.265,"mark","#92400e","^","(3) Trend",          f"drift  ->  {a3['pred']:+.4f}%" if a3 else "No data"),
        (0.515,"dot","#7c3aed","","Last 5 avg (r5)",      f"{a1['r5']:+.4f}%"),
        (0.515,"dot","#7c3aed","","Last 10 avg (r10)",    f"{a1['r10']:+.4f}%"),
        (0.515,"dot","#7c3aed","","Previous result",      f"{a1['last_val']:+.4f}%"),
        (0.765,"dot",grade_color,"","Accuracy Grade",     f"Grade:{grade}  MAE:{mae:.3f}%"),
        (0.765,"dot","#92400e","","6M Drift",             drift_info),
        (0.765,"dot","#92400e","","Avg firms",            a2co),
    ]
    row_cnt={0.01:0,0.265:0,0.515:0,0.765:0}
    TOP_Y=0.72; ROW_GAP=0.225
    for (cx,itype,color,mk,label,desc) in items:
        ri=row_cnt[cx]; row_cnt[cx]+=1; y=TOP_Y-ri*ROW_GAP
        if itype=="line":
            ax_l.plot([cx+0.005,cx+0.038],[y+0.05,y+0.05],color=color,lw=2.4,
                      transform=ax_l.transAxes,clip_on=False)
        elif itype=="band":
            ax_l.add_patch(mpatches.FancyBboxPatch(
                (cx+0.005,y+0.02),0.033,0.065,boxstyle="round,pad=0.003",
                facecolor=color,alpha=0.28,edgecolor=color,lw=1.1,transform=ax_l.transAxes))
        elif itype=="mark":
            ax_l.plot([cx+0.022],[y+0.05],marker=mk,color=color,ms=9,
                      transform=ax_l.transAxes,clip_on=False,
                      markeredgecolor="white",markeredgewidth=1.3)
        elif itype=="dot":
            ax_l.plot([cx+0.022],[y+0.05],marker="o",color=color,ms=5.5,
                      transform=ax_l.transAxes,clip_on=False,alpha=0.75)
        ax_l.text(cx+0.048,y+0.100,label,fontsize=8.5,fontweight="bold",
                  color="#1e293b",va="top",transform=ax_l.transAxes)
        ax_l.text(cx+0.048,y+0.005,desc,fontsize=8,color="#475569",
                  va="top",transform=ax_l.transAxes)
    ax_l.plot([0.01,0.99],[0.045,0.045],color="#e2e8f0",lw=0.8,transform=ax_l.transAxes)
    plt.subplots_adjust(left=0.055,right=0.92,top=0.95,bottom=0.02)
    buf=io.BytesIO()
    plt.savefig(buf,format="png",dpi=140,bbox_inches="tight",facecolor="#f8fafc")
    buf.seek(0); plt.close(); return buf

# ── 엑셀 생성 ─────────────────────────────────────────────────
def make_excel(results):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
    from openpyxl.utils import get_column_letter
    NAVY="FF1a2744";BLUE="FFdbeafe";GREEN="FFdcfce7";AMBER="FFfef9c3"
    RED_L="FFfee2e2";PURP="FFf3e8ff";GRAY="FFf8fafc";TEAL="FFccfbf1"
    thin=Side(style="thin",color="FFd1d5db")
    bdr=Border(left=thin,right=thin,top=thin,bottom=thin)
    def H(ws,r,c,v,bg=NAVY,fg="FFFFFFFF",sz=10,bold=True,wrap=False):
        cell=ws.cell(row=r,column=c,value=v)
        cell.font=Font(name="맑은 고딕",bold=bold,color=fg,size=sz)
        cell.fill=PatternFill("solid",start_color=bg)
        cell.alignment=Alignment(horizontal="center",vertical="center",wrap_text=wrap)
        cell.border=bdr; return cell
    def C(ws,r,c,v,bg=None,bold=False,right=False,sz=10,color="FF1e293b",center=False,wrap=False):
        cell=ws.cell(row=r,column=c,value=v)
        cell.font=Font(name="맑은 고딕",bold=bold,size=sz,color=color)
        ha="right" if right else("center" if center else "left")
        cell.alignment=Alignment(horizontal=ha,vertical="center",wrap_text=wrap)
        cell.border=bdr
        if bg: cell.fill=PatternFill("solid",start_color=bg)
        return cell
    wb=Workbook(); ws=wb.active; ws.title="투찰전략"; ws.sheet_view.showGridLines=False
    today=datetime.now().strftime("%Y.%m.%d")
    ws.merge_cells("A1:N1"); t=ws["A1"]
    t.value=f"투찰전략 분석표 — {today}  ★ 5가지 개선 적용 v2.0"
    t.font=Font(name="맑은 고딕",bold=True,size=13,color="FF1a2744")
    t.fill=PatternFill("solid",start_color="FFe0e7ff")
    t.alignment=Alignment(horizontal="center",vertical="center")
    ws.row_dimensions[1].height=30
    hdrs=["No","공고명","발주기관","기초금액(억)","금액구간","마감",
          "①패턴(%)","②유사표본(%)","③트렌드(%)","권장하한(%)","권장상한(%)",
          "수렴도","등급","트렌드"]
    wids=[5,40,20,11,9,13,11,11,11,11,11,10,6,9]
    for i,(h,w) in enumerate(zip(hdrs,wids),1):
        H(ws,2,i,h,wrap=True); ws.column_dimensions[get_column_letter(i)].width=w
    ws.row_dimensions[2].height=36

    for i,row in enumerate(results):
        r=i+3; bg=GRAY if r%2==0 else "FFFFFFFF"
        b=row["bid"]; a1=row["a1"]; a2=row["a2"]; a3=row["a3"]
        lo,hi=row["range_lo"],row["range_hi"]
        conv_std,conv_lbl=row["conv_std"],row["conv_lbl"]
        amt_lbl,amt_adj,amt_note=row["amt_lbl"],row["amt_adj"],row["amt_note"]

        C(ws,r,1,b["no"],bg=bg,bold=True,center=True)
        C(ws,r,2,b["name"][:55],bg=bg,sz=9,wrap=True)
        C(ws,r,3,b["org"],bg=bg,sz=9)
        if b["base"]>0:
            cx=C(ws,r,4,b["base_억"],bg=bg,right=True); cx.number_format="#,##0.0000"
        else:
            C(ws,r,4,"미정",bg=bg,center=True,sz=9)
        # 개선4: 금액구간
        amt_bg=TEAL if "유리" in amt_note else RED_L if "보수" in amt_note else bg
        C(ws,r,5,f"{amt_lbl}\n{amt_note}",bg=amt_bg,center=True,sz=8,wrap=True)
        C(ws,r,6,b["deadline"],bg=bg,sz=9,center=True)
        for ci,a,cbg in [(7,a1,BLUE),(8,a2,GREEN),(9,a3,AMBER)]:
            if a:
                cx2=C(ws,r,ci,a["pred"],bg=cbg,right=True,bold=True,
                      color="FF1d4ed8" if a["pred"]>=0 else "FF991b1b")
                cx2.number_format="+0.0000;-0.0000"
            else:
                C(ws,r,ci,"없음",bg=RED_L,center=True,sz=8)
        for ci,val in [(10,lo),(11,hi)]:
            if val is not None:
                cx3=C(ws,r,ci,val,bg=PURP,right=True,bold=True,color="FF7c3aed")
                cx3.number_format="+0.0000;-0.0000"
            else:
                C(ws,r,ci,"-",bg=bg,center=True)
        # 수렴도
        conv_bg=TEAL if "높음" in conv_lbl else AMBER if "보통" in conv_lbl else RED_L
        C(ws,r,12,conv_lbl,bg=conv_bg,center=True,sz=8)
        # 개선3: 등급
        if a1:
            grade=a1.get("grade","?")
            grade_bg={"A":"FF dcfce7","B":"FFdbeafe","C":"FFfef9c3","D":"FFfee2e2"}.get(grade,bg).replace(" ","")
            C(ws,r,13,f"{grade}",bg=grade_bg,center=True,bold=True,sz=11,
              color={"A":"FF15803d","B":"FF1d4ed8","C":"FF854d0e","D":"FF991b1b"}.get(grade,"FF1e293b"))
            tc="FF15803d" if "상승" in a1["trend"] else "FF991b1b" if "하락" in a1["trend"] else "FF64748b"
            C(ws,r,14,a1["trend"],bg=bg,center=True,color=tc,bold=True)
        ws.row_dimensions[r].height=34

    # Sheet2: 투찰금액 환산
    ws2=wb.create_sheet("투찰금액 환산"); ws2.sheet_view.showGridLines=False
    hdrs2=["No","공고명","기초금액(원)","금액구간","①패턴 금액","②유사표본 금액",
           "③트렌드 금액","권장하한 금액","권장상한 금액","수렴도","등급"]
    wids2=[5,40,15,10,15,15,15,15,15,10,6]
    for i,(h,w) in enumerate(zip(hdrs2,wids2),1):
        H(ws2,1,i,h,wrap=True); ws2.column_dimensions[get_column_letter(i)].width=w
    ws2.row_dimensions[1].height=32
    for i,row in enumerate(results):
        r=i+2; bg=GRAY if r%2==0 else "FFFFFFFF"
        b=row["bid"]; base=b["base"]
        a1=row["a1"]; a2=row["a2"]; a3=row["a3"]
        lo,hi=row["range_lo"],row["range_hi"]
        C(ws2,r,1,b["no"],bg=bg,center=True,bold=True)
        C(ws2,r,2,b["name"][:55],bg=bg,sz=9,wrap=True)
        if base>0:
            cx=C(ws2,r,3,int(base),bg=bg,right=True); cx.number_format="#,##0"
            amt_lbl=row["amt_lbl"]; amt_note=row["amt_note"]
            amt_bg=TEAL if "유리" in amt_note else RED_L if "보수" in amt_note else bg
            C(ws2,r,4,f"{amt_lbl}({amt_note})",bg=amt_bg,center=True,sz=8)
            for ci,a,cbg in [(5,a1,BLUE),(6,a2,GREEN),(7,a3,AMBER)]:
                if a:
                    amt=int(base*(100+a["pred"])/100)
                    cx2=C(ws2,r,ci,amt,bg=cbg,right=True,bold=True); cx2.number_format="#,##0"
                else:
                    C(ws2,r,ci,"없음",bg=RED_L,center=True,sz=8)
            for ci,val in [(8,lo),(9,hi)]:
                if val is not None:
                    amt=int(base*(100+val)/100)
                    cx3=C(ws2,r,ci,amt,bg=PURP,right=True,bold=True); cx3.number_format="#,##0"
                else:
                    C(ws2,r,ci,"-",bg=bg,center=True)
        else:
            for ci in range(3,10): C(ws2,r,ci,"기초금액 미정",bg=bg,center=True,sz=8)
        C(ws2,r,10,row["conv_lbl"],bg=bg,center=True,sz=8)
        if a1:
            grade=a1.get("grade","?")
            grade_bg={"A":"FFdcfce7","B":"FFdbeafe","C":"FFfef9c3","D":"FFfee2e2"}.get(grade,bg)
            C(ws2,r,11,grade,bg=grade_bg,center=True,bold=True,sz=11)
        ws2.row_dimensions[r].height=28
    ws2.freeze_panes="A2"
    buf=io.BytesIO(); wb.save(buf); buf.seek(0); return buf

# ════════════════════════════════════════════════════════════════
#  메인 UI
# ════════════════════════════════════════════════════════════════
st.markdown("""
<div class="main-header">
<h2>📊 투찰전략 분석 시스템</h2>
<p style="margin:0;opacity:0.8">5가지 개선 로드맵 적용 | v2.0</p>
</div>""", unsafe_allow_html=True)

df_hist       = load_history()
pattern_stats = load_pattern_stats()
df_c = df_hist[df_hist["예가/기초(0%)"].notna()&(df_hist["예가/기초(0%)"].abs()<10)].copy() if df_hist is not None else None

with st.sidebar:
    st.header("⚙️ 시스템 설정")
    mode=st.radio("모드 선택",["📊 투찰전략 분석","🔧 배포자 관리"])
    st.divider()
    if df_c is not None:
        st.success(f"✅ 낙찰이력 로드\n{len(df_c):,}건 | {df_c['발주기관'].nunique()}개 발주처")
    else:
        st.warning("⚠️ 낙찰이력 없음")
    if pattern_stats:
        st.info(f"📊 패턴통계 로드\n{len(pattern_stats)}개 발주처")
    st.divider()
    # 개선3: 신뢰도 등급 범례
    st.markdown("**등급 기준 (MAE)**")
    st.markdown('<span class="grade-a">A</span> &lt;0.35% &nbsp; <span class="grade-b">B</span> &lt;0.45%',unsafe_allow_html=True)
    st.markdown('<span class="grade-c">C</span> &lt;0.55% &nbsp; <span class="grade-d">D</span> ≥0.55%',unsafe_allow_html=True)
    st.divider()
    # 개선4: 금액구간 참고
    st.markdown("**기초금액 구간별 사정율**")
    for lbl,info in AMT_BRACKETS.items():
        adj=info["adj"]; note=info["note"]
        color="green" if adj>0.02 else "red" if adj<-0.02 else "gray"
        st.markdown(f":{color}[{lbl}: {adj:+.4f}% {note}]")
    st.divider()
    st.caption(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ══ 배포자 관리 ══════════════════════════════════════════════════
if mode=="🔧 배포자 관리":
    st.header("🔧 배포자 관리")
    pwd=st.text_input("관리자 비밀번호",type="password")
    ADMIN_PWD=st.secrets.get("ADMIN_PWD","admin1234")
    if pwd!=ADMIN_PWD: st.info("비밀번호를 입력하세요."); st.stop()
    st.success("✅ 관리자 인증")

    tab1,tab2=st.tabs(["📂 낙찰이력 업로드","📊 패턴통계 현황"])
    with tab1:
        uploaded=st.file_uploader("낙찰이력 xlsx 업로드",type=["xlsx","xls"])
        if uploaded:
            with st.spinner("처리 중..."):
                try:
                    content=uploaded.read()
                    df_new=pd.read_excel(io.BytesIO(content))
                    required=["발주기관","공고명","기초금액","예가/기초(0%)"]
                    missing=[c for c in required if c not in df_new.columns]
                    if missing: st.error(f"필수 컬럼 없음: {missing}")
                    else:
                        save_history(df_new)
                        df_v=df_new[df_new["예가/기초(0%)"].notna()&(df_new["예가/기초(0%)"].abs()<10)]
                        st.success(f"✅ 업로드 완료! {len(df_v):,}건 / {df_v['발주기관'].nunique()}개 발주처")
                        c1,c2,c3=st.columns(3)
                        c1.metric("총 건수",f"{len(df_v):,}건")
                        c2.metric("발주처 수",f"{df_v['발주기관'].nunique()}개")
                        c3.metric("평균 사정율",f"{df_v['예가/기초(0%)'].mean():+.4f}%")
                        st.dataframe(df_v["발주기관"].value_counts().head(10).reset_index(),
                                     use_container_width=True,hide_index=True)
                except Exception as e: st.error(f"오류: {e}")
    with tab2:
        if pattern_stats:
            rows=[]
            for org,d in pattern_stats.items():
                rows.append({"발주기관":org,"건수":d.get("n",0),
                    "예측값(%)":f"{d.get('pred',0):+.4f}",
                    "등급":d.get("grade","?"),"MAE":f"{d.get('mae',0):.4f}",
                    "트렌드":d.get("trend","-"),"drift":f"{d.get('drift6m',0):+.4f}",
                    "가중치":f"r5×{d.get('w5',0.25)}/r10×{d.get('w10',0.20)}/μ×{d.get('wm',0.55)}"})
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        else:
            st.info("패턴통계 없음")

# ══ 투찰전략 분석 ════════════════════════════════════════════════
else:
    if df_c is None and not pattern_stats:
        st.error("낙찰이력 없음. 배포자에게 문의하세요."); st.stop()

    st.header("📊 투찰전략 분석")
    col_up,col_info=st.columns([2,1])
    with col_up:
        xls_file=st.file_uploader("입찰서류함 xls 파일 업로드",type=["xls","xlsx"])
    with col_info:
        n_c = len(df_c) if df_c is not None else 0
        n_o = df_c["발주기관"].nunique() if df_c is not None else 0
        st.markdown(f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px">
        <b>📌 v2.0 개선 적용</b><br>
        🔵 ①패턴: 최적가중치+drift보정<br>
        🟢 ②유사표본: 유사용역 낙찰이력<br>
        🟡 ③트렌드: 최근 흐름 분석<br>
        🟣 💡권장: 3가지 종합 권장구간<br>
        🏅 신뢰도 등급: A/B/C/D<br>
        📐 금액구간 보정 안내 포함<br><br>
        <b>데이터:</b> {n_c:,}건 | {n_o}개 발주처
        </div>""",unsafe_allow_html=True)

    if not xls_file:
        st.info("👆 입찰서류함 xls 파일을 업로드하면 자동 분석합니다."); st.stop()

    with st.spinner("파일 읽는 중..."):
        try:
            bids=parse_xls(xls_file.read())
            if not bids: st.error("입찰 건을 읽을 수 없습니다."); st.stop()
        except Exception as e: st.error(f"파일 오류: {e}"); st.stop()

    st.success(f"✅ {len(bids)}건 확인")
    results=[]
    prog=st.progress(0,"분석 중...")
    for i,b in enumerate(bids):
        a1=analyze_pattern(b["org"],df_c,pattern_stats)
        a2=analyze_similar(b["name"],b["base"],df_c)
        a3=analyze_trend(b["org"],df_c)
        lo,hi=recommend_range(a1,a2,a3)
        conv_std,conv_lbl=convergence_score(a1,a2,a3)
        amt_lbl,amt_adj,amt_note=get_amt_info(b["base_억"])
        results.append({"bid":b,"a1":a1,"a2":a2,"a3":a3,
                        "range_lo":lo,"range_hi":hi,
                        "conv_std":conv_std,"conv_lbl":conv_lbl,
                        "amt_lbl":amt_lbl,"amt_adj":amt_adj,"amt_note":amt_note})
        prog.progress((i+1)/len(bids))
    prog.empty()

    # ── 요약 테이블 ──────────────────────────────────────────
    st.subheader(f"📋 투찰전략 — {datetime.now().strftime('%Y.%m.%d')} ({len(bids)}건)")
    rows=[]
    for row in results:
        b=row["bid"]; a1=row["a1"]; a2=row["a2"]; a3=row["a3"]
        lo,hi=row["range_lo"],row["range_hi"]
        grade=a1.get("grade","?") if a1 else "?"
        grade_emoji={"A":"🟢","B":"🔵","C":"🟡","D":"🔴"}.get(grade,"⚪")
        rows.append({"No":b["no"],
            "공고명":b["name"][:35]+"…" if len(b["name"])>35 else b["name"],
            "발주기관":b["org"].replace("한국전력공사 ","한전 "),
            "기초(억)":f"{b['base_억']:.4f}" if b["base"]>0 else "미정",
            "금액구간":f"{row['amt_lbl']}({row['amt_note']})",
            "①패턴":f"{a1['pred']:+.4f}%" if a1 else "없음",
            "②유사표본":f"{a2['pred']:+.4f}%" if a2 else "없음",
            "③트렌드":f"{a3['pred']:+.4f}%" if a3 else "없음",
            "💡하한":f"{lo:+.4f}%" if lo else "-",
            "💡상한":f"{hi:+.4f}%" if hi else "-",
            "수렴도":row["conv_lbl"],
            "등급":f"{grade_emoji}{grade}",
            "트렌드":a1["trend"] if a1 else "-"})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True,
                 column_config={"No":st.column_config.NumberColumn(width=50),
                                "공고명":st.column_config.TextColumn(width=230)})
    st.divider()

    # ── 건별 상세 ────────────────────────────────────────────
    st.subheader("📌 건별 상세 + 사정율 흐름 차트")
    for row in results:
        b=row["bid"]; a1=row["a1"]; a2=row["a2"]; a3=row["a3"]
        lo,hi=row["range_lo"],row["range_hi"]
        grade=a1.get("grade","?") if a1 else "?"
        grade_emoji={"A":"🟢","B":"🔵","C":"🟡","D":"🔴"}.get(grade,"⚪")
        label=(f"No.{b['no']}  {b['name'][:48]}  |  "
               f"{b['org'].replace('한국전력공사 ','한전 ')}  |  "
               f"{b['base_억']:.4f}억  |  {b['deadline']}  {grade_emoji}{grade}")
        with st.expander(label):
            # 개선3: 신뢰도 + 개선4: 금액구간 안내 배너
            col_g,col_a=st.columns([1,2])
            with col_g:
                grade_class={"A":"grade-a","B":"grade-b","C":"grade-c","D":"grade-d"}.get(grade,"grade-c")
                mae_v=a1.get("mae",0) if a1 else 0
                st.markdown(f'<span class="{grade_class}">신뢰도 등급 {grade} (MAE:{mae_v:.3f}%)</span>',unsafe_allow_html=True)
                if a1 and abs(a1.get("drift6m",0))>0.03:
                    drift_v=a1["drift6m"]; dir_txt="↑상승" if drift_v>0 else "↓하락"
                    st.caption(f"⚡ 6개월 Drift: {drift_v:+.4f}% {dir_txt} — 트렌드 보정 적용됨")
            with col_a:
                amt_note=row["amt_note"]; amt_adj=row["amt_adj"]
                if "유리" in amt_note:
                    st.success(f"💰 {row['amt_lbl']} 구간 — 사정율 유리한 구간 ({amt_adj:+.4f}%)")
                elif "보수" in amt_note:
                    st.warning(f"⚠️ {row['amt_lbl']} 구간 — 보수적 접근 권장 ({amt_adj:+.4f}%)")
                else:
                    st.info(f"📐 {row['amt_lbl']} 구간 ({amt_adj:+.4f}%)")

            c1,c2,c3,c4=st.columns(4)
            with c1:
                v=f"{a1['pred']:+.4f}%" if a1 else "이력없음"
                st.markdown(f'<div class="val-box val-pattern">①패턴<br>{v}</div>',unsafe_allow_html=True)
                if a1:
                    st.caption(f"n={a1['n']}건 | {a1['trend']} | {a1['pattern']}패턴")
                    st.caption(f"가중치: r5×{a1['w5']}/r10×{a1['w10']}/μ×{a1['wm']}")
                    st.caption(f"r5={a1['r5']:+.4f} / r10={a1['r10']:+.4f} / 직전:{a1['last_val']:+.4f}%")
            with c2:
                v=f"{a2['pred']:+.4f}%" if a2 else "이력없음"
                st.markdown(f'<div class="val-box val-similar">②유사표본<br>{v}</div>',unsafe_allow_html=True)
                if a2:
                    st.caption(f"유사 {a2['n']}건 | 평균:{a2['mean']:+.4f}%")
                    # 개선2: 업체수 참고 표시 (보정 미적용)
                    if a2["avg_companies"]: st.caption(f"업체수 참고:{a2['avg_companies']}개 (보정미적용)")
            with c3:
                v=f"{a3['pred']:+.4f}%" if a3 else "이력없음"
                st.markdown(f'<div class="val-box val-trend">③트렌드<br>{v}</div>',unsafe_allow_html=True)
                if a3:
                    st.caption(f"최근{a3['recent_n']}건평균:{a3['recent_mean']:+.4f}%")
                    st.caption(f"drift:{a3['drift']:+.4f}% | 최근3건:{a3['recent3_mean']:+.4f}%")
            with c4:
                if lo is not None:
                    st.markdown(f'<div class="val-box val-rec">💡권장구간<br>{lo:+.4f}%~{hi:+.4f}%</div>',unsafe_allow_html=True)
                    if b["base"]>0:
                        st.caption(f"하한: {int(b['base']*(100+lo)/100):,}원")
                        st.caption(f"상한: {int(b['base']*(100+hi)/100):,}원")
                    # 수렴도 표시
                    st.caption(f"수렴도: {row['conv_lbl']}")
                else:
                    st.markdown('<div class="val-box" style="background:#fee2e2;color:#991b1b">⚠️ 데이터부족</div>',unsafe_allow_html=True)

            if a1 and (a1.get("all_vals") or a1.get("recent10")):
                st.markdown("---")
                with st.spinner("차트 생성 중..."):
                    chart_buf=make_flow_chart(a1,a2,a3,lo,hi,b["org"])
                if chart_buf:
                    st.image(chart_buf,use_container_width=True)
            else:
                st.caption("⚠️ 이력 데이터 부족으로 차트를 표시할 수 없습니다.")

    st.divider()
    st.subheader("💾 전략표 다운로드")
    excel_buf=make_excel(results)
    today_str=datetime.now().strftime("%Y%m%d")
    st.download_button("📥 엑셀 다운로드 (v2.0 투찰전략표)",
        data=excel_buf,
        file_name=f"투찰전략_{today_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",use_container_width=True)
