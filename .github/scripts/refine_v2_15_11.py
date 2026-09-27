"""Small-pool guard found by private full-data execution, plus truthful labels."""
from pathlib import Path
p=Path('bid_strategies.py');s=p.read_text(encoding='utf-8')
def replace(old,new):
    global s
    if new in s:return
    if s.count(old)!=1:raise RuntimeError('Refinement anchor mismatch: '+old[:80])
    s=s.replace(old,new,1)
replace('    if not len(a):return None\n    lo,hi=np.quantile(a,[.1,.9]);trimmed=a[(a>=lo)&(a<=hi)]',
'''    if not len(a):return None
    if len(a)<5:return float(np.mean(a))
    lo,hi=np.quantile(a,[.1,.9]);trimmed=a[(a>=lo)&(a<=hi)]''')
replace('else "관측1위·시험" if selected in ("S3","S4") else "연구전략·시험"','else "연구전략·시험"')
replace('        if scope.get("scope_assumed"):meta["fallback_notes"].append',
'''        if selected=="S1" and meta.get("selected_strategy")=="S1":
            meta["window_days"]="현행모형별 기존기간"
        elif mode=="auto" and meta.get("selected_strategy")==selected and meta.get("window_days")==730 and meta.get("sample_status")=="연구전략·시험":
            meta["sample_status"]="관측1위·시험"
        if scope.get("scope_assumed"):meta["fallback_notes"].append''')
replace('    notes="; ".join(prediction.get("fallback_notes",[]))\n    basis=',
'''    notes="; ".join(prediction.get("fallback_notes",[]))
    window=prediction.get("window_days",730)
    period=f"{window}일" if isinstance(window,int) else str(window)
    basis=''')
replace('f"최근90일 n={prediction.get(\'recent90_n\',0)}; 과거 {prediction.get(\'window_days\',730)}일; "','f"최근90일 n={prediction.get(\'recent90_n\',0)}; 기간 {period}; "')
p.write_text(s,encoding='utf-8')
p=Path('tests/test_strategies_v2_15_11.py');text=p.read_text(encoding='utf-8')
if 'class SmallIssuerGuardTests(unittest.TestCase):' not in text:
    text+='''

class SmallIssuerGuardTests(unittest.TestCase):
    def test_robust_two_observations_is_finite(self):
        self.assertAlmostEqual(strategy.robust_center([{"value":-.5},{"value":.7}]),.1)

    def test_sparse_issuer_s2_parity_with_engine(self):
        h=history();h.loc[:87,"발주기관"]="다른시청"
        expected=engine.recommend_bid(bid(),h,{"rules":{}})
        actual=strategy.recommend_bids([bid()],h,{"rules":{}},"S2")[0]
        self.assertTrue(all(np.isfinite(x) for x in actual["rates"]))
        self.assertEqual(actual["rates"],expected["recommendations"]["family_center"])

    def test_manual_selection_does_not_claim_observed_first(self):
        p=strategy.recommend_bids([bid()],history(),mode="S4")[0]
        self.assertNotIn("관측1위",p["status"])

    def test_s1_window_label_is_not_false_two_year_limit(self):
        p=strategy.recommend_bids([bid()],history(),mode="S1")[0]
        self.assertEqual(p["window_days"],"현행모형별 기존기간")
'''
    p.write_text(text,encoding='utf-8')
print('Small-issuer guard and exact strategy/period labels applied.')
