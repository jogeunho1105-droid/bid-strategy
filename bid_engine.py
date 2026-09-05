"""Shared, prior-day-only prediction engine for the app and rolling evaluation."""
from __future__ import annotations

import json
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import bid_legacy as legacy
import bid_rules as rules

RATE = "예가/기초(0%)"
WINNER = "1순위사정율(0%)"
STRATEGIES = ("current", "family_center", "interval_center", "interval_direction", "interval_line")
STRATEGY_LABELS = {
    "current": "기존 추천(참여수 반영)",
    "family_center": "기관·모델군 축소중심",
    "interval_center": "낙찰구간 중심 대체",
    "interval_direction": "낙찰구간 방향성 대체",
    "interval_line": "낙찰구간 라인 대체",
}
GRID = np.round(np.arange(-3.0, 3.0001, 0.02), 4)


def numeric(series):
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def prepare_history(frame, as_of=None):
    """Normalize calendar days; retain uncertain result intervals for explicit QA."""
    d = frame.drop_duplicates().copy()
    required = ["개찰일", RATE, "발주기관", "공고명"]
    if any(c not in d for c in required):
        raise ValueError("필수 낙찰이력 컬럼이 없습니다: " + ", ".join(c for c in required if c not in d))
    for c in ("업종", "지역", "공고번호"):
        if c not in d:
            d[c] = ""
        d[c] = d[c].fillna("").astype(str).str.strip()
    d["_date"] = rules.parse_date_series(d["개찰일"]).dt.normalize()
    d["_value"] = numeric(d[RATE])
    d["_winner"] = numeric(d[WINNER]) if WINNER in d else np.nan
    d["_base"] = numeric(d["기초금액"]) if "기초금액" in d else np.nan
    d["_companies"] = numeric(d["업체수"]) if "업체수" in d else np.nan
    valid = d["_date"].notna() & d["_value"].notna() & d["_value"].abs().lt(10)
    valid &= d["발주기관"].fillna("").astype(str).str.strip().ne("")
    valid &= d["공고명"].fillna("").astype(str).str.strip().ne("")
    if as_of is not None:
        valid &= d["_date"].le(pd.Timestamp(as_of).normalize())
    d = d.loc[valid].copy()
    d["발주기관"] = d["발주기관"].astype(str).str.strip()
    event_key = ["공고번호", "발주기관", "_date", "공고명", "_base"]
    identified = d["공고번호"].ne("")
    event = d.loc[identified].groupby(event_key, dropna=False)
    conflicting_target = event["_value"].transform("nunique").gt(1)
    conflicting_winner = event["_winner"].transform("nunique").gt(1)
    quality = {"conflicting_target_rows": int(conflicting_target.sum()),
               "conflicting_winner_rows": int(conflicting_winner.sum())}
    # Conflicting winners are unknown, not an opportunity to choose the favorable one.
    d.loc[conflicting_winner[conflicting_winner].index, "_winner"] = np.nan
    d.loc[conflicting_winner[conflicting_winner].index, WINNER] = np.nan
    d = d.drop(index=conflicting_target[conflicting_target].index)
    before = len(d)
    with_id = d[d["공고번호"].ne("")].copy()
    with_id["_winner"] = with_id.groupby(event_key, dropna=False)["_winner"].transform("first")
    with_id[WINNER] = with_id["_winner"]
    with_id["지역"] = with_id["지역"].replace("", np.nan)
    with_id["지역"] = with_id.groupby(event_key, dropna=False)["지역"].transform("first").fillna("")
    d = pd.concat([with_id.drop_duplicates(event_key, keep="first"), d[d["공고번호"].eq("")]])
    quality["repeated_event_rows_removed"] = before - len(d)
    d["_svc"] = [rules.classify_service(n, i) for n, i in zip(d["공고명"], d["업종"])]
    d["_family"] = [rules.classify_model_family(n, i, o) for n, i, o in zip(d["공고명"], d["업종"], d["발주기관"])]
    d["_amt"] = d["_base"].map(legacy.amount_bucket)
    d["_region"] = d["지역"].map(legacy.simple_region)
    sort = ["_date", "공고번호"] + (["번호"] if "번호" in d else [])
    d = d.sort_values(sort, kind="mergesort").reset_index(drop=True)
    d.attrs["quality"] = quality
    return d


