"""Full rolling S1-S5/operating evaluation using the unchanged live app body.

Compile the prediction loop from recommend_bids into a streaming adapter: same
code and functions, one shared engine per mode, no repeated preparation. Never
fit policy or weights to either dataset.
"""
import argparse
import ast
import hashlib
import inspect
import json
import sys
import time
from collections import OrderedDict
from bisect import bisect_left
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bid_engine as engine
import bid_rules as rules
import bid_strategies as strategies

def ordered_window(records,date,days,cap):
    """Identical window on the chronological records guaranteed by this runner."""
    lower=date-pd.Timedelta(days=days)
    lo=bisect_left(records,lower,key=lambda r:r['date'])
    hi=bisect_left(records,date,key=lambda r:r['date'])
    return records[max(lo,hi-cap):hi]

def cached_weights(original):
    cache=OrderedDict();last=[None]
    def weights(records,date):
        if last[0]!=date:cache.clear();last[0]=date
        key=tuple(id(r) for r in records)
        if key not in cache:
            cache[key]=original(records,date)
            if len(cache)>128:cache.popitem(last=False)
        return cache[key]
    return weights

def cached_interval():
    """Memoize unchanged intermediate arrays, retaining original arithmetic order."""
    cache=OrderedDict();last=[None]
    def point(family,issuer,date,retained,fallback):
        if last[0]!=date:cache.clear();last[0]=date
        def score(records):
            key=tuple(id(r) for r in records)
            if key not in cache:
                valid=strategies._winner_records(records)
                if not valid:return np.zeros(len(strategies.GRID)),0
                lower=np.array([r['value'] for r in valid]);upper=np.array([r['winner'] for r in valid]);weights=strategies._weights(valid,date)
                hits=(lower[:,None]<strategies.GRID)&(strategies.GRID<upper[:,None])
                cumulative=np.cumsum(np.pad(hits.astype(float),((0,0),(6,5))),axis=1)
                smooth=(cumulative[:,11:]-cumulative[:,:-11])/11.
                cache[key]=(lower,upper,weights,smooth)
                if len(cache)>64:cache.popitem(last=False)
            lower,upper,weights,smooth=cache[key]
            covered=np.zeros(len(lower),dtype=bool)
            for rate in retained:covered|=(lower<rate)&(rate<upper)
            return np.einsum('i,ij->j',weights*(~covered),smooth)/weights.sum(),len(lower)
        global_score,n=score(family);local_score,m=score(issuer)
        if len(family)<50 or n<30:return fallback
        mix=m/(m+60.);scores=mix*local_score+(1-mix)*global_score
        if not np.any(scores>0):return fallback
        best=np.flatnonzero(np.isclose(scores,scores.max(),atol=1e-12))
        return float(strategies.GRID[best[np.argmin(abs(strategies.GRID[best]-fallback))]])
    return point

def live_adapter():
    tree=ast.parse(inspect.getsource(strategies.recommend_bids)); function=tree.body[0]
    loop=next(n for n in function.body if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and any(isinstance(x,ast.Name) and x.id=='row' for x in n.target.elts))
    # Skip only the cursor advancement. Caller supplies strictly prior-day state.
    body=loop.body[1:]
    fn=ast.FunctionDef(name='predict',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=n) for n in ('instance','row','bids','mode','policy','history_hash')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[ast.parse('i=0\noutput=[None]').body[0],ast.parse('output=[None]').body[0],ast.For(target=ast.Name(id='once',ctx=ast.Store()),iter=ast.List(elts=[ast.Constant(0)],ctx=ast.Load()),body=body,orelse=[]),ast.Return(value=ast.Subscript(value=ast.Name(id='output',ctx=ast.Load()),slice=ast.Constant(0),ctx=ast.Load()))],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
    namespace=dict(vars(strategies),engine=engine,rules=rules)
    exec(compile(module,'<unchanged live strategy loop>','exec'),namespace)
    return namespace['predict']

