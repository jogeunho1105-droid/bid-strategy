from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


OUT_DIR = Path("analysis/results/v2.15.4")
RATE = "예가/기초(0%)"
MIN_PRIOR_ROWS = 150

MODEL_RULES = {
    "PD": "all_recent20",
    "VLF": "blend_weighted",
    "concrete": "blend_mean",
    "construction_management": "blend_mean",
    "design": "blend_mean",
    "diagnosis_other": "org_service_nearest5_limited",
    "optical": "blend_weighted",
    "other": "blend_mean",
    "supervision": "blend_mean",
    "ultrasound": "org_recent20",
    "electric_construction": "blend_weighted",
    "_default": "org_service_recent20",
}

ROUTER_RULES = {
    ("기타", "diagnosis_other"): "bayes_shrink",
    ("기타", "other"): "bayes_shrink",
    ("기타", "design"): "bayes_shrink",
    ("조달청", "supervision"): "bayes_shrink",
    ("조달청", "construction_management"): "ar1_shrink",
}

KEPCO_LOCAL_ORGS = {
    "한국전력공사 부산울산본부": 2,
    "한국전력공사 경북본부": 1,
    "한국전력공사 대구본부": 1,
}

SERVICE_LABELS = {
    "PD": "PD",
    "VLF": "VLF",
    "optical": "광학",
    "concrete": "콘크리트",
    "ultrasound": "초음파",
    "supervision": "감리",
    "design": "설계",
    "construction_management": "건설사업관리",
    "diagnosis_other": "진단기타",
    "other": "기타",
    "electric_construction": "전기공사",
}


def notice_amount_for_year(year: int) -> int:
    if year <= 2022:
        return 210_000_000
    if year <= 2024:
        return 220_000_000
    return 230_000_000


