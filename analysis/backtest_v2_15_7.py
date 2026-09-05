"""Chronological model-family audit. Run from any directory with --source/--out-dir."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bid_engine as engine
import bid_rules as rules

DEV_END = pd.Timestamp("2025-09-01")
AUDIT_START = pd.Timestamp("2026-06-01")
STRATEGIES = engine.STRATEGIES


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def metrics(d, strategy="current"):
    valid = d["判定가능"].astype(bool)
    x = d.loc[valid]
    wins = int(x[f"win_{strategy}"].sum())
    n = len(x)
    positive = x[x["1순위사정율"] > x["실제사정율"]]
    return {"공고수":len(d), "판정가능":n, "가상낙찰":wins,
        "가상낙찰률":wins/n if n else None,
        "양수구간판정가능":len(positive),
        "양수구간낙찰률":float(positive[f"win_{strategy}"].mean()) if len(positive) else None,
        "추천커버MAE":float(d[f"mae_{strategy}"].mean()) if len(d) else None}


def block_ci(d, candidate, baseline="current", iterations=2000, alpha=0.05):
    x = d[d["判定가능"]].copy()
    if len(x) == 0:
        return [None,None]
    x["diff"] = x[f"win_{candidate}"].astype(float) - x[f"win_{baseline}"].astype(float)
    daily = x.groupby("개찰일").agg(diff=("diff","sum"), n=("diff","size"))
    if len(daily) < 20:
        return [None,None]
    a = daily.to_numpy()
    rng = np.random.default_rng(2157)
    indexes = rng.integers(0, len(a), size=(iterations,len(a)))
    sums = a[indexes].sum(axis=1)
    return np.quantile(sums[:,0]/sums[:,1], [alpha/2,1-alpha/2]).tolist()


def segment_decision(key, d):
    dev=d[d["개찰일"]<DEV_END]
    validation=d[(d["개찰일"]>=DEV_END)&(d["개찰일"]<AUDIT_START)]
    audit=d[d["개찰일"]>=AUDIT_START]
    base_dev, base_val=metrics(dev),metrics(validation)
    candidates=[]
    for method in STRATEGIES[1:]:
        dm,vm=metrics(dev,method),metrics(validation,method)
        dg=(dm["가상낙찰률"] or 0)-(base_dev["가상낙찰률"] or 0)
        vg=(vm["가상낙찰률"] or 0)-(base_val["가상낙찰률"] or 0)
        ci=block_ci(validation,method,iterations=1000,alpha=0.05/4)
        eligible=(dm["판정가능"]>=200 and vm["판정가능"]>=100 and dg>=0.005 and vg>=0.005 and ci[0] is not None and ci[0]>0)
        candidates.append({"method":method,"development":dm,"validation":vm,"development_gain":dg,"validation_gain":vg,"validation_adjusted_ci":ci,"eligible":eligible})
    good=[c for c in candidates if c["eligible"]]
    chosen=max(good,key=lambda c:c["validation_gain"])["method"] if good else "current"
    # Audit is consulted once only after development/validation selection.
    am,bm=metrics(audit,chosen),metrics(audit)
    ci=block_ci(audit,chosen)
    gain=(am["가상낙찰률"] or 0)-(bm["가상낙찰률"] or 0)
    return {"key":key,"candidate_evaluation":candidates,"selected_before_audit":chosen,
        "audit_baseline":bm,"audit_candidate":am,"audit_gain":gain,"audit_ci95":ci,
        "preliminary_accept":chosen!="current" and am["판정가능"]>=80 and gain>=0.005 and ci[0] is not None and ci[0]>0}


def summarize(detail, out):
    d=detail
    maxdate=d["개찰일"].max()
    periods={"전체":d,"최근1년":d[d["개찰일"]>=maxdate-pd.Timedelta(days=365)],
        "최종검증":d[d["개찰일"]>=AUDIT_START],"신규9월2일이후":d[d["개찰일"]>=pd.Timestamp("2026-09-02")]}
    summary=[]
    for period,x in periods.items():
        for strategy in STRATEGIES:
            summary.append({"기간":period,"방법":strategy,**metrics(x,strategy)})
    pd.DataFrame(summary).to_csv(out/"strategy_summary.csv",index=False,encoding="utf-8-sig")
    rows=[]
    roles=[]
    for period,x in periods.items():
        for dimension in ("중심모델군","발주기관","모델구간","세부업종"):
            for key,g in x.groupby(dimension):
                for strategy in STRATEGIES:
                    rows.append({"기간":period,"구분축":dimension,"구분":key,"방법":strategy,**metrics(g,strategy)})
        for (key, count),g in x.groupby(["모델구간", "참여업체수"]):
            for idx,role in enumerate(("방향성 헷지","중심모델","라인헷지"),1):
                valid=g[g["判定가능"] & g[f"role_{idx}_active"]]
                wins=int(valid[f"role_{idx}_win"].sum())
                unique=int(valid[f"role_{idx}_unique"].sum())
                roles.append({"기간":period,"모델구간":key,"참여업체수":int(count),"방법":role,"배정공고수":len(valid),"단독가상낙찰":wins,"단독가상낙찰률":wins/len(valid) if len(valid) else None,"추가낙찰공고":unique,"추가기여율":unique/len(valid) if len(valid) else None})
    pd.DataFrame(rows).to_csv(out/"segment_strategy_summary.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(roles).to_csv(out/"role_contribution.csv",index=False,encoding="utf-8-sig")
    decisions=[segment_decision(str(k),g) for k,g in d.groupby("모델구간")]
    # Issuer-specific routes require enough prior observations; sparse issuers inherit family.
    for (key,org),g in d.groupby(["모델구간","발주기관"]):
        if int(((g["개찰일"]<AUDIT_START)&g["判定가능"]).sum())>=500:
            decisions.append(segment_decision(key+"|"+org,g))
    families = {a["key"]:a["selected_before_audit"] for a in decisions if len(a["key"].split("|"))==2}
    for a in decisions:
        parts=a["key"].split("|")
        if len(parts)<=2:
            continue
        inherited=families.get("|".join(parts[:2]),"current")
        selected=a["selected_before_audit"]
        a["inherited_family_candidate"]=inherited
        if selected in ("current", inherited) or inherited=="current":
            continue
        g=d[d["모델구간"].eq("|".join(parts[:2])) & d["발주기관"].eq("|".join(parts[2:]))]
        dev=g[g["개찰일"]<DEV_END]
        val=g[(g["개찰일"]>=DEV_END)&(g["개찰일"]<AUDIT_START)]
        dg=(metrics(dev,selected)["가상낙찰률"] or 0)-(metrics(dev,inherited)["가상낙찰률"] or 0)
        vg=(metrics(val,selected)["가상낙찰률"] or 0)-(metrics(val,inherited)["가상낙찰률"] or 0)
        ci=block_ci(val,selected,baseline=inherited,alpha=.05/4)
        a["issuer_incremental_validation"]={"dev_gain":dg,"validation_gain":vg,"adjusted_ci":ci}
        if not (dg>=.005 and vg>=.005 and ci[0] is not None and ci[0]>0):
            a["issuer_candidate_before_inheritance_check"]=selected
            a["selected_before_audit"]="current"
            a["preliminary_accept"]=False
            a["audit_candidate"]=a["audit_baseline"]
            a["audit_gain"]=0.0
            a["audit_ci95"]=[0.0,0.0]
    # Bonferroni audit gate across preselected routes; resamples preserve per-notice estimand.
    tested=[a for a in decisions if a["selected_before_audit"]!="current"]
    routes={}
    for decision in decisions:
        selected=decision["selected_before_audit"]
        if selected=="current":
            decision["accepted"]=False
            continue
        parts=decision["key"].split("|")
        g=d[d["모델구간"].eq("|".join(parts[:2]))]
        if len(parts)>2:
            g=g[g["발주기관"].eq("|".join(parts[2:]))]
        audit=g[g["개찰일"]>=AUDIT_START]
        ci=block_ci(audit,selected,iterations=4000,alpha=0.05/max(1,len(tested)))
        decision["audit_multiple_route_adjusted_ci"]=ci
        decision["accepted"]=bool(decision["preliminary_accept"] and ci[0] is not None and ci[0]>0)
        inherited=decision.get("inherited_family_candidate","current")
        if len(parts)>2 and inherited not in ("current",selected):
            extra=block_ci(audit,selected,baseline=inherited,iterations=4000,alpha=.05/max(1,len(tested)))
            decision["issuer_incremental_audit_ci"]=extra
            decision["accepted"] = decision["accepted"] and extra[0] is not None and extra[0]>0
        if decision["accepted"]:
            routes[decision["key"]]=selected
    dump(out/"selection_decisions.json",decisions)
    # Frozen, pre-audit selection is the honest evaluation of the selection procedure.
    frozen={a["key"]:a["selected_before_audit"] for a in decisions
        if a["selected_before_audit"] != "current"}
    for policy_name,policy in (("frozen",frozen),("accepted",routes)):
        selected=[policy.get(r["모델구간"]+"|"+r["발주기관"],policy.get(r["모델구간"],"current")) for r in d.to_dict("records")]
        d["strategy_"+policy_name]=selected
        d["win_"+policy_name]=[d.iloc[i]["win_"+s] for i,s in enumerate(selected)]
        d["mae_"+policy_name]=[d.iloc[i]["mae_"+s] for i,s in enumerate(selected)]
    final={p:{s:metrics(x,s) for s in ("current","frozen","accepted")} for p,x in
        {"全期間":d,"最近1年":d[d["개찰일"]>=maxdate-pd.Timedelta(days=365)],"최종검증":d[d["개찰일"]>=AUDIT_START]}.items()}
    audit=d[d["개찰일"]>=AUDIT_START]
    final["frozen_audit_gain_ci95"]=block_ci(audit,"frozen")
    dump(out/"policy_summary.json",final)
    daily=[]
    for date,g in d.groupby("개찰일"):
        daily.append({"개찰일":date.strftime("%Y-%m-%d"),**metrics(g)})
    pd.DataFrame(daily).to_csv(out/"daily_summary.csv",index=False,encoding="utf-8-sig")
    recent=d[d["개찰일"]>=maxdate-pd.Timedelta(days=365)]
    policy={"version":"v2.15.7","as_of":maxdate.strftime("%Y-%m-%d"),
        "effective_from":(maxdate+pd.Timedelta(days=1)).strftime("%Y-%m-%d"),"rules":routes,
        "summary":{"recent1y":metrics(recent),"audit_baseline":metrics(audit),"audit_frozen":metrics(audit,"frozen"),
            "accepted_route_count":len(routes),"finite_equal_or_reversed_count":int(((recent["1순위사정율"]<=recent["실제사정율"])&recent["判定가능"]).sum())},
        "definition":"예가/기초 < 추천사정율 < 1순위사정율; 동일일 결과와 해당 공고의 실제 경쟁업체수 제외",
        "limitations":"실제 당사 낙찰률이 아닌 과거 가상검증. 승인된 경로의 검증구간 사후 조합은 확정 성능으로 해석하지 않음."}
    dump(out/"model_policy.json",policy)
    d.to_csv(out/"backtest_detail.csv",index=False,encoding="utf-8-sig")
    return policy,final


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--out-dir",required=True)
    p.add_argument("--as-of",default="2026-09-05")
    p.add_argument("--summarize-only",action="store_true")
    args=p.parse_args()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    if args.summarize_only:
        d=pd.read_csv(out/"backtest_detail.csv",parse_dates=["개찰일"])
        policy,final=summarize(d,out)
        print(json.dumps({"policy":policy,"summary":final},ensure_ascii=False,default=str),flush=True)
        return
    started=time.time()
    raw=pd.read_excel(args.source,sheet_name="통합낙찰이력")
    data=engine.prepare_history(raw,args.as_of)
    quality={"source_file":Path(args.source).name,"source_sha256":hashlib.sha256(Path(args.source).read_bytes()).hexdigest(),
        "source_rows":len(raw),"exact_duplicates":int(raw.duplicated().sum()),"valid_history":len(data),
        "date_min":str(data["_date"].min().date()),"date_max":str(data["_date"].max().date()),
        "missing_winner":int(data["_winner"].isna().sum()),"equal_winner":int(data["_winner"].eq(data["_value"]).sum()),
        "reversed_winner":int(data["_winner"].lt(data["_value"]).sum()),
        "missing_region":int(data["지역"].eq("").sum()),
        "event_quality":data.attrs.get("quality",{}),
        "families":data["_family"].value_counts().to_dict(),"as_of":args.as_of,
        "protocol":{"development_end":"2025-08-31","validation":"2025-09-01~2026-05-31","final_audit_start":"2026-06-01","same_day_training":False,"realized_target_company_count":False}}
    dump(out/"data_quality.json",quality)
    e=engine.PredictionEngine();results=[];excluded=[]
    processed=0
    for date,g in data.groupby("_date",sort=True):
        records=[engine.source_record(r) for r in g.to_dict("records")]
        for row in records:
            reason=""
            hs=rules.historical_scope(row["name"],row["industry"],row["org"],row["base"],row["date"])
            if row["family"]=="kepco_supervision" and hs=="지역제한" and rules.kepco_branch(row["org"]) not in ("부산울산","경북","대구"):
                reason="타 권역 한전 지역제한: 기존 업로드 제외 운영조건"
            elif row["family"]=="kepco_supervision" and hs=="확인필요":
                reason="한전 감리 기초금액/기준일 미확인"
            elif len(e.states["global"].records)<150:
                reason="초기 이력 150건 미만"
            pred=None if reason else e.predict(row)
            if pred is None:
                excluded.append({"개찰일":str(date.date()),"공고번호":row["notice"],"발주기관":row["org"],"사유":reason or "해당 구간 사전 이력 없음"})
                continue
            current=pred["recommendations"]["current"]
            x={"개찰일":date,"공고번호":row["notice"],"공고명":row["name"],"발주기관":row["org"],
                "중심모델군":rules.MODEL_FAMILY_LABELS[row["family"]],"내부분류":row["family"],"세부업종":row["svc"],
                "입찰범위":pred["scope"]["scope"],"참여업체수":len(current),"기초금액":row["base"],
                "모델구간":row["family"]+"|"+pred["scope"]["scope"],
                "실제사정율":row["value"],"1순위사정율":row["winner"],"사전이력수":pred["prior_rows"],
                "학습최종일":e.last_date,"判定가능":engine.strict_win(row["value"],current,row["winner"]) is not None}
            for s,recs in pred["recommendations"].items():
                x["win_"+s]=bool(engine.strict_win(row["value"],recs,row["winner"]))
                x["mae_"+s]=float(min(abs(r-row["value"]) for r in recs))
                for i in range(3):
                    x[f"rec_{s}_{i+1}"]=recs[i] if i<len(recs) else np.nan
            role_recs=[None,current[0],None] if len(current)==1 else current+[None]*(3-len(current))
            role_wins=[bool(engine.strict_win(row["value"],[r],row["winner"])) if r is not None else False for r in role_recs]
            for i in range(3):
                x[f"role_{i+1}_active"]=role_recs[i] is not None
                x[f"role_{i+1}_win"]=role_wins[i]
                x[f"role_{i+1}_unique"]=role_wins[i] and sum(role_wins)==1
            results.append(x)
        for row in records:
            e.add(row)
        processed+=len(g)
        if processed%2000<len(g):
            print(f"{processed:,}/{len(data):,} processed; {len(results):,} predictions; {time.time()-started:.0f}s",flush=True)
    d=pd.DataFrame(results)
    d.to_csv(out/"backtest_detail.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(excluded).to_csv(out/"excluded_notices.csv",index=False,encoding="utf-8-sig")
    quality.update({"evaluated_notices":len(d),"excluded_notices":len(excluded),"excluded_reasons":pd.Series([r["사유"] for r in excluded]).value_counts().to_dict(),"runtime_seconds":round(time.time()-started,1)})
    dump(out/"data_quality.json",quality)
    policy,final=summarize(d,out)
    print(json.dumps({"quality":quality,"policy":policy,"summary":final},ensure_ascii=False,default=str),flush=True)


if __name__=="__main__":
    main()
