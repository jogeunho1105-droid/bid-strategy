from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "tests" / "backtest_codecheck"
DETAIL = RESULT / "backtest_detail.csv"


def metrics(frame: pd.DataFrame, error: str, signed: str) -> dict:
    return {
        "count": int(len(frame)),
        "mae": float(frame[error].mean()),
        "hit_030": float((frame[error] <= 0.30).mean()),
        "hit_050": float((frame[error] <= 0.50).mean()),
        "below_risk": float((frame[signed] < -0.50).mean()),
        "above_risk": float((frame[signed] > 0.50).mean()),
        "bias": float(frame[signed].mean()),
    }


detail = pd.read_csv(DETAIL, parse_dates=["개찰일"])
hold = detail["분석구분"].isin(["전기공사 단일참여 1개사", "한전 감리 지역제한 1개사"])
detail["v2.15.4_proposed_error"] = detail["v2.15.4_candidate_error"].where(
    ~hold, detail["v2.15.3_current_error"]
)
detail["v2.15.4_proposed_signed"] = detail["v2.15.4_candidate_signed"].where(
    ~hold, detail["v2.15.3_current_signed"]
)
maximum = detail["개찰일"].max()
periods = {
    "all": detail,
    "recent_2y": detail[detail["개찰일"] >= maximum - pd.DateOffset(years=2)],
    "recent_1y": detail[detail["개찰일"] >= maximum - pd.DateOffset(years=1)],
    "recent_6m": detail[detail["개찰일"] >= maximum - pd.DateOffset(months=6)],
    "recent_3m": detail[detail["개찰일"] >= maximum - pd.Timedelta(days=92)],
}
summary = {}
for label, frame in periods.items():
    summary[label] = {
        "current": metrics(frame, "v2.15.3_current_error", "v2.15.3_current_signed"),
        "proposed": metrics(frame, "v2.15.4_proposed_error", "v2.15.4_proposed_signed"),
    }

recent_year = summary["recent_1y"]
recent_quarter = summary["recent_3m"]
overall = summary["all"]
acceptance = {
    "recent_1y_mae": recent_year["proposed"]["mae"] <= recent_year["current"]["mae"],
    "recent_1y_hit_030": recent_year["proposed"]["hit_030"] >= recent_year["current"]["hit_030"],
    "recent_3m_mae": recent_quarter["proposed"]["mae"] <= recent_quarter["current"]["mae"] + 0.002,
    "recent_3m_hit_030": recent_quarter["proposed"]["hit_030"] >= recent_quarter["current"]["hit_030"],
    "overall_mae": overall["proposed"]["mae"] <= overall["current"]["mae"] + 0.001,
}
output = {
    "source_date_max": maximum.strftime("%Y-%m-%d"),
    "backtest_rows": int(len(detail)),
    "hold_rows": int(hold.sum()),
    "summary": summary,
    "acceptance": acceptance,
    "all_passed": all(acceptance.values()),
}
(ROOT / "tests" / "v2.15.4_codecheck_summary.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(output, ensure_ascii=False, indent=2))
if not output["all_passed"]:
    raise SystemExit(1)