def parse_dates(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    parsed = pd.to_datetime(series, errors="coerce")
    short = text.str.match(r"^\d{2}\.\d{2}\.\d{2}")
    if short.any():
        parsed.loc[short] = pd.to_datetime(
            text.loc[short].str[:8], format="%y.%m.%d", errors="coerce"
        )
    return parsed


def classify_service(name: object, industry: object = None) -> str:
    s = str(name or "")
    ind = str(industry or "")
    su = s.upper()
    is_elec_industry = bool(re.match(r"^전기($|,|\s)", ind))
    is_service_like = any(keyword in s for keyword in ("감리", "설계", "진단", "점검", "측정"))
    if is_elec_industry and "전력감리" not in ind and "전력설계" not in ind and not is_service_like:
        return "electric_construction"
    if "전기공사" in s and not is_service_like:
        return "electric_construction"
    if "VLF" in su:
        return "VLF"
    if "PD" in su or "부분방전" in s:
        return "PD"
    if "광학" in s:
        return "optical"
    if "콘크리트" in s:
        return "concrete"
    if "초음파" in s:
        return "ultrasound"
    if "건설사업관리" in s or "감독권한대행" in s:
        return "construction_management"
    if "감리" in s:
        return "supervision"
    if "설계" in s:
        return "design"
    if "진단" in s or "점검" in s or "측정" in s:
        return "diagnosis_other"
    return "other"


def amount_bucket(base_won: object) -> str:
    try:
        eok = float(base_won or 0) / 1e8
    except Exception:
        eok = 0
    if not np.isfinite(eok) or eok <= 0:
        return "금액미상"
    if eok < 0.3:
        return "0.3억미만"
    if eok < 0.5:
        return "0.3~0.5억"
    if eok < 1.0:
        return "0.5~1억"
    if eok < 2.0:
        return "1~2억"
    if eok < 5.0:
        return "2~5억"
    return "5억이상"


def company_bucket(value: object) -> int | None:
    try:
        number = float(value)
        if np.isnan(number):
            return None
        return int(number // 10 * 10)
    except Exception:
        return None


def simple_region(value: object) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "지역미상"
    if not str(value).strip() or str(value).strip().lower() in ("nan", "none"):
        return "지역미상"
    parts = [part.strip() for part in re.split(r"[,/\s]+", str(value)) if part.strip()]
    for part in parts:
        if part != "전국":
            return part
    return "전국"


def org_group(org: str) -> str:
    if "한국전력" in org or "한전" in org:
        return "한국전력공사"
    if "조달청" in org:
        return "조달청"
    return "기타"


def current_scope(row: dict) -> tuple[str, int]:
    if row["svc"] == "electric_construction":
        return "전기공사 단일참여", 1
    if "한국전력공사" not in row["org"] or row["svc"] != "supervision":
        return "기존분석", 3
    below = row["base"] > 0 and round(row["base"]) < round(
        notice_amount_for_year(row["date"].year) * 1.1
    )
    if row["org"] in KEPCO_LOCAL_ORGS and below:
        return "지역제한", KEPCO_LOCAL_ORGS[row["org"]]
    return "전국입찰", 3


def historical_kepco_scope(row: dict) -> str | None:
    if "한국전력공사" not in row["org"] or row["svc"] != "supervision":
        return None
    below = row["base"] > 0 and round(row["base"]) < round(
        notice_amount_for_year(row["date"].year) * 1.1
    )
    return "지역제한" if below else "전국입찰"


@dataclass
class PoolState:
    records: list[dict] = field(default_factory=list)
    by_org: defaultdict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    by_svc: defaultdict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    by_svc_amt: defaultdict[tuple[str, str], list[dict]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_org_svc: defaultdict[tuple[str, str], list[dict]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_region: defaultdict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    by_company: defaultdict[int | None, list[dict]] = field(
        default_factory=lambda: defaultdict(list)
    )
    line_exact: defaultdict[str, defaultdict[tuple[str, str, str], Counter]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Counter))
    )
    line_move_sign: defaultdict[str, defaultdict[tuple[str, str], Counter]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Counter))
    )
    line_sign: defaultdict[str, defaultdict[str, Counter]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Counter))
    )
    line_all: defaultdict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, row: dict) -> None:
        prior_org_records = self.by_org[row["org"]]
        if len(prior_org_records) >= 2:
            sample = values(prior_org_records, 5)
            profile = (
                movement(sample[-1] - sample[-2]),
                "양수" if sample[-1] >= 0 else "음수",
                trend(sample),
            )
            line_key = int(round(float(row["value"]) / 0.02))
            self.line_exact[row["org"]][profile][line_key] += 1
            self.line_move_sign[row["org"]][profile[:2]][line_key] += 1
            self.line_sign[row["org"]][profile[1]][line_key] += 1
            self.line_all[row["org"]][line_key] += 1
        self.records.append(row)
        self.by_org[row["org"]].append(row)
        self.by_svc[row["svc"]].append(row)
        self.by_svc_amt[(row["svc"], row["amt"])].append(row)
        self.by_org_svc[(row["org"], row["svc"])].append(row)
        self.by_region[row["region"]].append(row)
        self.by_company[row["comp"]].append(row)


def values(records: Iterable[dict], tail: int | None = None) -> list[float]:
    data = records[-tail:] if tail and isinstance(records, list) else list(records)
    if tail and not isinstance(records, list):
        data = data[-tail:]
    return [float(record["value"]) for record in data]


def mean_tail(records: list[dict], tail: int) -> float | None:
    vals = values(records, tail)
    return float(np.mean(vals)) if vals else None


def nearest_mean(records: list[dict], hint: float | None, n: int) -> float | None:
    vals = np.asarray(values(records, 180), dtype=float)
    if len(vals) < 3:
        return None
    target = float(hint if hint is not None else np.mean(vals[-min(10, len(vals)) :]))
    indexes = np.argsort(np.abs(vals - target))[: min(n, len(vals))]
    return float(np.mean(vals[indexes]))


def weighted_mean(items: list[tuple[float | None, float]]) -> float | None:
    valid = [(value, weight) for value, weight in items if value is not None]
    total = sum(weight for _, weight in valid)
    if not valid or not total:
        return None
    return float(sum(float(value) * weight for value, weight in valid) / total)


def quantile_clip(value: float, vals: list[float], lower: float = 0.05, upper: float = 0.95) -> float:
    if len(vals) < 10:
        return float(value)
    return float(np.clip(value, np.quantile(vals, lower), np.quantile(vals, upper)))


def robust_center(records: list[dict]) -> float | None:
    vals = np.asarray(values(records), dtype=float)
    if len(vals) == 0:
        return None
    if len(vals) < 5:
        return float(np.mean(vals))
    lo, hi = np.quantile(vals, [0.10, 0.90])
    trimmed = vals[(vals >= lo) & (vals <= hi)]
    return float(0.60 * np.median(vals) + 0.40 * np.mean(trimmed))


