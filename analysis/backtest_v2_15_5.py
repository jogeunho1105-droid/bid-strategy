from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
BASELINE_SCRIPT = HERE / "backtest_v2_15_4.py"
DEFAULT_OUT_DIR = HERE / "results" / "v2.15.5"
BUSAN_ULSAN_ORG = "한국전력공사 부산울산본부"
THREE_COMPANY_BASE_THRESHOLD = 100_000_000


def load_baseline_module():
    spec = importlib.util.spec_from_file_location("backtest_v2_15_4", BASELINE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"기준 백테스트 모듈을 읽을 수 없습니다: {BASELINE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def apply_proposed_count(row: dict, scope: str, count: int) -> tuple[str, int]:
    """v2.15.5 제안: 부산울산본부 지역제한 감리 중 기초금액 1억원 이상은 3개사."""
    if (
        scope == "지역제한"
        and row["org"] == BUSAN_ULSAN_ORG
        and row["svc"] == "supervision"
        and row["base"] >= THREE_COMPANY_BASE_THRESHOLD
    ):
        return scope, 3
    return scope, count


def proposed_scope(module, row: dict) -> tuple[str, int]:
    return apply_proposed_count(row, *module.current_scope(row))


def production_variant(row: dict, scope: str, count: int) -> str:
    """v2.15.4 운영본에서 전용 가중치를 유지하는 단일참여 구간을 재현한다."""
    if row["svc"] == "electric_construction" or (scope == "지역제한" and count == 1):
        return "v2.15.3_current"
    return "v2.15.4_candidate"


def production_recommendations(module, row: dict, state) -> tuple[list[float], str]:
    scope, count = module.current_scope(row)
    return module.recommendations(row, state, production_variant(row, scope, count))


def proposed_recommendations(module, row: dict, state) -> tuple[list[float], str]:
    """추천식은 v2.15.4를 유지하고 참여업체 수 분기만 v2.15.5로 교체한다."""
    original_scope = module.current_scope
    next_scope, next_count = apply_proposed_count(row, *original_scope(row))
    module.current_scope = lambda current_row: apply_proposed_count(
        current_row, *original_scope(current_row)
    )
    try:
        return module.recommendations(
            row, state, production_variant(row, next_scope, next_count)
        )
    finally:
        module.current_scope = original_scope


def evaluate(actual: float, recommendations: list[float]) -> tuple[float, float, float, int]:
    signed_values = [float(rate) - actual for rate in recommendations]
    best_index = int(np.argmin(np.abs(signed_values)))
    signed = float(signed_values[best_index])
    return abs(signed), signed, float(recommendations[best_index]), best_index + 1


def metric_row(frame: pd.DataFrame, error_column: str, signed_column: str) -> dict:
    errors = frame[error_column].dropna()
    signed = frame[signed_column].dropna()
    return {
        "표본수": int(len(errors)),
        "MAE": round(float(errors.mean()), 4) if len(errors) else None,
        "중앙절대오차": round(float(errors.median()), 4) if len(errors) else None,
        "±0.20%p": round(float((errors <= 0.20).mean()), 4) if len(errors) else None,
        "±0.30%p": round(float((errors <= 0.30).mean()), 4) if len(errors) else None,
        "±0.50%p": round(float((errors <= 0.50).mean()), 4) if len(errors) else None,
        "하한미달위험": round(float((signed < -0.50).mean()), 4) if len(signed) else None,
        "과상향위험": round(float((signed > 0.50).mean()), 4) if len(signed) else None,
        "평균편향": round(float(signed.mean()), 4) if len(signed) else None,
    }


def build_summary(detail: pd.DataFrame) -> pd.DataFrame:
    max_date = detail["개찰일"].max()
    periods = {
        "전체": detail,
        "최근 2년": detail[detail["개찰일"] >= max_date - pd.Timedelta(days=730)],
        "최근 1년": detail[detail["개찰일"] >= max_date - pd.Timedelta(days=365)],
        "최근 6개월": detail[detail["개찰일"] >= max_date - pd.Timedelta(days=183)],
        "최근 3개월": detail[detail["개찰일"] >= max_date - pd.Timedelta(days=92)],
    }
    rows: list[dict] = []
    for population, source in (("전체 평가표본", detail), ("변경 대상", detail[detail["변경대상"]])):
        for period, period_frame in periods.items():
            if population == "변경 대상":
                period_frame = period_frame[period_frame["변경대상"]]
            for version, error_column, signed_column in (
                ("v2.15.4_현행2개사", "현행오차", "현행편향"),
                ("v2.15.5_제안3개사", "제안오차", "제안편향"),
            ):
                row = {"모집단": population, "기간": period, "기준": version}
                row.update(metric_row(period_frame, error_column, signed_column))
                rows.append(row)
    return pd.DataFrame(rows)


def boundary_checks(module) -> list[dict]:
    common = {
        "date": pd.Timestamp("2026-08-20"),
        "value": 0.0,
        "org": BUSAN_ULSAN_ORG,
        "name": "부산 지역제한 감리용역",
        "notice": "BOUNDARY",
        "industry": "전력감리",
        "companies": 20,
        "svc": "supervision",
        "amt": "1~2억",
        "comp": 20,
        "region": "부산",
    }
    cases = [
        ("1억원 미만", 99_999_999, "지역제한", 2),
        ("1억원 정확히", 100_000_000, "지역제한", 3),
        ("2026 지역제한 상한 직전", 252_999_999, "지역제한", 3),
        ("2026 전국입찰 경계", 253_000_000, "전국입찰", 3),
    ]
    checks: list[dict] = []
    for label, base, expected_scope, expected_count in cases:
        row = dict(common, base=base)
        actual_scope, actual_count = proposed_scope(module, row)
        passed = actual_scope == expected_scope and actual_count == expected_count
        checks.append(
            {
                "검사": label,
                "기초금액": base,
                "예상범위": expected_scope,
                "예상업체수": expected_count,
                "실제범위": actual_scope,
                "실제업체수": actual_count,
                "통과": passed,
            }
        )
    if not all(check["통과"] for check in checks):
        raise AssertionError("v2.15.5 금액 경계조건 검사 실패")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--keep-exact-duplicates",
        action="store_true",
        help="완전 동일 중복행을 유지하는 민감도 검사용 옵션",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    module = load_baseline_module()
    data, quality = module.prepare_data(
        drop_exact_duplicates=not args.keep_exact_duplicates
    )
    checks = boundary_checks(module)
    states = {
        "global": module.PoolState(),
        "electric": module.PoolState(),
        "kepco_local": module.PoolState(),
        "kepco_national": module.PoolState(),
    }
    results: list[dict] = []

    for date, group in data.groupby("_date", sort=True):
        if len(states["global"].records) >= module.MIN_PRIOR_ROWS:
            for _, source_row in group.iterrows():
                row = {
                    "date": date,
                    "value": float(source_row["_value"]),
                    "base": float(source_row["_base"]) if pd.notna(source_row["_base"]) else 0.0,
                    "org": str(source_row["발주기관"]),
                    "name": str(source_row["공고명"]),
                    "notice": str(source_row["공고번호"]),
                    "industry": str(source_row["업종"] or ""),
                    "companies": float(source_row["_companies"]) if pd.notna(source_row["_companies"]) else None,
                    "svc": source_row["_svc"],
                    "amt": source_row["_amt"],
                    "comp": source_row["_comp"],
                    "region": source_row["_region"],
                }
                state = module.select_state(row, states)
                current, _ = production_recommendations(module, row, state)
                proposed, _ = proposed_recommendations(module, row, state)
                if not current or not proposed:
                    continue

                current_error, current_signed, current_best, current_best_position = evaluate(
                    row["value"], current
                )
                proposed_error, proposed_signed, proposed_best, proposed_best_position = evaluate(
                    row["value"], proposed
                )
                current_scope, current_count = module.current_scope(row)
                next_scope, next_count = proposed_scope(module, row)
                affected = current_scope == "지역제한" and current_count == 2 and next_count == 3
                results.append(
                    {
                        "개찰일": date,
                        "공고번호": row["notice"],
                        "공고명": row["name"],
                        "발주기관": row["org"],
                        "용역분류": module.SERVICE_LABELS[row["svc"]],
                        "분석구분": (
                            "전기공사 단일참여 1개사"
                            if next_scope == "전기공사 단일참여"
                            else f"한전 감리 {next_scope} {next_count}개사"
                            if "한국전력공사" in row["org"] and row["svc"] == "supervision"
                            else "한전 외/기타 3개사"
                        ),
                        "기초금액": row["base"],
                        "실제사정율": row["value"],
                        "입찰범위": current_scope,
                        "현행업체수": current_count,
                        "제안업체수": next_count,
                        "변경대상": affected,
                        "현행추천1": current[0] if len(current) >= 1 else None,
                        "현행추천2": current[1] if len(current) >= 2 else None,
                        "제안추천1": proposed[0] if len(proposed) >= 1 else None,
                        "제안추천2": proposed[1] if len(proposed) >= 2 else None,
                        "제안추천3": proposed[2] if len(proposed) >= 3 else None,
                        "현행최적추천": current_best,
                        "현행최적순번": current_best_position,
                        "제안최적추천": proposed_best,
                        "제안최적순번": proposed_best_position,
                        "현행오차": current_error,
                        "제안오차": proposed_error,
                        "현행편향": current_signed,
                        "제안편향": proposed_signed,
                        "오차개선": current_error - proposed_error,
                    }
                )

        for _, source_row in group.iterrows():
            row = {
                "date": date,
                "value": float(source_row["_value"]),
                "base": float(source_row["_base"]) if pd.notna(source_row["_base"]) else 0.0,
                "org": str(source_row["발주기관"]),
                "name": str(source_row["공고명"]),
                "notice": str(source_row["공고번호"]),
                "industry": str(source_row["업종"] or ""),
                "companies": float(source_row["_companies"]) if pd.notna(source_row["_companies"]) else None,
                "svc": source_row["_svc"],
                "amt": source_row["_amt"],
                "comp": source_row["_comp"],
                "region": source_row["_region"],
            }
            module.add_to_states(row, states)

    detail = pd.DataFrame(results)
    summary = build_summary(detail)
    affected = detail[detail["변경대상"]].copy()
    recent_year_cutoff = detail["개찰일"].max() - pd.Timedelta(days=365)
    recent_affected = affected[affected["개찰일"] >= recent_year_cutoff]

    validation = {
        "source": str(module.SOURCE),
        "source_quality": quality,
        "analysis_date_min": detail["개찰일"].min().strftime("%Y-%m-%d"),
        "analysis_date_max": detail["개찰일"].max().strftime("%Y-%m-%d"),
        "evaluated_rows": int(len(detail)),
        "affected_rows": int(len(affected)),
        "affected_recent_1y_rows": int(len(recent_affected)),
        "third_recommendation_best_rows": int((affected["제안최적순번"] == 3).sum()),
        "third_recommendation_best_recent_1y_rows": int((recent_affected["제안최적순번"] == 3).sum()),
        "new_hits_030_rows": int(((affected["현행오차"] > 0.30) & (affected["제안오차"] <= 0.30)).sum()),
        "new_hits_050_rows": int(((affected["현행오차"] > 0.50) & (affected["제안오차"] <= 0.50)).sum()),
        "non_regression": {
            "no_row_worsened": bool((detail["제안오차"] <= detail["현행오차"] + 1e-12).all()),
            "unaffected_rows_identical": bool(
                np.allclose(
                    detail.loc[~detail["변경대상"], "현행오차"],
                    detail.loc[~detail["변경대상"], "제안오차"],
                    equal_nan=True,
                )
            ),
            "only_target_count_changed": bool(
                (detail.loc[detail["변경대상"], "제안업체수"] == 3).all()
                and (detail.loc[~detail["변경대상"], "현행업체수"] == detail.loc[~detail["변경대상"], "제안업체수"]).all()
            ),
        },
        "boundary_checks": checks,
    }

    detail.to_csv(out_dir / "impact_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out_dir / "impact_summary.csv", index=False, encoding="utf-8-sig")
    affected.to_csv(out_dir / "affected_cases.csv", index=False, encoding="utf-8-sig")
    (out_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
