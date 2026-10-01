"""Read-only verified collection import. Private outputs never belong in Git."""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bid_engine as engine
import ledger_metadata
from analysis.collection_classification_review import review

def sha(data):
    return hashlib.sha256(data).hexdigest()

def build(archive, legacy, output, as_of="2026-10-01"):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    archive = Path(archive); legacy = Path(legacy)
    initial = {"archive": sha(archive.read_bytes()), "legacy": sha(legacy.read_bytes())}
    frames=[]; files=[]
    with zipfile.ZipFile(archive) as z:
        names=z.namelist(); plan_name=next(n for n in names if n.endswith("장기수집_계획.json"))
        prefix=plan_name.rsplit("/",1)[0]+"/"
        plan=json.loads(z.read(plan_name))
        completed=[t for t in plan["tasks"] if t["state"] in ("verified","verified_zero")]
        print("states",pd.Series([t["state"] for t in plan["tasks"]]).value_counts().to_dict(),flush=True)
        # Zero receipts have no source CSV, but are still completed tasks.
        completed=[t for t in plan["tasks"] if t["state"] in ("verified","verified_zero")]
        for t in completed:
            receipt=t.get("receipt",{}); directory=receipt.get("attempt_dir")
            if not directory:
                directory=next(a["directory"] for a in reversed(t["attempts"]) if a["state"]==t["state"])
            csvs=[]
            for f in receipt.get("files",[]):
                name=prefix+directory.replace("\\","/")+"/"+f["path"].replace("\\","/")
                data=z.read(name)
                assert sha(data)==f["sha256"], name
                files.append({"task":t["id"],"path":name,"sha256":sha(data)})
                if name.lower().endswith(".csv"): csvs.append((name,data))
            for name,data in csvs:
                csv=pd.read_csv(io.BytesIO(data),encoding="utf-8-sig",dtype=object,keep_default_na=False)
                rows=[json.loads(x) for x in csv["원본행JSON"]]
                d=pd.DataFrame([x["values"] for x in rows],columns=rows[0]["headers"])
                assert len(d)==receipt["rows"],t["id"]
                d["source_task"]=t["id"];d["source_profile"]=t["profile"]
                d["source_csv"]=name;d["source_csv_row"]=np.arange(2,len(d)+2)
                frames.append(d)
    raw=pd.concat(frames,ignore_index=True)
    assert len(raw)==112848,len(raw)
    original_columns=[c for c in raw if not c.startswith("source_")]
    dates=engine.rules.parse_date_series(raw["개찰일"])
    target=engine.numeric(raw[engine.RATE]);winner=engine.numeric(raw[engine.WINNER])
    status=np.where(target.notna()&winner.notna(),"both_numeric",np.where(target.notna(),"target_only_numeric","target_not_numeric"))
    raw["quality_status"]=status
    counts=pd.Series(status).value_counts().to_dict()
    assert counts=={"both_numeric":87539,"target_not_numeric":21757,"target_only_numeric":3552},counts
    missing_name=raw["공고명"].str.strip().eq("");missing_org=raw["발주기관"].str.strip().eq("")
    future=dates.gt(pd.Timestamp(as_of));bad_target=target.notna()&(~np.isfinite(target)|target.abs().ge(10))
    bad_winner=winner.notna()&(~np.isfinite(winner)|winner.abs().ge(10))
    flags={"missing_name":missing_name,"missing_org":missing_org,"missing_date":dates.isna(),"future_opening":future,"abnormal_target":bad_target,"abnormal_winner":bad_winner}
    raw["quality_warnings"]=[";".join(k for k,v in flags.items() if v.iloc[i]) for i in range(len(raw))]
    keycols=["공고번호","개찰일","발주기관","공고명","기초금액"]
    raw["event_id"]=[sha(json.dumps(x,ensure_ascii=False).encode()) for x in raw[keycols].itertuples(index=False,name=None)]
    profiles=raw.groupby("event_id")["source_profile"].nunique()
    dual=set(profiles[profiles.gt(1)].index)
    assert len(dual)==721,len(dual)
    # Stable service-first precedence preserves explicit service descriptions and all provenance.
    raw=raw.sort_values(["source_profile","source_task","source_csv_row"],ascending=[False,True,True],kind="stable")
    lineage=raw.groupby("event_id")["source_profile"].agg(lambda x:";".join(sorted(set(x))))
    operational=raw.drop_duplicates("event_id").copy()
    operational["source_profiles"]=operational["event_id"].map(lineage)
    operational[engine.RATE]=engine.numeric(operational[engine.RATE])
    operational[engine.WINNER]=engine.numeric(operational[engine.WINNER])
    operational["개찰일"]=engine.rules.parse_date_series(operational["개찰일"])
    operational["기초금액"]=engine.numeric(operational["기초금액"])
    eligible=engine.prepare_history(operational,as_of)
    class_review,class_learning=review(raw,eligible)
    class_review.to_csv(output/'classification_review.csv',index=False,encoding='utf-8-sig')
    raw.to_csv(output/"source_rows.csv.gz",index=False,encoding="utf-8-sig",compression="gzip")
    operational.to_pickle(output/"operational.pkl")
    eligible.to_pickle(output/"training.pkl")
    pd.DataFrame(files).to_csv(output/"source_files.csv",index=False,encoding="utf-8-sig")
    manifest={"version":"collection-20261001-112848","as_of":as_of,"plan_id":plan["plan_id"],"source":"Info21C 검증 완료 수집원본","source_rows":len(raw),"operational_rows":len(operational),"training_rows":len(eligible),"completed_tasks":len(completed),"total_tasks":len(plan["tasks"]),"dual_profile_events":len(dual),"numeric_status":counts,"warnings":{k:int(v.sum()) for k,v in flags.items()},"data_start_date":str(eligible['_date'].min().date()),"data_end_date":str(eligible['_date'].max().date()),"region_basis":"수집 원천값; 실제 참가제한 별도 검증 필요","unresolved":"2024-05-29 전기공사 1건: 화면29 / Excel26; 부분자료 제외","source_hashes":initial,"original_columns":original_columns,"classification_policy":"동일사건 1행, 용역 출처 우선, 공고명 기반 기존 분류 유지; 모든 출처 별도 보존"}
    manifest['operational_data_sha256']=ledger_metadata.data_signature(operational,original_columns)
    manifest['classification_review_events']=len(class_review)
    manifest['classification_review_learning_rows']=class_learning
    assert initial=={"archive":sha(archive.read_bytes()),"legacy":sha(legacy.read_bytes())}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    frozen=output/"legacy_frozen.xlsx"
    if frozen.exists():
        assert sha(frozen.read_bytes())==initial['legacy'],'Existing frozen backup differs'
    else:
        frozen.write_bytes(legacy.read_bytes())
    frozen.chmod(0o444)
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--archive",required=True);p.add_argument("--legacy",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();build(a.archive,a.legacy,a.output)
