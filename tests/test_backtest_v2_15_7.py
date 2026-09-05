"""Independent tests of denominators, time separation, and policy inheritance."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("backtest_v2157_test", ROOT / "analysis" / "backtest_v2_15_7.py")
bt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bt)


def sample(dates):
    n = len(dates)
    result = pd.DataFrame({
        "개찰일": pd.to_datetime(dates), "공고번호": [f"n-{i}" for i in range(n)],
        "발주기관": "기관A", "중심모델군": "설계", "모델구간": "design|기존분석",
        "세부업종": "design", "실제사정율": 0.0, "1순위사정율": 0.3,
        "判定가능": True, "참여업체수": 3,
    })
    for s in bt.STRATEGIES:
        result["win_" + s] = False
        result["mae_" + s] = 0.1
    for i in range(1, 4):
        result[f"role_{i}_active"] = True
        result[f"role_{i}_win"] = False
        result[f"role_{i}_unique"] = False
    return result


class BacktestProtocolTests(unittest.TestCase):
    def test_equal_and_reversed_intervals_are_included_as_losses(self):
        frame = sample(pd.date_range("2026-01-01", periods=4))
        frame["1순위사정율"] = [0.3, 0.0, -0.1, np.nan]
        frame["判定가능"] = [True, True, True, False]
        frame["win_current"] = [True, False, False, False]
        result = bt.metrics(frame)
        self.assertEqual(result["공고수"], 4)
        self.assertEqual(result["판정가능"], 3)
        self.assertAlmostEqual(result["가상낙찰률"], 1 / 3)
        self.assertEqual(result["양수구간판정가능"], 1)
        self.assertEqual(result["양수구간낙찰률"], 1.0)

    def test_day_block_ci_targets_per_notice_not_average_day_win_rate(self):
        large = sample(pd.DatetimeIndex(np.repeat(pd.date_range("2026-01-01", periods=10), 1000)))
        small = sample(pd.date_range("2026-01-11", periods=10))
        small["win_family_center"] = True
        frame = pd.concat([large, small], ignore_index=True)
        ci = bt.block_ci(frame, "family_center", iterations=1000)
        self.assertGreaterEqual(ci[0], 0)
        self.assertLess(ci[1], 0.01)
        self.assertAlmostEqual(bt.metrics(frame, "family_center")["가상낙찰률"], 10 / 10010)

    def test_audit_success_cannot_rescue_a_development_rejected_candidate(self):
        dates = pd.date_range("2024-01-01", periods=300).append(
            pd.date_range("2025-09-01", periods=200)).append(pd.date_range("2026-06-01", periods=90))
        frame = sample(dates)
        frame.loc[frame["개찰일"] >= bt.AUDIT_START, "win_family_center"] = True
        decision = bt.segment_decision("design|기존분석", frame)
        self.assertEqual(decision["selected_before_audit"], "current")
        self.assertFalse(decision["preliminary_accept"])

    def test_audit_results_never_change_frozen_candidate_selection(self):
        dates = pd.date_range("2024-01-01", periods=300).append(
            pd.date_range("2025-09-01", periods=200)).append(pd.date_range("2026-06-01", periods=90))
        frame = sample(dates)
        frame["win_family_center"] = True
        accepted = bt.segment_decision("design|기존분석", frame)
        frame.loc[frame["개찰일"] >= bt.AUDIT_START, "win_family_center"] = False
        rejected = bt.segment_decision("design|기존분석", frame)
        self.assertEqual(accepted["selected_before_audit"], "family_center")
        self.assertEqual(accepted["selected_before_audit"], rejected["selected_before_audit"])
        self.assertTrue(accepted["preliminary_accept"])
        self.assertFalse(rejected["preliminary_accept"])

    def test_unselected_issuer_inherits_family_in_frozen_and_accepted_policies(self):
        dates = pd.date_range("2024-01-01", periods=400).append(
            pd.date_range("2025-09-01", periods=150)).append(pd.date_range("2026-06-01", periods=90))
        frame = sample(dates)
        frame["win_family_center"] = True

        def decision(key, _):
            chosen = "current" if key.endswith("|기관A") else "family_center"
            return {"key": key, "selected_before_audit": chosen,
                    "preliminary_accept": chosen != "current"}

        with tempfile.TemporaryDirectory(prefix="bid-protocol-test-") as folder:
            out = Path(folder)
            with patch.object(bt, "segment_decision", side_effect=decision), \
                    patch.object(bt, "block_ci", return_value=[0.1, 0.2]):
                bt.summarize(frame, out)
            self.assertTrue(frame["strategy_frozen"].eq("family_center").all())
            self.assertTrue(frame["strategy_accepted"].eq("family_center").all())

    def test_inactive_third_role_is_not_in_diagnosis_role_denominator(self):
        frame = sample(pd.date_range("2026-06-01", periods=6))
        frame["모델구간"] = "diagnosis|기존분석"
        frame["중심모델군"] = "일반진단"
        frame["세부업종"] = ["VLF"] * 3 + ["PD"] * 3
        frame.loc[:2, "참여업체수"] = 2
        frame.loc[:2, "role_3_active"] = False
        frame.loc[3:, "role_3_win"] = True
        frame.loc[3:, "role_3_unique"] = True
        with tempfile.TemporaryDirectory(prefix="bid-role-test-") as folder:
            out = Path(folder)
            with patch.object(bt, "segment_decision", side_effect=lambda key, _: {
                    "key": key, "selected_before_audit": "current", "preliminary_accept": False}):
                bt.summarize(frame, out)
            roles = pd.read_csv(out / "role_contribution.csv")
            third = roles[(roles["기간"] == "전체") & (roles["방법"] == "라인헷지") & (roles["참여업체수"] == 3)].iloc[0]
            self.assertEqual(third["배정공고수"], 3)
            self.assertEqual(third["단독가상낙찰"], 3)
            self.assertEqual(third["단독가상낙찰률"], 1.0)


if __name__ == "__main__":
    unittest.main()