def source_record(source):
    return {
        "date": source["_date"], "value": float(source["_value"]),
        "winner": float(source["_winner"]), "org": str(source["발주기관"]),
        "name": str(source["공고명"]), "industry": str(source["업종"]),
        "notice": str(source["공고번호"]),
        "base": float(source["_base"]) if pd.notna(source["_base"]) else 0.0,
        "svc": source["_svc"], "family": source["_family"],
        "amt": source["_amt"], "region": source["_region"],
        # Actual competition count belongs to historical outcomes, never target features.
        "companies": None, "comp": None,
    }


def bid_record(bid):
    date = rules.parse_date_series(pd.Series([bid.get("deadline")])).iloc[0]
    if pd.isna(date):
        raise ValueError("개찰일을 확인할 수 없어 사전 추천을 계산할 수 없습니다.")
    name, industry, org = (str(bid.get(k, "") or "") for k in ("name", "industry", "org"))
    base = pd.to_numeric(str(bid.get("base", 0) or 0).replace(",", ""), errors="coerce")
    base = float(base) if pd.notna(base) and np.isfinite(base) else 0.0
    return {
        "date": date.normalize(), "org": org, "name": name, "industry": industry,
        "base": base, "notice": str(bid.get("id", "")),
        "svc": rules.classify_service(name, industry),
        "family": rules.classify_model_family(name, industry, org),
        "amt": legacy.amount_bucket(base), "region": legacy.simple_region(bid.get("region")),
        "companies": None, "comp": None,
    }


def scope_for(row):
    return rules.classify_bid_scope({"org": row["org"], "name": row["name"],
        "industry": row["industry"], "base": row["base"], "deadline": row["date"]})


def pool_key(row, historical=False):
    scope = ""
    if row["family"] == "kepco_supervision":
        scope = (rules.historical_scope(row["name"], row["industry"], row["org"], row["base"], row["date"])
                 if historical else scope_for(row)["scope"])
    return row["family"], scope


def strict_win(actual, recs, winner):
    if not np.isfinite(actual) or not np.isfinite(winner) or abs(winner) >= 10:
        return None
    return any(actual < float(x) < winner for x in recs if x is not None and np.isfinite(x))


def recent(records, date, days):
    start = bisect_left(records, date - pd.Timedelta(days=days), key=lambda r:r["date"])
    end = bisect_left(records, date, key=lambda r:r["date"])
    return records[start:end]