def bayes(local: list[dict], prior: list[dict], global_records: list[dict], k: int = 10) -> float:
    prior_vals = values(prior, 100) or values(global_records, 100)
    prior_mean = float(np.mean(prior_vals)) if prior_vals else 0.0
    local_vals = np.asarray(values(local, 50), dtype=float)
    if len(local_vals) == 0:
        return prior_mean
    return float((len(local_vals) * np.mean(local_vals) + k * prior_mean) / (len(local_vals) + k))


def ar1(records: list[dict], center: float, ridge: float = 10.0) -> float:
    vals = values(records, 30)
    arr = np.asarray(vals, dtype=float)
    if len(arr) < 8:
        return float(center)
    x = arr[:-1]
    y = arr[1:]
    x_centered = x - np.mean(x)
    y_centered = y - np.mean(y)
    beta = float(np.sum(x_centered * y_centered) / (np.sum(x_centered**2) + ridge))
    beta = float(np.clip(beta, -0.5, 0.5))
    raw = float(np.mean(y) - beta * np.mean(x) + beta * arr[-1])
    return quantile_clip(0.5 * raw + 0.5 * center, vals)


def recent_records(records: list[dict], days: int) -> list[dict]:
    if not records:
        return []
    latest = records[-1]["date"]
    boundary = latest - pd.Timedelta(days=days)
    low = 0
    high = len(records)
    while low < high:
        middle = (low + high) // 2
        if records[middle]["date"] < boundary:
            low = middle + 1
        else:
            high = middle
    return records[low:]


def raw_center(row: dict, state: PoolState) -> tuple[float | None, str]:
    all_records = state.records
    org_records = state.by_org[row["org"]]
    svc_records = state.by_svc[row["svc"]]
    svc_amt_records = state.by_svc_amt[(row["svc"], row["amt"])]
    org_svc_records = state.by_org_svc[(row["org"], row["svc"])]
    hint = next((v for v in (mean_tail(org_records, 10), mean_tail(svc_records, 10), mean_tail(all_records, 20)) if v is not None), None)
    candidates = {
        "all_recent20": mean_tail(all_records, 20),
        "org_recent20": mean_tail(org_records, 20),
        "service_recent20": mean_tail(svc_records, 20),
        "svc_amount_recent20": mean_tail(svc_amt_records, 20),
        "org_service_recent20": mean_tail(org_svc_records, 20),
        "service_nearest10_limited": nearest_mean(svc_records, hint, 10),
        "org_service_nearest5_limited": nearest_mean(org_svc_records, hint, 5),
    }
    candidates["blend_weighted"] = weighted_mean(
        [
            (candidates["org_recent20"], 0.35),
            (candidates["org_service_recent20"], 0.25),
            (candidates["svc_amount_recent20"] if candidates["svc_amount_recent20"] is not None else candidates["service_recent20"], 0.20),
            (candidates["service_recent20"], 0.10),
            (candidates["all_recent20"], 0.10),
        ]
    )
    candidates["blend_mean"] = weighted_mean(
        [
            (candidates["org_recent20"], 1),
            (candidates["org_service_recent20"], 1),
            (candidates["svc_amount_recent20"] if candidates["svc_amount_recent20"] is not None else candidates["service_recent20"], 1),
            (candidates["all_recent20"], 1),
        ]
    )
    requested = MODEL_RULES.get(row["svc"], MODEL_RULES["_default"])
    fallback = [
        requested,
        "org_service_recent20",
        "org_recent20",
        "svc_amount_recent20",
        "service_recent20",
        "blend_weighted",
        "blend_mean",
        "all_recent20",
    ]
    used = next((name for name in fallback if candidates.get(name) is not None), "")
    if not used:
        return None, ""
    pred = float(candidates[used])
    route = ROUTER_RULES.get((org_group(row["org"]), row["svc"]))
    if route == "bayes_shrink":
        pred = bayes(org_svc_records or org_records, svc_records, all_records)
        used = route
    elif route == "ar1_shrink":
        center = bayes(org_svc_records, svc_records, all_records)
        selected = (org_svc_records if len(org_svc_records) >= 3 else org_records if len(org_records) >= 3 else svc_records if len(svc_records) >= 3 else all_records)
        pred = ar1(selected, center)
        used = route
    return pred, used


