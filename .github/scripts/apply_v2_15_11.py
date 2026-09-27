"""Apply the reviewed UI integration to the pinned v2.15.10 source only."""
from pathlib import Path
import ast
import hashlib
import re

p=Path('입찰 앱.py'); text=p.read_text(encoding='utf-8')
if 'MODEL_VERSION = "v2.15.11"' in text:
    print('Already integrated; running verification only.')
    raise SystemExit(0)
raw=p.read_bytes()
sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
if sha!='1667c7e8a0c55e68dec446157ab23e37ff701b76':
    raise SystemExit('App baseline changed. Review instead of overwrite: '+sha)

def once(old,new):
    global text
    if text.count(old)!=1:
        raise RuntimeError('Expected one source anchor: '+old[:90]+'; got '+str(text.count(old)))
    text=text.replace(old,new,1)

# Top-level import only; keep local imports for AST-based existing tests.
once('import bid_engine\n\nplt.rcParams', 'import bid_engine\nimport bid_strategies\n\nplt.rcParams')
text=text.replace('투찰전략 분석 시스템 v2.15.10','투찰전략 분석 시스템 v2.15.11')
once('MODEL_VERSION = "v2.15.10"','MODEL_VERSION = "v2.15.11"')
once('def predict_final_batch(bids, history, policy=None):','def predict_final_batch(bids, history, policy=None, strategy_mode=None):')
once('    """Build one local engine per batch; malformed announcement dates stay blank."""\n', '''    """Build one local engine per batch; malformed announcement dates stay blank."""
    if strategy_mode is not None:
        import bid_strategies
        predictions=bid_strategies.recommend_bids(bids,history,policy,mode=strategy_mode)
        return [{"prediction":p,"recommendations":bid_strategies.recommendation_records(b,p),
                 "error":(p or {}).get("error","")} for b,p in zip(bids,predictions)]
''')
once('    """Adapt final engine rates for screen/Excel; never apply a second correction."""\n', '''    """Adapt final engine rates for screen/Excel; never apply a second correction."""
    if prediction is not None and "selected_strategy" in prediction:
        import bid_strategies
        return bid_strategies.recommendation_records(bid,prediction)
''')
once('    mode=st.radio("모드 선택",["📊 투찰전략 분석","🔧 배포자 관리"])', '''    mode=st.radio("모드 선택",["📊 투찰전략 분석","🔧 배포자 관리"])
    strategy_display=st.selectbox("전략 선택",list(bid_strategies.MODES))
    strategy_mode=bid_strategies.MODES[strategy_display]
    st.caption("관측 1위 기반 시험운영입니다. S1은 기존 계산 기준을 유지합니다.")''')
start=text.index('        <b>📌 분석 방법</b><br>'); end=text.index('        <b>정렬:</b>',start)
text=text[:start]+'''        <b>📌 적용 전략: {strategy_display}</b><br>
        <b>자동 선택:</b> 전기공사 S4 · 건설사업관리 S3 · 기존사업 S3<br>
        <b>근거:</b> 2026.09.27 연구의 분야별 관측 가격구간 통과율 1위<br>
        <b>주의:</b> 실제 낙찰확률·미래 우위가 아닙니다. 기존사업의 통계적 우위는 미확정입니다.<br>
        <b>소표본:</b> 동일범위 2년 → 최대4년 → S3 대체 → 참고 중심값 → 산정보류<br>
'''+text[end:]
once('            raw_bytes = xls_file.read()','            raw_bytes = xls_file.getvalue()')
once('            bids = parse_xls(raw_bytes, xls_file.name)', '''            bids = parse_xls(raw_bytes, xls_file.name)
            original_bid_count=len(bids)
            bids,revision_exclusions=bid_strategies.latest_notices(bids)''')
once('    st.success(f"✅ {len(bids)}건 확인")', '''    st.success(f"✅ 원본 {original_bid_count}행 · 구차수/중복 {len(revision_exclusions)}행 제외 · 최종 {len(bids)}건")
    if revision_exclusions:
        with st.expander("정정공고 제외내역"):
            st.dataframe(pd.DataFrame(revision_exclusions),hide_index=True,use_container_width=True)
    st.warning("관측 1위 기반 시험운영입니다. 추천기준금액은 최종 투찰금액이 아니며, 참가자격·A값·최저가격 조건은 공고 원문으로 확인하세요.")''')
once('            batch_predictions=predict_final_batch(bids,df_c)','            batch_predictions=predict_final_batch(bids,df_c,strategy_mode=strategy_mode)')
summary=text.index('    # ── 요약 테이블: 최종 추천값과 근거만 표시');idx=text.index('        notes=[]',summary)
text=text[:idx]+'''        prediction=row.get("prediction") or {}
        notes=[f"전략: {prediction.get('selected_strategy','미확인')} / 상태: {prediction.get('status','미확인')}"]
        notes.extend(prediction.get("fallback_notes",[]))'''+text[idx+len('        notes=[]'):]