class PredictionEngine:
    def __init__(self):
        self.states = {k: legacy.PoolState() for k in ("global", "electric", "kepco_local", "kepco_national")}
        self.family = defaultdict(list)
        self.issuer_family = defaultdict(list)
        self.last_date = None
        self.cache_date = None
        self.cache = {}

    def add(self, row):
        if self.last_date is not None and row["date"] < self.last_date:
            raise ValueError("이력은 날짜순으로 추가해야 합니다.")
        self.last_date = row["date"]
        self.states["global"].add(row)
        if row["svc"] == "electric_construction":
            self.states["electric"].add(row)
        if row["family"] == "kepco_supervision":
            scope = pool_key(row, True)[1]
            if scope in ("지역제한", "전국입찰"):
                self.states["kepco_local" if scope == "지역제한" else "kepco_national"].add(row)
        key = pool_key(row, True)
        self.family[key].append(row)
        self.issuer_family[(key, row["org"])].append(row)

    def state(self, row):
        if row["svc"] == "electric_construction":
            return self.states["electric"]
        if row["family"] == "kepco_supervision":
            return self.states["kepco_local" if scope_for(row)["scope"] == "지역제한" else "kepco_national"]
        return self.states["global"]

    def pools(self, row):
        key = pool_key(row)
        # Group windows are anchored to this bid's date, including sparse issuers.
        cache_key = ("pools", key, row["org"])
        if cache_key not in self.cache:
            family = recent(self.family[key], row["date"], 730)[-2000:]
            issuer = recent(self.issuer_family[(key, row["org"])], row["date"], 730)[-300:]
            self.cache[cache_key] = (family, issuer)
        return self.cache[cache_key]

    def baseline(self, row):
        state = self.state(row)
        raw, model = legacy.raw_center(row, state)
        if raw is None:
            return None
        singles = scope_for(row)["company_count"] == 1
        choices = [state.by_org_svc[(row["org"], row["svc"])], state.by_org[row["org"]], state.by_svc[row["svc"]], state.records]
        pool = next((p for p in choices if len(p) >= 8), state.records)
        vals, latest = legacy.values(pool), legacy.values(recent(pool, row["date"], 90))
        center = float(raw)
        if len(vals) >= 8 and len(latest) >= 5:
            weight = (0.12 if np.std(latest) >= 0.55 else 0.18) if singles else (0.05 if np.std(latest) >= 0.55 else 0.10)
            center = legacy.quantile_clip((1-weight)*center + weight*np.mean(latest) + (0.08 if singles else 0.04)*(np.mean(latest)-np.mean(vals)), vals)
        center = round(float(center), 4)
        count = scope_for(row)["company_count"]
        if count == 1:
            if row["svc"] == "electric_construction":
                center = self.electric(row, state, center)
            else:
                center = legacy.local_single(center, state, False)
            return [center], model
        recs = [legacy.company1(row, state, center), center]
        if count == 3:
            recs.append(legacy.company3(row, state, center))
        return recs, model

    def electric(self, row, state, center):
        if len(state.records) < 20:
            return center
        parts = []
        org = state.by_org[row["org"]][-30:]
        if len(org) >= 5:
            parts.append((legacy.robust_center(org), 0.35 if len(org) >= 10 else 0.25))
        region = state.by_region[row["region"]][-50:]
        if row["region"] != "지역미상" and len(region) >= 8:
            parts.append((legacy.robust_center(region), 0.20))
        amt = state.by_svc_amt[(row["svc"], row["amt"])][-80:]
        if len(amt) >= 10:
            parts.append((legacy.robust_center(amt), 0.20))
        year, quarter = recent(state.records, row["date"], 365), recent(state.records, row["date"], 90)
        if len(year) >= 20:
            parts.append((legacy.robust_center(year), 0.20))
        if len(quarter) >= 10:
            parts.append((legacy.robust_center(quarter), 0.10 if np.std(legacy.values(quarter)) >= 0.55 else 0.15))
        pred = legacy.weighted_mean(parts) if parts else legacy.robust_center(state.records[-100:])
        return round(legacy.quantile_clip(pred, legacy.values(state.records[-300:])), 4)

    def family_center(self, row, fallback):
        family, issuer = self.pools(row)
        family = recent(family, row["date"], 365)[-200:]
        issuer = recent(issuer, row["date"], 365)[-50:]
        if len(family) < 20:
            return fallback
        prior = legacy.robust_center(family)
        weight = len(issuer)/(len(issuer)+20)
        local = legacy.robust_center(issuer) if issuer else prior
        return round(legacy.quantile_clip(weight*local+(1-weight)*prior, legacy.values(family)), 4)

    def interval(self, row, retained, fallback):
        """Past open-interval coverage, smoothed +/-0.10pp, issuer shrunk to family."""
        family, issuer = self.pools(row)
        if len(family) < 50:
            return fallback
        def scores(records, label):
            cache_key = ("interval", pool_key(row), row["org"] if label == "issuer" else "", label)
            if cache_key not in self.cache:
                valid = [r for r in records if np.isfinite(r.get("winner", np.nan)) and abs(r["winner"]) < 10]
                lower = np.array([r["value"] for r in valid])
                upper = np.array([r["winner"] for r in valid])
                w = np.array([2**(-(row["date"]-r["date"]).days/180) for r in valid])
                hits = (lower[:,None] < GRID) & (GRID < upper[:,None])
                cumulative = np.cumsum(np.pad(hits.astype(float), ((0,0),(6,5))),axis=1)
                smooth = (cumulative[:,11:]-cumulative[:,:-11])/11
                self.cache[cache_key] = (lower, upper, w, smooth)
            lower, upper, w, smooth = self.cache[cache_key]
            covered = np.zeros(len(lower), dtype=bool)
            for x in retained:
                covered |= (lower < x) & (x < upper)
            return (np.einsum("i,ij->j", w*(~covered), smooth)/w.sum() if w.sum() else np.zeros(len(GRID))), len(lower)
        global_score, n = scores(family, "family")
        local_score, m = scores(issuer, "issuer")
        if n < 30:
            return fallback
        weight = m/(m+60)
        score = weight*local_score + (1-weight)*global_score
        best = np.flatnonzero(np.isclose(score, score.max(), atol=1e-12))
        return float(GRID[best[np.argmin(abs(GRID[best]-fallback))]])

    def predict(self, row, alternatives=True):
        if self.last_date is not None and self.last_date >= row["date"]:
            raise ValueError("현재일 또는 미래의 낙찰결과가 추천 이력에 포함되어 있습니다.")
        if self.cache_date != row["date"]:
            self.cache, self.cache_date = {}, row["date"]
        result = self.baseline(row)
        if result is None:
            return None
        recs, model = result
        all_recs = {"current": recs}
        if alternatives:
            center_index = 0 if len(recs) == 1 else 1
            for strategy in STRATEGIES[1:]:
                updated = list(recs)
                index = center_index if strategy.endswith("center") else 0 if strategy.endswith("direction") else 2
                if index < len(recs) and not (len(recs) == 1 and strategy in ("interval_direction", "interval_line")):
                    updated[index] = (self.family_center(row, recs[index]) if strategy == "family_center" else
                        self.interval(row, [r for i,r in enumerate(recs) if i != index], recs[index]))
                all_recs[strategy] = updated
        return {"recommendations": all_recs, "model": model, "family": row["family"],
                "scope": scope_for(row), "prior_rows": len(self.state(row).records)}


