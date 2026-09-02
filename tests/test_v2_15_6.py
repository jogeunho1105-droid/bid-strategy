from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ANALYSIS = Path(__file__).resolve().parents[1] / "analysis"
if str(ANALYSIS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS))

import backtest_v2_15_6 as audit
import evaluate_core_models_v2_15_6 as core
import evaluate_virtual_win_v2_15_6 as virtual
import evaluate_virtual_win_candidates_v2_15_6 as win_candidates


class V2156AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = audit.load_baseline_module()

    def test_v2155_busan_company_count_boundary(self):
        row = {
            "date": pd.Timestamp("2026-08-31"),
            "org": "한국전력공사 부산울산본부",
            "svc": "supervision",
            "base": 100_000_000,
        }
        self.assertEqual(audit.v2155_scope(self.module, row), ("지역제한", 3))
        self.assertEqual(audit.v2155_scope(self.module, {**row, "base": 99_999_999}), ("지역제한", 2))

    def test_portfolio_error_is_explicitly_not_center_error(self):
        error, signed, best, position = audit.evaluate(0.0, [-0.2, 0.4, 0.8])
        self.assertAlmostEqual(error, 0.2)
        self.assertAlmostEqual(signed, -0.2)
        self.assertEqual(best, -0.2)
        self.assertEqual(position, 1)
        self.assertNotEqual(error, abs(0.4))

    def test_virtual_win_uses_strict_boundaries(self):
        self.assertFalse(virtual.virtual_win(0.0, 0.0, 0.30))
        self.assertTrue(virtual.virtual_win(0.0, 0.10, 0.30))
        self.assertFalse(virtual.virtual_win(0.0, 0.30, 0.30))
        self.assertFalse(virtual.virtual_win(0.0, 0.40, 0.30))

    def test_notice_win_requires_any_company_inside_band(self):
        self.assertTrue(virtual.notice_virtual_win(0.0, [-0.10, 0.20, 0.40], 0.30))
        self.assertFalse(virtual.notice_virtual_win(0.0, [-0.10, 0.30, 0.40], 0.30))

    def test_nonpositive_band_is_not_evaluable(self):
        self.assertFalse(virtual.is_evaluable(0.30, 0.30))
        self.assertFalse(virtual.is_evaluable(0.30, 0.20))
        self.assertFalse(virtual.notice_virtual_win(0.30, [0.25], 0.20))

    def test_virtual_win_detail_separates_win_loss_and_unevaluable(self):
        detail = pd.DataFrame(
            {
                "개찰일": pd.to_datetime(["2026-09-01"] * 4),
                "공고번호": ["A", "B", "C", "D"],
                "공고명": ["a", "b", "c", "d"],
                "발주기관": ["기관"] * 4,
                "분석구분": ["일반"] * 4,
                "용역분류": ["감리"] * 4,
                "참여업체수": [3, 1, 1, 1],
                "실제사정율": [0.0, 0.0, 0.0, 0.3],
                "운영추천1": [-0.1, 0.0, 0.1, 0.25],
                "운영추천2": [0.2, None, None, None],
                "운영추천3": [0.4, None, None, None],
            }
        )
        source = pd.DataFrame({virtual.WINNER_COLUMN: [0.3, 0.3, None, 0.2]})
        result = virtual.evaluate_detail(detail, source)
        self.assertEqual(result["판정상태"].tolist(), ["판정가능", "판정가능", "1순위사정율누락", "낙찰구간비양수"])
        self.assertEqual(result["가상낙찰"].tolist()[:2], [True, False])
        self.assertTrue(pd.isna(result.loc[2, "가상낙찰"]))
        self.assertTrue(pd.isna(result.loc[3, "가상낙찰"]))
        self.assertEqual(result.loc[0, "가상낙찰업체"], "2")
        self.assertEqual(result.loc[1, "실패유형"], "전추천_예가기초이하")

    def test_positive_offset_is_applied_to_every_available_recommendation(self):
        frame = pd.DataFrame(
            {
                "참여업체수": [3, 1],
                "실제사정율": [0.0, 0.0],
                "1순위사정율": [0.3, 0.3],
                "운영추천1": [-0.1, -0.1],
                "운영추천2": [0.1, None],
                "운영추천3": [0.4, None],
            }
        )
        baseline, _ = win_candidates.shifted_flags(frame, 0.0)
        shifted, _ = win_candidates.shifted_flags(frame, 0.15)
        self.assertEqual(baseline.tolist(), [True, False])
        self.assertEqual(shifted.tolist(), [True, True])

    def synthetic_core_frame(self, candidate_dev_error: float, candidate_holdout_error: float) -> pd.DataFrame:
        dates = pd.date_range("2023-09-01", "2026-09-01", periods=900)
        cutoff = pd.Timestamp("2025-09-01")
        frame = pd.DataFrame({"개찰일": dates})
        for candidate in core.CANDIDATES:
            frame[f"error__{candidate}"] = 0.20
        frame["error__current"] = 0.10
        frame["error__blend_mean"] = frame["개찰일"].apply(
            lambda value: candidate_dev_error if value < cutoff else candidate_holdout_error
        )
        return frame

    def test_holdout_cannot_select_a_candidate_rejected_in_development(self):
        frame = self.synthetic_core_frame(candidate_dev_error=0.11, candidate_holdout_error=0.01)
        decision = core.choose_for_segment(frame, pd.Timestamp("2026-09-01"), "STANDARD|design")
        self.assertEqual(decision["개발선택"], "current")
        self.assertFalse(decision["운영반영승인"])

    def test_candidate_needs_minimum_development_effect(self):
        frame = self.synthetic_core_frame(candidate_dev_error=0.096, candidate_holdout_error=0.01)
        decision = core.choose_for_segment(frame, pd.Timestamp("2026-09-01"), "STANDARD|design")
        self.assertEqual(decision["개발선택"], "current")
        self.assertFalse(decision["운영반영승인"])

    def test_stable_candidate_can_pass_all_gates(self):
        frame = self.synthetic_core_frame(candidate_dev_error=0.08, candidate_holdout_error=0.08)
        decision = core.choose_for_segment(frame, pd.Timestamp("2026-09-01"), "STANDARD|design")
        self.assertEqual(decision["개발선택"], "blend_mean")
        self.assertTrue(decision["운영반영승인"])


if __name__ == "__main__":
    unittest.main()