def overlay(row: dict, state: PoolState, pred: float, candidate: bool = False) -> float:
    pools = [
        state.by_org_svc[(row["org"], row["svc"])],
        state.by_org[row["org"]],
        state.by_svc[row["svc"]],
        state.records,
    ]
    pool = next((records for records in pools if len(records) >= 8), state.records)
    recent = recent_records(pool, 90)
    all_vals = values(pool)
    recent_vals = values(recent)
    if len(all_vals) < 8 or len(recent_vals) < 5:
        return round(float(pred), 4)
    recent_mean = float(np.mean(recent_vals))
    overall_mean = float(np.mean(all_vals))
    recent_std = float(np.std(recent_vals))
    drift = recent_mean - overall_mean
    if candidate:
        weight = 0.05 if recent_std >= 0.55 else 0.10
        drift_weight = 0.04
    else:
        weight = 0.12 if recent_std >= 0.55 else 0.18
        drift_weight = 0.08
    raw = (1 - weight) * pred + weight * recent_mean + drift_weight * drift
    return round(quantile_clip(raw, all_vals), 4)


def sign_transition(records: list[dict]) -> float | None:
    vals = values(records, 100)
    if len(vals) < 3:
        return None
    current_positive = vals[-1] >= 0
    next_signs = [
        vals[index + 1] >= 0
        for index in range(len(vals) - 1)
        if (vals[index] >= 0) == current_positive
    ]
    if len(next_signs) < 3:
        next_signs = [value >= 0 for value in vals[1:]]
    return float(np.mean(next_signs)) if next_signs else None


def company1(row: dict, state: PoolState, fallback: float) -> float:
    org_records = state.by_org[row["org"]]
    service_records = state.by_org_svc[(row["org"], row["svc"])]
    stats = [value for value in (sign_transition(org_records), sign_transition(service_records)) if value is not None]
    step_records = service_records if len(service_records) >= 2 else org_records
    step_vals = values(step_records, 2)
    if not stats or len(step_vals) < 2:
        return round(float(fallback), 4)
    predicted_positive = float(np.mean(stats)) >= 0.5
    previous, last = step_vals[-2:]
    gap = abs(last - previous)
    candidate = last + gap if predicted_positive else last - gap
    if predicted_positive and candidate <= 0:
        candidate = gap if gap > 0 else abs(last)
    elif not predicted_positive and candidate >= 0:
        candidate = -gap if gap > 0 else -abs(last)
    org_vals = values(org_records)
    if len(org_vals) >= 10:
        candidate = quantile_clip(candidate, org_vals)
    return round(float(candidate), 4)


def movement(delta: float) -> str:
    if delta > 0.0001:
        return "상승"
    if delta < -0.0001:
        return "하락"
    return "보합"


def trend(vals: list[float]) -> str:
    sample = np.asarray(vals[-5:], dtype=float)
    if len(sample) < 2:
        return "보합"
    slope = float(np.polyfit(np.arange(len(sample)), sample, 1)[0])
    if slope > 0.005:
        return "상승"
    if slope < -0.005:
        return "하락"
    return "보합"


def pattern(vals: list[float], index: int) -> tuple[str, str, str]:
    start = max(0, index - 4)
    return (
        movement(vals[index] - vals[index - 1]),
        "양수" if vals[index] >= 0 else "음수",
        trend(vals[start : index + 1]),
    )


def company3(row: dict, state: PoolState, fallback: float, step: float = 0.02) -> float:
    org_records = state.by_org[row["org"]]
    if len(org_records) < 6:
        return round(float(fallback), 4)
    vals = values(org_records, 5)
    current = pattern(vals, len(vals) - 1)
    counters = [
        state.line_exact[row["org"]][current],
        state.line_move_sign[row["org"]][current[:2]],
        state.line_sign[row["org"]][current[1]],
        state.line_all[row["org"]],
    ]
    counts = next((counter for counter in counters[:-1] if sum(counter.values()) >= 3), counters[-1])
    if not counts:
        return round(float(fallback), 4)
    maximum = max(counts.values())
    top = [int(key) for key, count in counts.items() if int(count) == maximum]
    diffs = np.diff(np.asarray(vals[-5:], dtype=float))
    target = vals[-1] + (float(np.median(diffs)) if len(diffs) else 0.0)
    selected_key = min(top, key=lambda key: abs(key * step - target))
    return round(selected_key * step, 4)


