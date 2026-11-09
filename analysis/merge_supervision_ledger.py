"""Private supervision ingestion; keep immutable originals and event-level lineage."""
import argparse
import hashlib
import json
import re
import unicodedata
import shutil
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import numpy as np
import pandas as pd
from openpyxl import load_workbook
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bid_engine as engine
import ledger_metadata

KEYS=['공고번호','개찰일','발주기관','기초금액']
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def text(v):
    if pd.isna(v):return ''
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(v))).strip()
def keyed(frame,columns=KEYS):
    f=frame.copy()
    f['개찰일']=engine.rules.parse_date_series(f['개찰일']).dt.strftime('%Y-%m-%d').fillna('')
    f['기초금액']=engine.numeric(f['기초금액']).map(lambda v:format(v,'.12g') if pd.notna(v) else '')
    return f[columns].apply(lambda row:tuple(text(v) for v in row),axis=1)
def load_new(root):
    root=Path(root);queue=json.loads((root/'all-supervision-detail-queue.json').read_text(encoding='utf-8'))
    pilot=json.loads((root/'official-list-ser.json').read_text(encoding='utf-8'))
    cache={};records=[];columns=pilot['headers']
    for q in queue:
        s=next((s for s in q['sources'] if s['profile']=='power_services'),q['sources'][0])
        if s.get('month'):
            page=parse_qs(urlparse(s['list_url']).query)['page'][0]
            path=root/'monthly-supervision'/s['month']/f'list-{page}.xlsx'
            if path not in cache:
                w=load_workbook(path,read_only=True,data_only=True);rows=list(w.active.values);w.close()
                cache[path]={str(r[0]):r for r in rows[1:] if r[0] is not None}
            raw=cache[path][s['cells'][0].strip()]
        else:
            raw=pilot['rows'][int(s['cells'][0].strip())-1]
            page=parse_qs(urlparse(s['list_url']).query).get('page',['1'])[0]
            path=root/'retry-service-02'/f'list-{int(page):03d}.xlsx'
        d=dict(zip(columns,raw));assert f"[{d['공고번호']}]" in s['cells'][1],q['id']
        d.update(source_event_id=q['id'],source_url=q['url'],source_excel=str(path),source_excel_row=2+(int(s['cells'][0].strip())-1)%100)
        records.append(d)
    f=pd.DataFrame(records);assert len(f)==8482 and f.source_event_id.is_unique
    return f

