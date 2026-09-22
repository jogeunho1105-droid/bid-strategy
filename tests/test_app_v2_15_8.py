"""Static regression checks for the v2.15.8 summary-table-only change."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "입찰 앱.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = APP.split("# ── 요약 테이블: 최종 추천값과 근거만 표시", 1)[1].split("# ── 건별 상세", 1)[0]
DETAIL = APP.split("# ── 건별 상세", 1)[1]


class SummaryLayoutV2158Tests(unittest.TestCase):
    def test_version_updated(self):
        self.assertIn('MODEL_VERSION = "v2.15.9"', APP)
        self.assertIn("투찰전략 분석 시스템 v2.15.9", APP)
        self.assertTrue(README.startswith("# 투찰전략 분석 시스템 v2.15.9"))

    def test_summary_title_and_columns(self):
        self.assertIn("최대 3개 업체 추천 사정률 —", SUMMARY)
        self.assertNotIn("추천 사정률·추천기준금액", SUMMARY)
        expected = ["중심모델군", "공고명", "업체1추천", "업체2추천", "업체3추천", "비고"]
        positions = [SUMMARY.index(f'"{name}"') for name in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("summary_df=pd.DataFrame(rows, columns=summary_columns)", SUMMARY)

    def test_amount_columns_removed_only_from_summary(self):
        self.assertNotIn("display_amounts", SUMMARY)
        for name in ("업체1추천기준금액", "업체2추천기준금액", "업체3추천기준금액"):
            self.assertNotIn(name, SUMMARY)
        self.assertIn("recommendation_reference_amount", DETAIL)
        self.assertIn("추천기준금액", DETAIL)
        self.assertIn('f"업체{pos} 추천기준금액(원)"', APP)


if __name__ == "__main__":
    unittest.main()