def electric_rate(row: dict, state: PoolState, center: float, candidate: bool) -> float:
    if len(state.records) < 20:
        return round(center, 4)
    components: list[tuple[float | None, float]] = []
    org_records = state.by_org[row["org"]][-30:]
    if len(org_records) >= 5:
        if candidate:
            weight = 0.30 if len(org_records) >= 10 else 0.20
        else:
            weight = 0.35 if len(org_records) >= 10 else 0.25
        components.append((robust_center(org_records), weight))
    region_records = state.by_region[row["region"]][-50:]
    if row["region"] != "지역미상" and len(region_records) >= 8:
        components.append((robust_center(region_records), 0.15 if candidate else 0.20))
    amount_records = state.by_svc_amt[(row["svc"], row["amt"])][-80:]
    if len(amount_records) >= 10:
        components.append((robust_center(amount_records), 0.15 if candidate else 0.20))
    company_records = state.by_company[row["comp"]][-80:]
    if row["comp"] is not None and len(company_records) >= 10:
        components.append((robust_center(company_records), 0.10))
    recent365 = recent_records(state.records, 365)
    if len(recent365) >= 20:
        components.append((robust_center(recent365), 0.20))
    recent90 = recent_records(state.records, 90)
    if len(recent90) >= 10:
        recent_std = float(np.std(values(recent90)))
        if candidate:
            weight = 0.05 if recent_std >= 0.55 else 0.10
        else:
            weight = 0.10 if recent_std >= 0.55 else 0.15
        components.append((robust_center(recent90), weight))
    if candidate:
        components.append((robust_center(state.records[-100:]), 0.10))
    if not components:
        components.append((robust_center(state.records[-100:]), 1.0))
    pred = weighted_mean(components)
    if pred is None:
        return round(center, 4)
    return round(quantile_clip(pred, values(state.records[-300:])), 4)


def local_single(center: float, state: PoolState, candidate: bool) -> float:
    vals = values(state.records)
    if len(vals) < 8:
        return round(center, 4)
    r3 = float(np.mean(vals[-3:]))
    r5 = float(np.mean(vals[-5:]))
    r10 = float(np.mean(vals[-10:]))
    recent_center = 0.45 * r3 + 0.35 * r5 + 0.20 * r10
    recent_std = float(np.std(vals[-10:]))
    if candidate:
        weight = 0.15 if recent_std >= 0.55 else 0.25
    else:
        weight = 0.25 if recent_std >= 0.55 else 0.35
    return round(quantile_clip((1 - weight) * center + weight * recent_center, vals), 4)


def recommendations(row: dict, state: PoolState, variant: str) -> tuple[list[float], str]:
    raw, model = raw_center(row, state)
    if raw is None:
        return [], model
    candidate = variant == "v2.15.4_candidate"
    center = overlay(row, state, raw, candidate=candidate)
    first = company1(row, state, center)
    third = company3(row, state, center)
    scope, count = current_scope(row)
    if count == 1:
        if row["svc"] == "electric_construction":
            return [electric_rate(row, state, center, candidate)], model
        return [local_single(center, state, candidate)], model
    if count == 2:
        return [first, center], model
    return [first, center, third], model


def segment_name(row: dict) -> str:
    scope, count = current_scope(row)
    if scope == "전기공사 단일참여":
        return "전기공사 단일참여 1개사"
    if "한국전력공사" in row["org"] and row["svc"] == "supervision":
        return f"한전 감리 {scope} {count}개사"
    return "한전 외/기타 3개사"


def select_state(row: dict, states: dict[str, PoolState]) -> PoolState:
    scope, _ = current_scope(row)
    if scope == "전기공사 단일참여":
        return states["electric"]
    if "한국전력공사" in row["org"] and row["svc"] == "supervision":
        return states["kepco_local" if scope == "지역제한" else "kepco_national"]
    return states["global"]


def add_to_states(row: dict, states: dict[str, PoolState]) -> None:
    states["global"].add(row)
    if row["svc"] == "electric_construction":
        states["electric"].add(row)
    hist_scope = historical_kepco_scope(row)
    if hist_scope == "지역제한":
        states["kepco_local"].add(row)
    elif hist_scope == "전국입찰":
        states["kepco_national"].add(row)