def load_policy():
    path = Path(__file__).with_name("model_policy.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"rules": {}}


def recommend_bid(bid, history, policy=None):
    row = bid_record(bid)
    d = prepare_history(history)
    d = d[d["_date"] < row["date"]]
    engine = PredictionEngine()
    for source in d.to_dict("records"):
        engine.add(source_record(source))
    prediction = engine.predict(row)
    if prediction is None:
        return None
    policy = load_policy() if policy is None else policy
    active = not policy.get("effective_from") or row["date"] >= pd.Timestamp(policy["effective_from"])
    key = row["family"] + "|" + scope_for(row)["scope"]
    route = policy.get("rules", {}) if active else {}
    strategy = route.get(key + "|" + row["org"], route.get(key, "current"))
    prediction["strategy"] = strategy
    prediction["rates"] = prediction["recommendations"].get(strategy, prediction["recommendations"]["current"])
    return prediction


def recommend_bids(bids, history, policy=None):
    """One chronological history build per batch; preserve the input notice order."""
    policy = load_policy() if policy is None else policy
    prepared = prepare_history(history)
    records = [source_record(s) for s in prepared.to_dict("records")]
    targets = [(i, bid_record(b)) for i,b in enumerate(bids)]
    results = [None] * len(bids)
    instance, cursor = PredictionEngine(), 0
    for i,row in sorted(targets, key=lambda item: item[1]["date"]):
        while cursor < len(records) and records[cursor]["date"] < row["date"]:
            instance.add(records[cursor])
            cursor += 1
        active = not policy.get("effective_from") or row["date"] >= pd.Timestamp(policy["effective_from"])
        route = policy.get("rules", {}) if active else {}
        key = row["family"] + "|" + scope_for(row)["scope"]
        strategy = route.get(key+"|"+row["org"], route.get(key,"current"))
        prediction = instance.predict(row, alternatives=strategy!="current")
        if prediction is not None:
            prediction["strategy"] = strategy
            prediction["rates"] = prediction["recommendations"].get(strategy,prediction["recommendations"]["current"])
        results[i] = prediction
    return results