def run(source,out,as_of):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    raw=pd.read_pickle(source) if str(source).endswith('.pkl') else pd.read_excel(source,sheet_name='통합낙찰이력')
    data=engine.prepare_history(raw,as_of)
    strategies._window=ordered_window
    strategies._weights=cached_weights(strategies._weights)
    strategies.interval_point=cached_interval()
    predict=live_adapter();instance=engine.PredictionEngine();rows=[];start=time.time()
    modes=['S1','S2','S3','S4','S5','auto'];policy=engine.load_policy()
    # Archive a data signature without any original notice names in public summaries.
    signature=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    done=0
    for date,g in data.groupby('_date',sort=True):
        records=[engine.source_record(r) for r in g.to_dict('records')]
        for record in records:
            bid=dict(name=record['name'],org=record['org'],industry=record['industry'],base=record['base'],deadline=date,bid_no=record['notice'],region=record['region'])
            scope=rules.historical_scope(record['name'],record['industry'],record['org'],record['base'],date)
            excluded=''
            if len(instance.states['global'].records)<150:excluded='initial_history_under_150'
            elif record['family']=='kepco_supervision' and scope=='지역제한' and rules.kepco_branch(record['org']) not in ('부산울산','경북','대구'):excluded='other_kepco_local_branch'
            elif record['family']=='kepco_supervision' and scope=='확인필요':excluded='kepco_scope_unknown'
            # Live strategies all consult the same baseline. Cache only this pure result.
            original=instance.predict;baseline=[None];computed=[False]
            def cached(row,alternatives=True):
                if not computed[0]:
                    active=not policy.get('effective_from') or row['date']>=pd.Timestamp(policy['effective_from'])
                    key=row['family']+'|'+engine.scope_for(row)['scope']
                    routes=policy.get('rules',{}) if active else {}
                    used=routes.get(key+'|'+row['org'],routes.get(key,'current'))
                    baseline[0]=original(row,alternatives=used!='current');computed[0]=True
                return baseline[0]
            instance.predict=cached
            x={'date':str(date.date()),'notice':record['notice'],'org':record['org'],'name':record['name'],'base':record['base'],'family':record['family'],'actual':record['value'],'winner':record['winner'],'exclusion':excluded}
            baseline_s1=None
            for mode in modes:
                p=None if excluded else baseline_s1 if mode=='auto' and strategies.resolved_strategy(record['family'],'auto',date)=='S1' else predict(instance,record,[bid],mode,policy,signature)
                if mode=='S1':baseline_s1=p
                rates=p.get('rates',[]) if p else []
                valid=bool(rates) and engine.strict_win(record['value'],rates,record['winner']) is not None
                x[mode+'_predicted']=bool(rates);x[mode+'_evaluable']=valid
                x[mode+'_win']=bool(engine.strict_win(record['value'],rates,record['winner'])) if valid else False
                x[mode+'_mae']=min(abs(v-record['value']) for v in rates) if rates else np.nan
                center=rates[0] if len(rates)==1 else (rates[1] if len(rates)>1 else np.nan)
                x[mode+'_center_mae']=abs(center-record['value'])
                x[mode+'_selected']=p.get('selected_strategy','HOLD') if p else 'HOLD'
            instance.predict=original;rows.append(x)
        for record in records:instance.add(record)
        done+=len(records)
        if done%3000<len(records):print(f'{done}/{len(data)} {time.time()-start:.0f}s',flush=True)
    detail=pd.DataFrame(rows);detail.to_csv(out/'private_detail.csv.gz',index=False,compression='gzip')
    summary=[]
    periods={'all':detail,'fixed_holdout':detail[detail.date.between('2025-09-01','2026-09-01')],'fixed_audit':detail[detail.date.between('2026-06-01','2026-09-23')],'new_period':detail[detail.date.gt('2026-09-23')]}
    for period,d in periods.items():
        for family,x in [('all',d)]+list(d.groupby('family')):
            for mode in modes:
                predicted=x[x[mode+'_predicted']];valid=x[x[mode+'_evaluable']];wins=int(valid[mode+'_win'].sum())
                summary.append(dict(period=period,family=family,strategy=mode,total=len(x),predicted=len(predicted),evaluable=len(valid),wins=wins,win_rate=wins/len(valid) if len(valid) else None,coverage_mae=predicted[mode+'_mae'].mean(),center_mae=predicted[mode+'_center_mae'].mean(),hit_030=predicted[mode+'_mae'].le(.30).mean()))
    pd.DataFrame(summary).to_csv(out/'summary.csv',index=False,encoding='utf-8-sig')
    meta={'source_rows':len(raw),'valid_history':len(data),'as_of':as_of,'source_sha256':signature,'live_loop_sha256':hashlib.sha256(inspect.getsource(strategies.recommend_bids).encode()).hexdigest(),'engine_sha256':hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),'policy_sha256':hashlib.sha256(Path('model_policy.json').read_bytes()).hexdigest(),'runtime_seconds':round(time.time()-start),'same_day_training':False,'burn_in':150,'strict_win':'actual < recommendation < winner; finite abs(winner)<10; missing winner excluded; nonpositive intervals remain losses per v2.15.7','strategy_refit':False,'exclusions':detail.exclusion.value_counts().to_dict()}
    (out/'protocol.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8');print(meta,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);p.add_argument('--as-of',default='2026-10-01');a=p.parse_args();run(a.source,a.out,a.as_of)
