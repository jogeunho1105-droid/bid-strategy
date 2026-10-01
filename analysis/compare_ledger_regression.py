"""Public aggregate comparison; original event-level results remain private."""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd

def key(d):
    return d['notice'].fillna('').astype(str).str.strip()+'|'+d['date']+'|'+d['org'].fillna('').str.strip()+'|'+d['name'].fillna('').str.strip()+'|'+pd.to_numeric(d['base'],errors='coerce').map(lambda v:format(v,'.12g') if pd.notna(v) else '')

def compare(old,new,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    a=pd.read_csv(Path(old)/'summary.csv');b=pd.read_csv(Path(new)/'summary.csv')
    delta=a.merge(b,on=['period','family','strategy'],suffixes=('_old','_new'),how='outer')
    for col in ['total','predicted','evaluable','wins','win_rate','coverage_mae','center_mae','hit_030']:delta[col+'_change']=delta[col+'_new']-delta[col+'_old']
    delta.to_csv(out/'before_after.csv',index=False,encoding='utf-8-sig')
    oldd=pd.read_csv(Path(old)/'private_detail.csv.gz');newd=pd.read_csv(Path(new)/'private_detail.csv.gz')
    oldd['event_key']=key(oldd);newd['event_key']=key(newd)
    pairs=oldd.merge(newd,on='event_key',suffixes=('_old','_new'),validate='one_to_one')
    pairs['same_outcome']=np.isclose(pairs.actual_old,pairs.actual_new,atol=1e-10,rtol=0)&((pairs.winner_old.isna()&pairs.winner_new.isna())|np.isclose(pairs.winner_old,pairs.winner_new,atol=1e-10,rtol=0))
    metrics=[]
    for period,mask in [('all',pd.Series(True,index=pairs.index)),('fixed_holdout',pairs.date_old.between('2025-09-01','2026-09-01')),('fixed_audit',pairs.date_old.between('2026-06-01','2026-09-23'))]:
        for cohort,subset in [('common_events',pairs[mask]),('common_unchanged_outcomes',pairs[mask&pairs.same_outcome])]:
            for mode in ['S1','S2','S3','S4','S5','auto']:
                d=subset[subset[mode+'_evaluable_old']&subset[mode+'_evaluable_new']].copy()
                diff=d[mode+'_win_new'].astype(float)-d[mode+'_win_old'].astype(float)
                daily=pd.DataFrame({'date':d.date_old,'diff':diff,'n':1}).groupby('date').sum().to_numpy()
                ci=[None,None]
                if len(daily)>=20:
                    rng=np.random.default_rng(2157);draws=rng.integers(0,len(daily),size=(2000,len(daily)));sums=daily[draws].sum(axis=1);ci=np.quantile(sums[:,0]/sums[:,1],[.025,.975]).tolist()
                metrics.append(dict(period=period,cohort=cohort,strategy=mode,common_events=len(subset),both_evaluable=len(d),wins_old=int(d[mode+'_win_old'].sum()),wins_new=int(d[mode+'_win_new'].sum()),win_rate_change=float(diff.mean()) if len(d) else None,paired_daily_ci95_low=ci[0],paired_daily_ci95_high=ci[1],coverage_mae_old=float(d[mode+'_mae_old'].mean()) if len(d) else None,coverage_mae_new=float(d[mode+'_mae_new'].mean()) if len(d) else None))
    pd.DataFrame(metrics).to_csv(out/'paired_cohorts.csv',index=False,encoding='utf-8-sig')
    summary={'old_valid_events':len(oldd),'new_valid_events':len(newd),'matched_valid_events':len(pairs),'old_only_valid_events':len(oldd)-len(pairs),'new_only_valid_events':len(newd)-len(pairs),'matched_outcome_changed':int((~pairs.same_outcome).sum()),'confidence_interval':'paired dates block bootstrap 2000 draws seed2157; descriptive, no retuning','limitation':'Data coverage and outcome/source values changed. Differences are not proof of improved future performance or actual participation eligibility.'}
    (out/'cohort_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    for src,label in [(old,'old'),(new,'new')]:
        (out/(label+'_protocol.json')).write_bytes((Path(src)/'protocol.json').read_bytes())
    print(summary,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--old',required=True);p.add_argument('--new',required=True);p.add_argument('--out',required=True);a=p.parse_args();compare(a.old,a.new,a.out)
