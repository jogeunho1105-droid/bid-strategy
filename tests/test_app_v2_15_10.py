"""Regression checks for the v2.15.10 default-screen lower-limit column."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "입찰 앱.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = APP.split("# ── 요약 테이블: 최종 추천값과 근거만 표시", 1)[1].split("# ── 건별 상세", 1)[0]


class DefaultScreenLowerLimitV21510Tests(unittest.TestCase):
    def test_version_updated(self):
        self.assertIn('MODEL_VERSION = "v2.15.10"', APP)
        self.assertIn("투찰전략 분석 시스템 v2.15.10", APP)
        self.assertTrue(README.startswith("# 투찰전략 분석 시스템 v2.15.10"))

    def test_summary_includes_lower_limit_after_name(self):
        expected = ["중심모델군", "공고명", "낙찰하한율", "업체1추천", "업체2추천", "업체3추천", "비고"]
        columns = SUMMARY.split("summary_columns=[", 1)[1].split("]", 1)[0]
        positions = [columns.index(f'"{name}"') for name in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('"낙찰하한율":format_lower_limit_rate(b.get("lower_limit_rate"))', SUMMARY)

    def test_original_value_formatter_and_download_remain(self):
        self.assertIn("def format_lower_limit_rate", APP)
        self.assertIn("📥 투찰전략 요약 다운로드", APP)
        self.assertIn("def make_strategy_summary_excel", APP)
        self.assertIn("누락·비정상 값은 `미확인`", README)


if __name__ == "__main__":
    unittest.main()
