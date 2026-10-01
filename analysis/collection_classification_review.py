"""Preserve both source industry labels without changing the frozen classifier."""
import pandas as pd
import bid_rules

def review(raw, learning):
    dual=raw[raw.event_id.duplicated(False)].copy()
    dual['classified_family']=[bid_rules.classify_model_family(n,i,o) for n,i,o in zip(dual['공고명'],dual['업종'],dual['발주기관'])]
    counts=dual.groupby('event_id').classified_family.nunique()
    keys=set(counts[counts.gt(1)].index)
    rows=[]
    for event_id,group in dual[dual.event_id.isin(keys)].groupby('event_id'):
        service=group[group.source_profile.eq('service')].iloc[0];electric=group[group.source_profile.eq('electric')].iloc[0]
        rows.append({'event_id':event_id,'공고번호':service['공고번호'],'공고명':service['공고명'],'발주기관':service['발주기관'],'용역_원문업종':service['업종'],'전기공사_원문업종':electric['업종'],'용역_기존분류':service['classified_family'],'전기공사_기존분류':electric['classified_family'],'대표출처':'service','처리상태':'분류 원문확인 필요; 기존 분류·출처 우선정책 적용','용역_source_csv':service['source_csv'],'용역_source_row':service['source_csv_row'],'전기공사_source_csv':electric['source_csv'],'전기공사_source_row':electric['source_csv_row']})
    return pd.DataFrame(rows),len(learning[learning.event_id.isin(keys)])
