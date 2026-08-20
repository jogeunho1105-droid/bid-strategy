from __future__ import annotations

import ast
import json
import re
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


warnings.filterwarnings("ignore", message="Could not infer format")


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
APP = ROOT / "입찰 앱.py"
SOURCE = Path(r"C:\Users\USER\OneDrive - (주)와이앤제이이앤씨\운영관리\입찰분석\낙찰데이터.xlsx")
DETAIL = ROOT / "analysis" / "results" / "v2.15.5" / "impact_detail.csv"
OUTPUT = ROOT / "tests" / "v2.15.5_code_validation.json"


def load_app_namespace() -> dict:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    namespace = {"pd": pd, "np": np, "re": re, "datetime": datetime}
    bodies = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
            bodies.append(node)
        elif isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    namespace[target.id] = value
    module = ast.Module(body=bodies, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(APP), "exec"), namespace)
    return namespace


def parse_dates(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    parsed = pd.to_datetime(series, errors="coerce")
    short = text.str.match(r"^\d{2}\.\d{2}\.\d{2}")
    parsed.loc[short] = pd.to_datetime(text.loc[short].str[:8], format="%y.%m.%d", errors="coerce")
    return parsed


def run_recommendations(app: dict, source: pd.Series, history: pd.DataFrame) -> list[dict]:
    bid = {
        "org": str(source["발주기관"]),
        "name": str(source["공고명"]),
        "base": float(source["기초금액"]),
        "base_억": float(source["기초금액"]) / 1e8,
        "deadline": source["_date"],
        "industry": str(source["업종"]),
        "region": str(source["지역"]) if pd.notna(source["지역"]) else "",
        "companies": float(source["업체수"]) if pd.notna(source["업체수"]) else None,
    }
    model = app["enrich_history"](history)
    scope = app["classify_bid_scope"](bid)
    bid_history = app["history_for_bid"](bid, model, scope)
    improved = app["analyze_improved_model"](bid, bid_history)
    recs = app["build_company_recommendations"](bid, improved, None, None, None, bid_history)
    recs = app["apply_company_count"](recs, scope, bid_history, bid)
    return recs


def main() -> None:
    app = load_app_namespace()
    raw = pd.read_excel(SOURCE, sheet_name="통합낙찰이력").drop_duplicates(keep="first")
    raw["_date"] = parse_dates(raw["개찰일"])
    raw["예가/기초(0%)"] = pd.to_numeric(raw["예가/기초(0%)"], errors="coerce")
    valid = raw[
        raw["_date"].notna()
        & raw["예가/기초(0%)"].notna()
        & (raw["예가/기초(0%)"].abs() < 10)
        & raw["발주기관"].notna()
        & raw["공고명"].notna()
    ].copy()
    valid = valid.sort_values(["_date", "공고번호", "번호"], kind="mergesort")
    detail = pd.read_csv(DETAIL, parse_dates=["개찰일"])
    detail["표본출처"] = "v2.15.5 최신 전체 백테스트"
    sample_frames = []
    for label, frame in detail.groupby("분석구분", sort=False):
        indexes = np.linspace(0, len(frame) - 1, min(3, len(frame))).astype(int)
        sample_frames.append(frame.iloc[indexes].copy())
    samples = pd.concat(sample_frames, ignore_index=True)

    checks = []
    for _, sample in samples.iterrows():
        matches = valid[
            (valid["공고번호"].astype(str) == str(sample["공고번호"]))
            & (valid["_date"] == sample["개찰일"])
            & (valid["공고명"].astype(str) == str(sample["공고명"]))
        ]
        if matches.empty:
            raise AssertionError(f"원천 행을 찾을 수 없음: {sample['공고번호']} / {sample['개찰일']}")
        source = matches.iloc[0]
        history = valid[valid["_date"] < sample["개찰일"]].drop(columns=["_date"]).copy()
        actual_recs = run_recommendations(app, source, history)
        actual = [float(rec["rate"]) for rec in actual_recs]
        prefix = "제안"
        expected = [
            float(sample[column])
            for column in (f"{prefix}추천1", f"{prefix}추천2", f"{prefix}추천3")
            if pd.notna(sample[column])
        ]
        differences = [abs(a - b) for a, b in zip(actual, expected)]
        max_difference = max(differences or [0.0])
        checks.append(
            {
                "공고번호": str(sample["공고번호"]),
                "분석구분": sample["분석구분"],
                "개찰일": sample["개찰일"].strftime("%Y-%m-%d"),
                "표본출처": sample["표본출처"],
                "코드추천": actual,
                "코드근거": [rec.get("basis","") for rec in actual_recs],
                "백테스트제안": expected,
                "최대차이": max_difference,
                "통과": len(actual) == len(expected) and max_difference <= 0.0001000001,
            }
        )

    result = {
        "sample_count": len(checks),
        "passed": sum(item["통과"] for item in checks),
        "failed": sum(not item["통과"] for item in checks),
        "max_difference": max(item["최대차이"] for item in checks),
        "checks": checks,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sample_count", "passed", "failed", "max_difference")}, ensure_ascii=False))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
