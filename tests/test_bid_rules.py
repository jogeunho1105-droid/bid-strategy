"""Participation boundaries and classification fixtures independent of Streamlit."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bid_rules import (
    classify_bid_scope, classify_model_family, classify_service, historical_scope,
    is_below_notice, parse_date_value, year_from_value,
)


class ClassificationTests(unittest.TestCase):
    def test_requested_six_families(self):
        cases = [
            ("도로 건설사업관리", "", "지방자치단체", "construction_management"),
            ("교량 정밀안전진단", "", "지방자치단체", "diagnosis"),
            ("배전 설계", "", "한국전력공사", "design"),
            ("학교 감리", "", "교육청", "supervision"),
            ("배전 감리", "", "한국전력공사 경북본부", "kepco_supervision"),
            ("공동주택 감리", "", "주택조합", "residential_supervision"),
        ]
        for name, industry, org, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify_model_family(name, industry, org), expected)

    def test_residential_does_not_swallow_other_services(self):
        self.assertEqual(classify_model_family("아파트 설계", "", "시청"), "design")
        self.assertEqual(classify_model_family("주거시설 건설사업관리", "", "시청"), "construction_management")
        self.assertEqual(classify_model_family("공동주택 안전진단", "", "시청"), "diagnosis")
        self.assertEqual(classify_model_family("주거시설", "전력감리", "시청"), "residential_supervision")
        self.assertEqual(classify_model_family("공동주택 감리", "", "한국전력공사"), "kepco_supervision")

    def test_diagnostic_subtypes_preserved(self):
        for name, expected in [("vlf 진단", "VLF"), ("부분방전", "PD"), ("광학", "optical"), ("콘크리트", "concrete"), ("초음파", "ultrasound")]:
            with self.subTest(name=name):
                self.assertEqual(classify_service(name), expected)
                self.assertEqual(classify_model_family(name), "diagnosis")

    def test_industry_fallback_and_electric_service_protection(self):
        cases = [
            ("노후 전원장치 개선", "전기", "electric_construction"),
            ("배선 개선", "전기공사업", "electric_construction"),
            ("전기공사", "전력감리", "supervision"),
            ("전기공사", "전기,전력설계", "design"),
            ("전기공사 감리", "전기", "supervision"),
            ("배전 상주 업무", "전력감리", "supervision"),
            ("청사 시설 업무", "안전진단", "diagnosis_other"),
            ("장비 구매", "물품", "other"),
            ("전기설비 업무", "전기안전관리", "other"),
        ]
        for name, industry, expected in cases:
            with self.subTest(name=name, industry=industry):
                self.assertEqual(classify_service(name, industry), expected)

    def test_contract_work_precedes_material_and_instrument_keywords(self):
        self.assertEqual(classify_service("신한울3,4호기 콘크리트 시험실 신축 설계용역"), "design")
        self.assertEqual(classify_service("부산해군과학기술고등학교 콘크리트 모듈러 설치 설계용역"), "design")
        self.assertEqual(classify_service("부사지구 초음파수위계 및 보조릴레이 교체공사", "전기"), "electric_construction")
        self.assertEqual(classify_service("콘크리트 구조물 건설사업관리"), "construction_management")
        self.assertEqual(classify_service("초음파 검사장비 설치공사 감리"), "supervision")
        self.assertEqual(classify_service("VLF 진단 장비를 이용한 진단 용역"), "VLF")
        self.assertEqual(classify_service("PD 진단장비 구매", "물품"), "other")
        self.assertEqual(classify_service("부분방전 장비 구매", "물품"), "other")


class ParticipationTests(unittest.TestCase):
    def bid(self, base, org="한국전력공사 부산울산본부", name="배전 감리", deadline="26.09.01"):
        return {"name": name, "industry": "", "org": org, "base": base, "deadline": deadline}

    def test_busan_one_hundred_million_boundary(self):
        for base, expected in [(99_999_999, 2), (100_000_000, 3)]:
            result = classify_bid_scope(self.bid(base))
            self.assertEqual((result["scope"], result["company_count"]), ("지역제한", expected))

    def test_notice_price_boundary_is_exact(self):
        for year, cutoff in [(2022, 231_000_000), (2024, 242_000_000), (2026, 253_000_000)]:
            self.assertTrue(is_below_notice(Decimal(cutoff) - Decimal("0.01"), year))
            self.assertFalse(is_below_notice(cutoff, year))
            result = classify_bid_scope(self.bid(cutoff, deadline=f"{year}-09-01"))
            self.assertEqual(result["scope"], "전국입찰")

    def test_gyeongbuk_daegu_one_company(self):
        for org in ["한국전력공사 경북본부", "한국전력공사 대구본부", "한국전력공사경북본부"]:
            with self.subTest(org=org):
                self.assertEqual(classify_bid_scope(self.bid(100_000_000, org))["company_count"], 1)
                self.assertEqual(classify_bid_scope(self.bid(253_000_000, org))["company_count"], 3)

    def test_vlf_and_other_diagnosis(self):
        for base, expected in [(99_999_999, 2), (100_000_000, 3)]:
            result = classify_bid_scope(self.bid(base, name="VLF 진단"))
            self.assertEqual(result["company_count"], expected)
            self.assertFalse(result["applicable"])
        for name in ["PD 진단", "광학 진단", "안전진단"]:
            self.assertEqual(classify_bid_scope(self.bid(50_000_000, name=name))["company_count"], 3)
        self.assertEqual(classify_bid_scope(self.bid(50_000_000, org="시청", name="VLF 진단"))["company_count"], 3)

    def test_electric_always_single(self):
        result = classify_bid_scope(self.bid(300_000_000, name="전기공사"))
        self.assertEqual((result["scope"], result["company_count"]), ("전기공사 단일참여", 1))

    def test_unknown_branch_explicit_assumption_and_independent_history(self):
        org = "한국전력공사 경기본부"
        result = classify_bid_scope(self.bid(100_000_000, org))
        self.assertEqual((result["scope"], result["company_count"]), ("전국입찰", 3))
        self.assertTrue(result["scope_assumed"])
        self.assertEqual(historical_scope("감리", "", org, 100_000_000, "26.09.01"), "지역제한")
        self.assertEqual(historical_scope("감리", "", org, 253_000_000, "26.09.01"), "전국입찰")

    def test_missing_history_does_not_enter_national_pool(self):
        self.assertEqual(historical_scope("감리", "", "한국전력공사", None, "26.09.01"), "확인필요")
        self.assertEqual(historical_scope("감리", "", "한국전력공사", 100_000_000, None), "확인필요")
        self.assertEqual(historical_scope("설계", "", "한국전력공사", 100_000_000, "26.09.01"), "해당없음")


class DateTests(unittest.TestCase):
    def test_supported_date_formats(self):
        expected = datetime(2026, 9, 1)
        for value in ["26.09.01", "2026-09-01", "2026/9/1", "2026년9월1일", "20260901", expected, expected.date(), "2026-09-01T10:00:00"]:
            with self.subTest(value=value):
                self.assertEqual(parse_date_value(value).date(), expected.date())
                self.assertEqual(year_from_value(value), 2026)

    def test_excel_serial(self):
        expected = datetime(2026, 9, 1)
        serial = (expected - datetime(1899, 12, 30)).days
        for value in [serial, float(serial), str(serial)]:
            self.assertEqual(parse_date_value(value), expected)
            self.assertEqual(year_from_value(value), 2026)

    def test_invalid_dates_remain_unknown(self):
        for value in [None, float("nan"), "NaT", "26.02.30", "unknown"]:
            self.assertIsNone(parse_date_value(value))
        self.assertEqual(year_from_value(None, default_year=2024), 2024)


if __name__ == "__main__":
    unittest.main()