once('    excel_buf=make_excel_simple(results,quality_info)','    excel_buf=bid_strategies.add_review_sheets(make_excel_simple(results,quality_info),results,revision_exclusions)')
once('    summary_excel_buf=make_strategy_summary_excel(results)','    summary_excel_buf=bid_strategies.add_review_sheets(make_strategy_summary_excel(results),results,revision_exclusions)')
text+='''
    st.download_button("📥 사전추천 계산기록 JSON 저장",
        data=bid_strategies.snapshot(results,revision_exclusions),
        file_name=f"투찰전략_계산기록_{today_str}.json",mime="application/json",use_container_width=True)
'''
ast.parse(text);p.write_text(text,encoding='utf-8')

p=Path('README.md');s=p.read_text(encoding='utf-8')
s=s.replace('# 투찰전략 분석 시스템 v2.15.10','# 투찰전략 분석 시스템 v2.15.11',1)
s=s.replace('v2.15.10은 사용자 승인에 따라 기본 요약화면에도 입찰서류함 원본 낙찰하한율을 표시하는 배포 버전입니다.','v2.15.11은 정정공고 최고차수와 분야별 관측1위 전략, 표본부족 대체를 제공하는 시험운영 버전입니다. 이번 신규 기능의 미래 낙찰성능은 아직 확정되지 않았습니다.')
s=s.replace('v2.15.10은 실행파일','v2.15.11은 실행파일')
s=s.replace('| `bid_rules.py` |','| `bid_strategies.py` | 정정차수·전략선택·소표본 대응·계산기록 |\n| `bid_rules.py` |',1)
section='''## v2.15.11 시험운영 기준

기본 선택은 **분야별 관측 1위 · 시험운영**입니다. 전기공사 S4, 건설사업관리 및 기존사업 S3를 적용하고 S1~S5를 직접 선택해 비교할 수도 있습니다. 새 정책의 자동 적용시작일은 2026-09-28이며 이전 공고 자동모드는 S1입니다. 연구 관측률은 실제 개별공고의 낙찰확률이 아니고, 기존사업 S3의 미래 우위는 확정되지 않았습니다.

현재 업로드 공고는 공고번호의 마지막 숫자 차수가 가장 큰 행만 사용합니다. 원본을 삭제하지 않고 구차수 제외내역을 화면과 Excel에 남깁니다. 최고차수 내용 충돌·취소는 강제 계산하지 않습니다. 과거 이력 전체를 미래 정정차수에 맞추어 일괄 삭제하지 않습니다.

같은 모델군·입찰범위 안에서 2년→4년으로 표본을 확장하고, 1순위구간이 부족하면 S3 분포로 대체합니다. 소표본은 강건중심 참고값, 5건 미만은 산정보류입니다. 최근90일의 기관·세부업종 표본과 실제 적용전략, 대체 이유를 표시합니다. 대체값에 기존 전략의 관측 통과율을 보장값으로 적용하지 않습니다. 한전 단가감리의 금액·입찰범위 미확인은 보류합니다.

두 Excel의 기존 첫 시트는 유지하고 `전략선택근거`, `정정공고제외`를 추가합니다. `사전추천 계산기록 JSON 저장`은 사용자 PC에만 저장하며 원본 낙찰이력·참여목록을 공개 저장소에 업로드하지 않습니다. 운영 확인은 버전·전략선택·업로드·정정제외·다운로드와 기존 낙찰이력 보존을 확인해야 합니다.

기준·한계: `docs/change_requests/v2.15.11.md`. 원 데이터의 연구 결과와 모의/회귀검증은 구분하여 기록합니다.

'''
s=s.replace('## 설치 및 실행\n',section+'## 설치 및 실행\n',1);p.write_text(s,encoding='utf-8')
p=Path('AGENTS.md');s=p.read_text(encoding='utf-8')
s+='''
## v2.15.11 추가 운영원칙
- 현재 업로드 공고는 숫자 최고 정정차수만 분석하고 원본·제외내역을 보존한다.
- 과거 이력을 미래 정정차수로 소급 정리하여 백테스트에 미래정보를 반영하지 않는다.
- 관측 1위는 실제 낙찰확률 보장이 아니다. 시험운영 경고와 S1 비교선택을 유지한다.
- 소표본 대체는 모델군·입찰범위를 유지하며 적용전략·표본수·기간·이유를 표시한다.
- 표본5건 미만, 미분류, 한전 단가총액 미확인, 최고차수 충돌·취소는 산정보류한다.
- 원본·운영 DB·회사 참여자료는 덮어쓰거나 공개하지 않는다.
''';p.write_text(s,encoding='utf-8')
# Update only live version assertions, never previous-policy evidence or formulas.
for p in Path('tests').glob('test*.py'):
    lines=[]
    for line in p.read_text(encoding='utf-8').splitlines(keepends=True):
        if 'assert' in line and any(k in line for k in ('MODEL_VERSION =','투찰전략 분석 시스템 v2.15.','values["모델버전"]')):
            line=re.sub(r'v2\.15\.(?:7|8|9|10)', 'v2.15.11',line)
        lines.append(line)
    p.write_text(''.join(lines),encoding='utf-8')
print('v2.15.11 integration ready for tests; core engine, rules, legacy and policy unchanged.')