def audit(base,collection,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    old=pd.read_pickle(Path(base)/'operational.pkl');new=load_new(collection)
    old.to_pickle(out/'before.pkl');new.to_pickle(out/'incoming.pkl')
    old['_key4']=keyed(old);new['_key4']=keyed(new)
    old['_key5']=keyed(old,KEYS+['공고명']);new['_key5']=keyed(new,KEYS+['공고명'])
    old['_key3']=keyed(old,['공고번호','개찰일','발주기관']);new['_key3']=keyed(new,['공고번호','개찰일','발주기관'])
    result={'before_rows':len(old),'incoming_rows':len(new),'incoming_key4_duplicate_rows':int(new._key4.duplicated(False).sum()),'incoming_key5_duplicate_rows':int(new._key5.duplicated(False).sum()),'key5_overlap':int(new._key5.isin(set(old._key5)).sum()),'key4_overlap':int(new._key4.isin(set(old._key4)).sum()),'key3_overlap':int(new._key3.isin(set(old._key3)).sum())}
    for n,k in [('incoming_duplicate4','_key4'),('incoming_duplicate5','_key5')]:new[new[k].duplicated(False)].drop(columns=['_key4','_key5','_key3']).to_csv(out/(n+'.csv'),index=False,encoding='utf-8-sig')
    new[new._key3.isin(set(old._key3))&~new._key4.isin(set(old._key4))].drop(columns=['_key4','_key5','_key3']).to_csv(out/'changed_base_candidates.csv',index=False,encoding='utf-8-sig')
    (out/'initial_audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(result,flush=True)

def merge_frames(old,incoming,original_columns):
    """Correction suffix, day, issuer, title and base must all match; never fuzzy join."""
    old=old.copy().reset_index(drop=True);incoming=incoming.copy().reset_index(drop=True)
    # Normalize before assigning into an existing datetime column: pandas would
    # otherwise interpret e.g. 24.11.01 as 2001-11-24 during scalar assignment.
    incoming['개찰일']=engine.rules.parse_date_series(incoming['개찰일'])
    keys=KEYS+['공고명'];oldkeys=keyed(old,keys);newkeys=keyed(incoming,keys)
    if oldkeys.duplicated().any() or newkeys.duplicated().any():
        raise ValueError('Ambiguous repeated full event key: review original events first')
    lookup=dict(zip(oldkeys,old.index));changes=[];mapping=[]
    numeric={'기초금액','예정가격','1순위투찰금액','1순위기초대비','1순위사정율(100%)','1순위사정율(0%)','예가/기초(100%)','예가/기초(0%)','업체수'}
    def equivalent(c,a,b):
        if c=='개찰일':return engine.rules.parse_date_series(pd.Series([a,b])).nunique(dropna=False)==1
        if c in numeric:
            av,bv=engine.numeric(pd.Series([a,b]));return (pd.isna(av) and pd.isna(bv)) or bool(np.isclose(av,bv,atol=1e-12,rtol=0,equal_nan=True))
        return text(a)==text(b)
    old['source_event_id']=old.get('source_event_id',pd.Series('',index=old.index))
    old['source_url']=old.get('source_url',pd.Series('',index=old.index))
    max_number=pd.to_numeric(old['번호'],errors='coerce').max() if '번호' in old else len(old)
    appended=[]
    for j,row in incoming.iterrows():
        k=newkeys.iloc[j];matched=k in lookup
        index=lookup[k] if matched else None
        event_id=str(old.at[index,'event_id']) if matched else hashlib.sha256(json.dumps(k,ensure_ascii=False).encode()).hexdigest()
        record=old.loc[index].to_dict() if matched else {c:np.nan for c in old.columns}
        for c in original_columns:
            if c=='번호':continue
            v=row[c]
            if matched and not equivalent(c,record[c],v):
                changes.append({'event_id':event_id,'source_event_id':row.source_event_id,'column':c,'before':record[c],'after':v})
            record[c]=v
        if not matched:record['번호']=int(max_number)+len(appended)+1
        record.update(event_id=event_id,source_event_id=row.source_event_id,source_url=row.source_url,
                      source_task='supervision-20261109/'+row.source_event_id,source_profile='power_services',
                      source_csv='incoming.pkl',source_csv_row=j+2,source_excel=row.source_excel,source_excel_row=row.source_excel_row,
                      source_profiles=';'.join(sorted(set(text(record.get('source_profiles','')).split(';'))|{'power_supervision'})).strip(';'))
        if matched:
            for c,v in record.items():old.at[index,c]=v
        else:appended.append(record)
        mapping.append({'source_event_id':row.source_event_id,'event_id':event_id,'action':'refresh_existing' if matched else 'add_new'})
    merged=pd.concat([old,pd.DataFrame(appended)],ignore_index=True)
    if keyed(merged,keys).duplicated().any():raise AssertionError('Merged full event key is not unique')
    return merged,pd.DataFrame(mapping),pd.DataFrame(changes)

def quality(frame,as_of):
    target=engine.numeric(frame[engine.RATE]);winner=engine.numeric(frame[engine.WINNER]);dates=engine.rules.parse_date_series(frame['개찰일'])
    statuses=np.where(target.notna()&winner.notna(),'both_numeric',np.where(target.notna(),'target_only_numeric','target_not_numeric'))
    flags={'missing_name':frame['공고명'].fillna('').astype(str).str.strip().eq(''),
           'missing_org':frame['발주기관'].fillna('').astype(str).str.strip().eq(''),
           'missing_date':dates.isna(),'future_opening':dates.gt(pd.Timestamp(as_of)),
           'abnormal_target':target.notna()&(~np.isfinite(target)|target.abs().ge(10)),
           'abnormal_winner':winner.notna()&(~np.isfinite(winner)|winner.abs().ge(10))}
    frame['quality_status']=statuses
    frame['quality_warnings']=[';'.join(k for k,v in flags.items() if v.iloc[i]) for i in range(len(frame))]
    return {k:int((statuses==k).sum()) for k in ['both_numeric','target_only_numeric','target_not_numeric']},{k:int(v.sum()) for k,v in flags.items()}

def build(base,collection,out,as_of='2026-11-09'):
    base=Path(base);collection=Path(collection);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'));columns=manifest['original_columns']
    frozen=out/'frozen_before';frozen.mkdir(exist_ok=True)
    hashes={}
    for name in ['operational.pkl','training.pkl','manifest.json','신규_운영_낙찰데이터_20261001.xlsx','source_rows.csv.gz','source_excel_lineage.csv.gz','classification_review.csv']:
        src=base/name;dst=frozen/name;hashes[name]=sha(src)
        if dst.exists():assert sha(dst)==hashes[name]
        else:shutil.copy2(src,dst);dst.chmod(0o444)
    (frozen/'hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    old=pd.read_pickle(base/'operational.pkl');incoming=pd.read_pickle(out/'incoming.pkl')
    lineage=pd.read_csv(base/'source_excel_lineage.csv.gz',keep_default_na=False)
    old=old.merge(lineage[['task','source_csv_row','source_excel','source_excel_row']],left_on=['source_task','source_csv_row'],right_on=['task','source_csv_row'],how='left',validate='one_to_one').drop(columns=['task'])
    merged,mapping,changes=merge_frames(old,incoming,columns)
    oldraw=pd.read_csv(base/'source_rows.csv.gz',keep_default_na=False)
    newraw=incoming.copy();newraw['source_profile']='power_services';newraw['source_task']='supervision-20261109/'+newraw.source_event_id;newraw['source_csv']='incoming.pkl';newraw['source_csv_row']=np.arange(2,len(newraw)+2)
    newraw['event_id']=newraw.source_event_id.map(mapping.set_index('source_event_id').event_id)
    raw=pd.concat([oldraw,newraw],ignore_index=True)
    numeric_status,warnings=quality(raw,as_of);quality(merged,as_of)
    exceptions=json.loads((collection/'source-exceptions.json').read_text(encoding='utf-8'))
    exception_ids={x['id'] for x in exceptions}
    affected=merged.source_event_id.isin(exception_ids)
    merged.loc[affected,'quality_warnings']=merged.loc[affected,'quality_warnings'].map(lambda v:(';'.join([v,'participant_disclosure_exception'])).strip(';'))
    for c in [engine.RATE,engine.WINNER,'기초금액']:merged[c]=engine.numeric(merged[c])
    merged['개찰일']=engine.rules.parse_date_series(merged['개찰일'])
    training=engine.prepare_history(merged,as_of)
    raw.to_csv(out/'source_rows.csv.gz',index=False,encoding='utf-8-sig',compression='gzip')
    merged.to_pickle(out/'operational.pkl');training.to_pickle(out/'training.pkl')
    mapping.to_csv(out/'event_mapping.csv',index=False,encoding='utf-8-sig');changes.to_csv(out/'value_changes.csv',index=False,encoding='utf-8-sig')
    newlin=pd.DataFrame({'task':newraw.source_task,'profile':newraw.source_profile,'source_csv':newraw.source_csv,'source_csv_row':newraw.source_csv_row,'source_excel':newraw.source_excel,'source_excel_row':newraw.source_excel_row,'source_event_id':newraw.source_event_id,'source_url':newraw.source_url,'event_id':newraw.event_id})
    pd.concat([lineage,newlin],ignore_index=True).to_csv(out/'source_excel_lineage.csv.gz',index=False,encoding='utf-8-sig',compression='gzip')
    shutil.copy2(base/'classification_review.csv',out/'classification_review.csv')
    added=int(mapping.action.eq('add_new').sum());overlap=len(incoming)-added
    update={'source_events':len(incoming),'overlap_events':overlap,'added_events':added,'updated_value_events':int(changes.event_id.nunique()) if len(changes) else 0,'changed_fields':changes.column.value_counts().to_dict() if len(changes) else {},'source_exception_events':len(exceptions),'participant_rows_preserved':471448,'collection_batches_completed':25,'collection_batches_total':25,'participant_rows_used_as_training_events':0}
    manifest.update(version='collection-20261109-supervision-8482',as_of=as_of,source='Info21C 검증 장기수집 + 감리용역 추가 수집',source_rows=len(raw),operational_rows=len(merged),training_rows=len(training),numeric_status=numeric_status,warnings=warnings,data_start_date=str(training._date.min().date()),data_end_date=str(training._date.max().date()),supervision_update=update,
                    unresolved='기존 미해결 1건: 2024-05-29 전기공사 화면29/Excel26(부분자료 제외). 감리 원문 공개 예외 8건: 업체 미공개 4건·표시 0/업체행 공존 4건(원본 보존).',
                    previous_version=manifest['version'],previous_operational_sha256=hashes['operational.pkl'])
    manifest['source_hashes']['supervision_archive']=sha(collection/'감리용역_2년_전체내역_20261109.zip')
    manifest['operational_data_sha256']=ledger_metadata.data_signature(merged,columns)
    manifest['classification_review_learning_rows']=int(training.event_id.isin(set(pd.read_csv(out/'classification_review.csv').event_id)).sum())
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    assert all(sha(base/name)==digest and sha(frozen/name)==digest for name,digest in hashes.items())
    print(json.dumps({'source_rows':len(raw),'operational_rows':len(merged),'training_rows':len(training),'update':update},ensure_ascii=True),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--collection',required=True);p.add_argument('--out',required=True);p.add_argument('--build',action='store_true');a=p.parse_args();(build if a.build else audit)(a.base,a.collection,a.out)
