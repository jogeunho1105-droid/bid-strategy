"""Behavioral checks for chronological recommendation safety and routing."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bid_engine as engine
import bid_legacy as legacy


def history_rows(n=60, *, org="시험기관", name="전기설계 용역", industry="전력설계",
                 base=150_000_000, start="2026-01-01", lower=None, upper=None):
    values = np.linspace(-0.6, 0.6, n) if lower is None else np.full(n, lower)
    return pd.DataFrame({
        "개찰일": pd.date_range(start, periods=n),
        "공고번호": [f"{org}-{name}-{i:04d}" for i in range(n)],
        "발주기관": org, "공고명": name, "업종": industry,
        "기초금액": base, "지역": "부산", "업체수": np.arange(n) + 20,
        engine.RATE: values,
        engine.WINNER: values + 0.12 if upper is None else np.full(n, upper),
    })


def bid(**updates):
    result = {"org": "시험기관", "name": "전기설계 용역", "industry": "전력설계",
              "base": 150_000_000, "deadline": "2026-04-01", "region": "부산"}
    result.update(updates)
    return result


def fitted(frame):
    result = engine.PredictionEngine()
    for source in engine.prepare_history(frame).to_dict("records"):
        result.add(engine.source_record(source))
    return result


class ChronologicalEngineTests(unittest.TestCase):
    def test_same_calendar_day_and_future_outcomes_do_not_change_prediction(self):
        past = history_rows()
        extra = history_rows(3, start="2026-04-01", lower=9.0, upper=9.8)
        extra.loc[0, "개찰일"] = pd.Timestamp("2026-04-01 00:01")
        extra.loc[1, "개찰일"] = pd.Timestamp("2026-04-01 22:00")
        expected = engine.recommend_bid(bid(), past, policy={})
        actual = engine.recommend_bid(bid(deadline="2026-04-01 23:00"),
                                      pd.concat([past, extra], ignore_index=True), policy={})
        self.assertEqual(actual["recommendations"], expected["recommendations"])
        self.assertEqual(actual["prior_rows"], len(past))

    def test_predict_rejects_state_containing_same_day_or_future_results(self):
        model = fitted(history_rows(2))
        for deadline in ("2026-01-02", "2026-01-01"):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                model.predict(engine.bid_record(bid(deadline=deadline)))

    def test_state_rejects_out_of_order_addition(self):
        records = [engine.source_record(s) for s in engine.prepare_history(history_rows(2)).to_dict("records")]
        model = engine.PredictionEngine()
        model.add(records[1])
        with self.assertRaises(ValueError):
            model.add(records[0])

    def test_target_results_and_competitor_count_cannot_change_any_candidate(self):
        history = history_rows(name="전기공사", industry="전기")
        model = fitted(history)
        row = engine.bid_record(bid(name="전기공사", industry="전기"))
        baseline = model.predict(row)["recommendations"]
        for value, winner, companies in ((8.0, 9.0, 3), (-8.0, -7.0, 9000)):
            poisoned = dict(row, value=value, winner=winner, companies=companies, comp=companies)
            self.assertEqual(model.predict(poisoned)["recommendations"], baseline)
        clean = engine.bid_record(bid(companies=9000, value=8.0, winner=9.0))
        self.assertIsNone(clean["companies"])
        self.assertIsNone(clean["comp"])
        self.assertNotIn("value", clean)
        self.assertNotIn("winner", clean)

    def test_historical_realized_competitor_count_is_not_a_target_feature(self):
        source = engine.prepare_history(history_rows(1)).iloc[0]
        record = engine.source_record(source)
        self.assertIsNone(record["companies"])
        self.assertIsNone(record["comp"])

    def test_repeated_candidates_are_deterministic_for_same_prior_data(self):
        frame = history_rows(80)
        expected = engine.recommend_bid(bid(), frame, policy={})
        shuffled = frame.sample(frac=1, random_state=42)
        self.assertEqual(engine.recommend_bid(bid(), shuffled, policy={})["recommendations"],
                         expected["recommendations"])

    def test_invalid_global_history_is_filtered_before_any_model(self):
        clean = history_rows(20)
        invalid = pd.concat([clean.iloc[[0]].copy() for _ in range(6)], ignore_index=True)
        invalid["개찰일"] = invalid["개찰일"].astype(object)
        invalid["공고번호"] = [f"invalid-{i}" for i in range(6)]
        invalid.loc[0, engine.RATE] = 10.0
        invalid.loc[1, engine.RATE] = np.inf
        invalid.loc[2, engine.RATE] = np.nan
        invalid.loc[3, "개찰일"] = "잘못된 날짜"
        invalid.loc[4, "발주기관"] = " "
        invalid.loc[5, "공고명"] = None
        combined = pd.concat([clean, invalid, clean.iloc[[0]]], ignore_index=True)
        prepared = engine.prepare_history(combined)
        self.assertEqual(len(prepared), len(clean))
        self.assertEqual(engine.recommend_bid(bid(), combined, policy={})["recommendations"],
                         engine.recommend_bid(bid(), clean, policy={})["recommendations"])

    def test_windows_anchor_to_target_date_not_stale_pool_date(self):
        model = fitted(history_rows(60, start="2022-01-01", lower=1.0, upper=1.2))
        row = engine.bid_record(bid())
        prediction = model.predict(row)
        self.assertIsNotNone(prediction)
        self.assertEqual(model.pools(row), ([], []))
        self.assertEqual(model.family_center(row, 0.25), 0.25)
        self.assertEqual(model.interval(row, [], 0.25), 0.25)

    def test_sparse_family_candidates_fall_back_to_original_rates(self):
        prediction = engine.recommend_bid(bid(), history_rows(12), policy={})
        for candidate in engine.STRATEGIES:
            self.assertEqual(prediction["recommendations"][candidate],
                             prediction["recommendations"]["current"])

    def test_kepco_local_predictions_never_use_national_history(self):
        org = "한국전력공사 경북본부"
        local = history_rows(org=org, name="전력감리", industry="전력감리", base=90_000_000)
        national = history_rows(org=org, name="전력감리", industry="전력감리", base=300_000_000,
                                lower=5.0, upper=5.5)
        target = bid(org=org, name="전력감리", industry="전력감리", base=90_000_000)
        first = engine.recommend_bid(target, local, policy={})
        second = engine.recommend_bid(target, pd.concat([local, national], ignore_index=True), policy={})
        self.assertEqual(first["recommendations"], second["recommendations"])
        self.assertEqual(second["prior_rows"], len(local))
        self.assertEqual(second["scope"]["company_count"], 1)

    def test_family_methods_do_not_borrow_unrelated_service_outcomes(self):
        design = history_rows(lower=0.0, upper=0.3)
        diagnostic = history_rows(name="안전진단 용역", industry="안전진단", lower=6.0, upper=7.0)
        model = fitted(pd.concat([design, diagnostic], ignore_index=True))
        row = engine.bid_record(bid())
        model.predict(row)
        family, issuer = model.pools(row)
        self.assertEqual(len(family), len(design))
        self.assertEqual(len(issuer), len(design))
        self.assertTrue(all(r["family"] == "design" for r in family))
        self.assertEqual(model.family_center(row, 4.0), 0.0)
        self.assertLess(model.interval(row, [], 4.0), 0.4)

    def test_interval_hedge_optimizes_remaining_not_already_covered_intervals(self):
        a = history_rows(60, lower=-1.0, upper=-0.5)
        b = history_rows(60, start="2026-03-02", lower=0.5, upper=1.0)
        model = fitted(pd.concat([a, b], ignore_index=True))
        row = engine.bid_record(bid(deadline="2026-05-15"))
        model.predict(row)
        additional = model.interval(row, [0.75], fallback=0.75)
        self.assertGreater(additional, -1.0)
        self.assertLess(additional, -0.5)

    def test_alternatives_respect_actual_company_count(self):
        org = "한국전력공사 부산울산본부"
        frame = history_rows(org=org, name="VLF 진단", industry="전기진단", base=90_000_000)
        result = engine.recommend_bid(bid(org=org, name="VLF 진단", industry="전기진단",
                                          base=99_999_999), frame, policy={})
        self.assertEqual(result["scope"]["company_count"], 2)
        self.assertTrue(all(len(rates) == 2 for rates in result["recommendations"].values()))
        self.assertEqual(result["recommendations"]["interval_line"], result["recommendations"]["current"])

    def test_strict_win_boundaries_and_nonpositive_intervals_are_losses(self):
        self.assertFalse(engine.strict_win(0.0, [0.0, 0.3], 0.3))
        self.assertTrue(engine.strict_win(-0.2, [0.0], 0.3))
        self.assertFalse(engine.strict_win(0.3, [0.3], 0.3))
        self.assertFalse(engine.strict_win(0.3, [0.25], 0.2))
        self.assertIsNone(engine.strict_win(0.0, [0.1], np.nan))
        self.assertIsNone(engine.strict_win(np.inf, [0.1], 0.3))
        self.assertIsNone(engine.strict_win(0.0, [0.1], 10.0))

    def test_policy_is_not_retroactively_applied_and_issuer_overrides_family(self):
        frame = history_rows(60)
        policy = {"effective_from": "2026-04-02", "rules": {
            "design|기존분석": "family_center",
            "design|기존분석|시험기관": "interval_direction",
        }}
        early = engine.recommend_bid(bid(), frame, policy=policy)
        self.assertEqual(early["strategy"], "current")
        effective = engine.recommend_bid(bid(deadline="2026-04-02"), frame, policy=policy)
        self.assertEqual(effective["strategy"], "interval_direction")
        other = engine.recommend_bid(bid(org="다른기관", deadline="2026-04-02"), frame, policy=policy)
        self.assertEqual(other["strategy"], "family_center")

    def test_sparse_ar1_route_uses_issuer_history_instead_of_single_local_result(self):
        frame = history_rows(20, org="조달청")
        frame.loc[19, "공고명"] = "건설사업관리 용역"
        frame.loc[19, "업종"] = "건설사업관리"
        model = fitted(frame)
        row = engine.bid_record(bid(org="조달청", name="건설사업관리 용역", industry="건설사업관리"))
        state = model.state(row)
        local = state.by_org_svc[(row["org"], row["svc"])]
        issuer = state.by_org[row["org"]]
        prior = state.by_svc[row["svc"]]
        raw, label = legacy.raw_center(row, state)
        self.assertEqual(label, "ar1_shrink")
        expected = legacy.ar1(issuer, legacy.bayes(local, prior, state.records))
        self.assertAlmostEqual(raw, expected)
        self.assertNotAlmostEqual(raw, local[0]["value"])

    def test_same_event_reexport_collapses_but_different_lot_names_are_kept(self):
        first = history_rows(1)
        repeat = first.copy()
        repeat["번호"] = 99
        repeat["개찰일"] = pd.Timestamp("2026-01-01 15:00")
        lot = first.copy()
        lot["공고명"] = "전기설계 용역 제2공구"
        frame = pd.concat([first, repeat, lot], ignore_index=True)
        prepared = engine.prepare_history(frame)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(set(prepared["공고명"]), {"전기설계 용역", "전기설계 용역 제2공구"})
        self.assertEqual(prepared.attrs["quality"]["repeated_event_rows_removed"], 1)

    def test_same_event_winner_disagreement_is_unknown_with_one_target_history(self):
        first = history_rows(1, lower=0.1, upper=0.2)
        conflicting = first.copy()
        conflicting[engine.WINNER] = 0.3
        prepared = engine.prepare_history(pd.concat([first, conflicting], ignore_index=True))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared.iloc[0]["_value"], 0.1)
        self.assertTrue(pd.isna(prepared.iloc[0]["_winner"]))
        self.assertIsNone(engine.strict_win(prepared.iloc[0]["_value"], [0.15], prepared.iloc[0]["_winner"]))
        self.assertEqual(prepared.attrs["quality"]["conflicting_winner_rows"], 2)

    def test_disagreeing_target_values_are_excluded_entirely(self):
        first = history_rows(1, lower=0.1, upper=0.3)
        conflicting = first.copy()
        conflicting[engine.RATE] = 0.2
        other_event = history_rows(1, start="2026-01-02", lower=-0.1, upper=0.1)
        prepared = engine.prepare_history(pd.concat([first, conflicting, other_event], ignore_index=True))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared.iloc[0]["_value"], -0.1)
        self.assertEqual(prepared.attrs["quality"]["conflicting_target_rows"], 2)

    def test_missing_region_is_unknown_and_never_national(self):
        frame = history_rows(4)
        frame["지역"] = [None, "", "   ", "전국"]
        prepared = engine.prepare_history(frame)
        self.assertEqual(prepared["_region"].tolist(), ["지역미상", "지역미상", "지역미상", "전국"])
        for region in (None, "", "   ", np.nan):
            self.assertEqual(engine.bid_record(bid(region=region))["region"], "지역미상")

    def test_batch_prediction_matches_individual_and_preserves_input_order(self):
        frame = history_rows(90)
        targets = [bid(deadline="2026-04-01"), bid(deadline="2026-02-20"),
                   bid(org="다른기관", deadline="2026-04-01"), bid(deadline="2026-03-15")]
        policy = {"effective_from": "2026-03-01", "rules": {"design|기존분석": "interval_direction"}}
        batch = engine.recommend_bids(targets, frame, policy=policy)
        for target, actual in zip(targets, batch):
            expected = engine.recommend_bid(target, frame, policy=policy)
            for key in ("rates", "strategy", "scope", "family", "prior_rows"):
                self.assertEqual(actual[key], expected[key], key)
        self.assertGreater(batch[0]["prior_rows"], batch[1]["prior_rows"])

    def test_comma_and_missing_bid_amounts_are_normalized(self):
        row = engine.bid_record(bid(base="150,000,000"))
        self.assertEqual(row["base"], 150_000_000)
        self.assertEqual(row["amt"], "1~2억")
        for value in (None, np.nan, np.inf, "금액 없음"):
            with self.subTest(value=value):
                row = engine.bid_record(bid(base=value))
                self.assertEqual(row["base"], 0.0)
                self.assertEqual(row["amt"], "금액미상")

    def test_missing_historical_base_does_not_enter_largest_amount_bucket(self):
        frame = history_rows(1)
        frame["기초금액"] = np.nan
        prepared = engine.prepare_history(frame)
        self.assertEqual(prepared.iloc[0]["_amt"], "금액미상")

    def test_zero_amount_group_mean_is_a_valid_blend_component(self):
        same_amount = history_rows(10, name="건설사업관리 용역", industry="건설사업관리", lower=0.0)
        other_amount = history_rows(10, name="건설사업관리 용역", industry="건설사업관리",
                                    base=300_000_000, start="2026-01-11", lower=1.0)
        model = fitted(pd.concat([same_amount, other_amount], ignore_index=True))
        row = engine.bid_record(bid(name="건설사업관리 용역", industry="건설사업관리"))
        prediction, name = legacy.raw_center(row, model.state(row))
        self.assertEqual(name, "blend_mean")
        # Issuer, issuer/service and overall averages are .5; target amount mean is zero.
        self.assertAlmostEqual(prediction, 0.375)


if __name__ == "__main__":
    unittest.main()
