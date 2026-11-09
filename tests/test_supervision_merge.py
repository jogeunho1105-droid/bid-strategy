import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
import pandas as pd
import numpy as np
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from analysis.merge_supervision_ledger import merge_frames,keyed
import ledger_metadata
from test_v2_15_4 import load_app_namespace
from test_strategies_v2_15_11 import history

class SupervisionMergeTests(unittest.TestCase):
    def frames(self):
        cols=['번호','공고번호','개찰일','발주기관','공고명','기초금액','예가/기초(0%)','1순위사정율(0%)','업종','지역']
        row=dict(zip(cols,[1,'R000-000',pd.Timestamp('2024-11-01'),'시험청','전기감리 용역',1e8,0.,.1,'전력감리','전국']))
        old=pd.DataFrame([{**row,'event_id':'old-event','source_profiles':'power_services'}])
        incoming=pd.DataFrame([{**row,'개찰일':'24.11.01','source_event_id':'new-source','source_url':'https://example.test/event','source_excel':'original.xlsx','source_excel_row':2}])
        return old,incoming,cols
    def test_overlap_once_keeps_original_event_and_both_lineages(self):
        old,new,cols=self.frames();merged,mapping,changes=merge_frames(old,new,cols)
        self.assertEqual(len(merged),1);self.assertEqual(merged.event_id.iloc[0],'old-event')
        self.assertEqual(mapping.action.tolist(),['refresh_existing']);self.assertTrue(changes.empty)
        self.assertIn('power_supervision',merged.source_profiles.iloc[0]);self.assertEqual(merged.source_event_id.iloc[0],'new-source')
        self.assertEqual(old.source_profiles.iloc[0],'power_services')
    def test_yymmdd_is_normalized_before_datetime_assignment(self):
        old,new,cols=self.frames();merged,_,_=merge_frames(old,new,cols)
        self.assertEqual(merged['개찰일'].iloc[0],pd.Timestamp('2024-11-01'))
        self.assertEqual(keyed(old).iloc[0],keyed(merged).iloc[0])
    def test_missing_winner_stays_missing_and_real_zero_stays_zero(self):
        old,new,cols=self.frames();new.loc[0,'1순위사정율(0%)']=np.nan
        merged,_,changes=merge_frames(old,new,cols)
        self.assertTrue(pd.isna(merged['1순위사정율(0%)'].iloc[0]));self.assertEqual(merged['예가/기초(0%)'].iloc[0],0.)
        self.assertEqual(changes.column.tolist(),['1순위사정율(0%)'])
    def test_amendments_are_not_collapsed_by_base_notice(self):
        old,new,cols=self.frames();new.loc[0,'공고번호']='R000-001'
        merged,mapping,_=merge_frames(old,new,cols)
        self.assertEqual(len(merged),2);self.assertEqual(mapping.action.tolist(),['add_new'])
    def test_ambiguous_full_key_is_rejected(self):
        old,new,cols=self.frames()
        with self.assertRaisesRegex(ValueError,'Ambiguous'):merge_frames(old,pd.concat([new,new]),cols)
    def test_per_version_server_backup_is_not_replaced(self):
        app=load_app_namespace();h=history();app['st']=Mock()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'private-ledger/test-temp') as temp:
            root=Path(temp);hist=root/'history.pkl';qual=root/'history_quality.json'
            original=b'prior private ledger';hist.write_bytes(original);qual.write_text('{}')
            app['DATA_DIR']=temp;app['HISTORY_FILE']=str(hist);app['HISTORY_QUALITY_FILE']=str(qual)
            h.attrs[ledger_metadata.ATTR]={'version':'supervision-test-v1','as_of':'2026-11-09','training_rows':len(h),'data_start_date':'2026-01-01','data_end_date':'2026-04-01'}
            app['save_history'](h)
            backup=root/'frozen_before_collection/supervision-test-v1/history.pkl'
            self.assertEqual(backup.read_bytes(),original)
            saved=hist.read_bytes();app['save_history'](h);self.assertEqual(backup.read_bytes(),original)
            h.attrs[ledger_metadata.ATTR]['version']='supervision-test-v2';app['save_history'](h)
            self.assertEqual((root/'frozen_before_collection/supervision-test-v2/history.pkl').read_bytes(),saved)

if __name__=='__main__':unittest.main()
