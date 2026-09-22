"""Regression tests for v2.15.9 lower-limit parsing and summary Excel."""
import io
from pathlib import Path
import sys
import unittest

import numpy as np
from openpyxl import Workbook, load_workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_v2_15_4 import load_app_namespace


class StrategySummaryV2159Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=load_app_namespace()

    def test_lower_limit_parser(self):
        parse=self.app["parse_lower_limit_rate"]
        self.assertEqual(parse(89.745),89.745)
        self.assertEqual(parse("89.745%"),89.745)
        self.assertEqual(parse(" 88 "),88.0)
        self.assertEqual(parse(0.89745),89.745)
        self.assertIsNone(parse(None))
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("오류"))
        self.assertIsNone(parse(np.nan))

    def test_xlsx_parser_reads_lower_limit(self):
        wb=Workbook(); ws=wb.active
        ws.append([None,None,"샘플 공고"])
        ws.append(["분류","번호","공고명","공고번호","기초금액","투찰마감","지역","참가마감","투찰유무","발주기관","업종","낙찰하한율"])
        ws.append(["공사",1,"샘플 전기공사","B1",100000000,"26.09.30 (14:00)","전국","","미투찰","발주처","전기","89.745%"])
        buf=io.BytesIO(); wb.save(buf)
        bids=self.app["parse_xls"](buf.getvalue(),"sample.xlsx")
        self.assertEqual(len(bids),1)
        self.assertEqual(bids[0]["lower_limit_rate"],89.745)

    def test_both_parser_branches_capture_lower_limit(self):
        app_text=(Path(__file__).resolve().parents[1]/"입찰 앱.py").read_text(encoding="utf-8")
        self.assertEqual(app_text.count('"lower_limit_rate": parse_lower_limit_rate(row.get("낙찰하한율"))'),2)

    def test_summary_excel_layout_and_values(self):
        results=[
            {"bid":{"name":"1개사 공고","lower_limit_rate":89.745},"scope_info":{"company_count":1},"recommendations":[{"rate":0.0161}]},
            {"bid":{"name":"2개사 공고","lower_limit_rate":"88%"},"scope_info":{"company_count":2},"recommendations":[{"rate":-0.5479},{"rate":0.0164}]},
            {"bid":{"name":"3개사 공고","lower_limit_rate":None},"scope_info":{"company_count":3},"recommendations":[{"rate":-0.7154},{"rate":-0.1254},{"rate":-0.36}]},
        ]
        out=self.app["make_strategy_summary_excel"](results)
        wb=load_workbook(io.BytesIO(out.getvalue()))
        ws=wb["투찰전략"]
        self.assertEqual([ws.cell(2,c).value for c in range(1,6)],["공고명","낙찰하한율","업체1추천","업체2추천","업체3추천"])
        self.assertEqual([ws.cell(3,c).value for c in range(1,6)],["1개사 공고","89.745%","+0.0161%","참여대상 없음","참여대상 없음"])
        self.assertEqual([ws.cell(4,c).value for c in range(1,6)],["2개사 공고","88%","-0.5479%","+0.0164%","참여대상 없음"])
        self.assertEqual([ws.cell(5,c).value for c in range(1,6)],["3개사 공고","미확인","-0.7154%","-0.1254%","-0.3600%"])
        self.assertEqual(ws.freeze_panes,"A3")
        self.assertEqual(ws.auto_filter.ref,"A2:E5")

    def test_download_button_and_version_present(self):
        app_text=(Path(__file__).resolve().parents[1]/"입찰 앱.py").read_text(encoding="utf-8")
        self.assertIn('MODEL_VERSION = "v2.15.10"',app_text)
        self.assertIn("📥 투찰전략 요약 다운로드",app_text)
        self.assertIn("투찰전략_요약_",app_text)


if __name__=="__main__":
    unittest.main()
