"""Read-only, password-free display of explicitly approved strategy summaries.

Only publications/latest.json is read. No ledger, private workbook, credential,
login, uploader, prediction engine, network request or server-side write path.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
HEADERS = ["공고명", "낙찰하한율", "업체1추천", "업체2추천", "업체3추천"]
TOP_KEYS = {"schema_version", "analysis_date", "target_date", "data_through", "revision", "model_version", "source_sha256", "notice", "rows"}
ROW_KEYS = {"no", "notice_id", "name", "deadline", "lower_limit", "rates", "amounts_won", "status", "strategy", "family", "field_samples", "agency_samples", "agency_recent90", "sample_start", "sample_end", "basis", "warning"}
RATE = re.compile(r"[+-]?\d+(?:\.\d{1,4})?%\Z")
RESIDENTIAL = ("공동주택", "주택건설", "주거시설", "아파트", "주상복합", "연립주택", "다세대주택", "오피스텔")


class PublicationError(ValueError):
    pass


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def validate_publication(data: dict) -> dict:
    """Fail closed on malformed, extra or private fields; never recalculate rates."""
    _fail(isinstance(data, dict) and set(data) == TOP_KEYS, "게시자료의 허용 항목을 확인하세요.")
    _fail(data["schema_version"] == 1, "게시 형식 버전을 확인하세요.")
    try:
        analysis, target, cutoff = (date.fromisoformat(data[k]) for k in ("analysis_date", "target_date", "data_through"))
    except (ValueError, TypeError) as exc:
        raise PublicationError("게시 기준일을 확인하세요.") from exc
    _fail(cutoff <= analysis <= target, "분석일·자료 기준일·대상일 순서가 맞지 않습니다.")
    _fail(bool(re.fullmatch(r"[0-9a-f]{64}", str(data["source_sha256"]))), "원본 식별값이 없습니다.")
    for key in ("revision", "model_version", "notice"):
        _fail(isinstance(data[key], str) and 0 < len(data[key]) <= 2000, "게시 설명을 확인하세요.")
    rows = data["rows"]
    _fail(isinstance(rows, list) and 0 < len(rows) <= 500, "게시할 공고 수를 확인하세요.")
    numbers, identifiers = set(), set()
    for row in rows:
        _fail(isinstance(row, dict) and set(row) == ROW_KEYS, "공고별 허용 항목을 확인하세요.")
        for key in ("no", "field_samples", "agency_samples", "agency_recent90"):
            _fail(type(row[key]) is int and row[key] >= 0, "공고번호·표본수를 확인하세요.")
        _fail(row["no"] > 0 and row["no"] not in numbers, "표 번호가 중복됩니다.")
        _fail(isinstance(row["notice_id"], str) and row["notice_id"] not in identifiers, "공고번호가 중복됩니다.")
        numbers.add(row["no"]); identifiers.add(row["notice_id"])
        for key in ("notice_id", "name", "strategy", "family", "basis", "warning"):
            _fail(isinstance(row[key], str) and 0 < len(row[key]) <= 4000, "공고 설명을 확인하세요.")
        _fail(row["strategy"] in {"S1", "S2", "S3", "S4", "S5"}, "적용전략을 확인하세요.")
        _fail(row["status"] in {"조건부 검토", "보류"}, "검토 상태를 확인하세요.")
        try:
            deadline = datetime.fromisoformat(row["deadline"])
            start, end = date.fromisoformat(row["sample_start"]), date.fromisoformat(row["sample_end"])
        except (ValueError, TypeError) as exc:
            raise PublicationError("공고별 날짜를 확인하세요.") from exc
        _fail(deadline.utcoffset() == timedelta(hours=9) and deadline.date() == target, "투찰마감의 날짜·시간대를 확인하세요.")
        _fail(start <= end <= cutoff, "표본 기간이 자료 기준일을 초과합니다.")
        _fail(row["agency_recent90"] <= row["agency_samples"] <= row["field_samples"], "표본수 관계를 확인하세요.")
        lower = row["lower_limit"]
        _fail(isinstance(lower, str) and bool(RATE.fullmatch(lower)), "낙찰하한율을 확인하세요.")
        _fail(0 < float(lower[:-1]) <= 100, "낙찰하한율 범위를 확인하세요.")
        rates, amounts = row["rates"], row["amounts_won"]
        _fail(isinstance(rates, list) and isinstance(amounts, list) and len(rates) == len(amounts) == 3, "출력 슬롯 수가 다릅니다.")
        numeric = []
        for rate, amount in zip(rates, amounts):
            valid = isinstance(rate, str) and bool(RATE.fullmatch(rate))
            if valid:
                value = float(rate[:-1])
                _fail(math.isfinite(value) and abs(value) < 10, "사정률은 0% 기준으로 확인하세요.")
                _fail(type(amount) is int and amount > 0, "검토금액을 확인하세요.")
            else:
                _fail(isinstance(rate, str) and rate in {"보류", "참여대상 없음"} and amount is None, "보류·미참여 슬롯에 숫자가 있습니다.")
            numeric.append(valid)
        if row["status"] == "보류":
            _fail(rates == ["보류"] * 3 and amounts == [None] * 3, "보류 공고의 추천값 표시를 차단합니다.")
        else:
            _fail(numeric[0], "첫 번째 추천값이 없습니다.")
            _fail(numeric == sorted(numeric, reverse=True), "추천 슬롯 순서를 확인하세요.")
        residential = "감리" in row["name"] and any(k in row["name"] for k in RESIDENTIAL)
        if residential or row["family"] in {"공동주택 전기감리", "전기공사"}:
            _fail(not any(numeric[1:]), "단일참여 공고의 업체2·3 숫자를 차단합니다.")
    text = json.dumps(data, ensure_ascii=False).lower()
    _fail(not any(x in text for x in ("http://", "https://", "sharepoint", "onedrive", "settings.json", "seed_state", "admin_pwd", "password", "access_token", "\\\\", "c:\\")), "게시자료에 허용하지 않는 경로·인증 항목이 있습니다.")
    return data


def load_publication(path: Path | None = None) -> dict:
    path = path or ROOT / "publications" / "latest.json"
    try:
        _fail(0 < path.stat().st_size <= 1_000_000, "게시자료 크기를 확인하세요.")
        return validate_publication(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError("검증된 게시자료를 읽을 수 없습니다. 이전 값을 임의로 표시하지 않습니다.") from exc


def summary_rows(data: dict) -> list[dict]:
    return [dict(zip(HEADERS, [row["name"], row["lower_limit"], *row["rates"]])) for row in data["rows"]]


def csv_bytes(data: dict) -> bytes:
    stream = io.StringIO(newline="")
    fields = ["공고번호", *HEADERS, "상태", "전략", "분야표본", "기관표본", "기관90일", "적용근거", "확인사항"]
    writer = csv.writer(stream); writer.writerow(fields)
    for r in data["rows"]:
        values = [r["notice_id"], r["name"], r["lower_limit"], *r["rates"], r["status"], r["strategy"], r["field_samples"], r["agency_samples"], r["agency_recent90"], r["basis"], r["warning"]]
        # Text export only. Prevent Excel interpreting any cell as a formula.
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
    return stream.getvalue().encode("utf-8-sig")


def render(st=None) -> None:
    if st is None:
        import streamlit as st
    st.title("투찰전략 · 적용근거")
    try:
        data = load_publication()
    except PublicationError as exc:
        st.error(str(exc)); st.stop(); return
    now = datetime.now(KST)
    st.caption(f"분석일 {data['analysis_date']} · 투찰대상일 {data['target_date']} · 개찰자료 상한 {data['data_through']} · {data['revision']}")
    if data["analysis_date"] != now.date().isoformat():
        st.warning(f"최근 게시본의 분석일은 {data['analysis_date']}입니다. 오늘 새로 분석한 결과가 아닙니다.")
    if all(datetime.fromisoformat(r["deadline"]) <= now for r in data["rows"]):
        st.warning("이 게시본의 공고는 원본에 기재된 투찰마감이 지났습니다. 과거 검토기록으로만 확인하세요.")
    st.info(data["notice"])
    held = sum(r["status"] == "보류" for r in data["rows"])
    columns = st.columns(3)
    for col, label, value in zip(columns, ("공고", "조건부 검토", "보류"), (len(data["rows"]), len(data["rows"])-held, held)):
        col.metric(label, f"{value}건")
    strategy_tab, basis_tab = st.tabs(["투찰전략", "적용근거"])
    with strategy_tab:
        st.caption("사정률은 0% 기준입니다. 예: -0.1000% = 100% 기준 99.9000%. 업체 슬롯은 실제 회사 배정·자격확인을 뜻하지 않습니다.")
        st.dataframe(summary_rows(data), use_container_width=True, hide_index=True, height=min(650, 80 + 55*len(data["rows"])))
        st.caption("공동주택 감리의 당사 1개사 기준을 반영했습니다. 보류 공고의 참고 계산값은 추천값으로 게시하지 않습니다.")
        with st.expander("산식상 검토금액 · 최종 제출금액 아님"):
            amounts = []
            for r in data["rows"]:
                for slot, value in enumerate(r["amounts_won"], 1):
                    if value is not None:
                        amounts.append({"공고명":r["name"], "출력슬롯":f"업체{slot}", "사정률":r["rates"][slot-1], "검토금액":f"{value:,}원"})
            st.dataframe(amounts, use_container_width=True, hide_index=True)
            st.caption("게시된 검토본의 금액을 그대로 표시합니다. A값·순공사원가·평가점수·원단위 처리는 공고 원문 확인 전의 계산가정입니다.")
    with basis_tab:
        st.caption(data["model_version"] + " · 이 화면에서는 새로 계산하거나 전략을 변경하지 않습니다.")
        for row in data["rows"]:
            with st.expander(f"{row['no']}. {row['name']} · {row['status']}", expanded=row["status"] == "보류"):
                st.text(f"공고번호: {row['notice_id']}\n투찰마감 원본값: {row['deadline'][:16].replace('T', ' ')} (한국시간)\n적용전략: {row['strategy']} / {row['family']}")
                st.text(f"분야표본 {row['field_samples']:,}건 · 동일기관 {row['agency_samples']:,}건 · 기관 최근90일 {row['agency_recent90']:,}건\n실제 분야표본 기간: {row['sample_start']} ~ {row['sample_end']}")
                st.text(row["basis"])
                st.warning(row["warning"])
        st.caption("분야별 표본은 서로 다릅니다. 과거 관측 적중률을 미래 낙찰확률로 표시하지 않으며, 대상 공고의 신규 전면 백테스트를 이번 게시에서 수행한 것은 아닙니다.")
    st.download_button("투찰전략·적용근거 CSV", csv_bytes(data), file_name=f"{data['analysis_date']}_투찰전략_적용근거_{data['revision']}.csv", mime="text/csv")
    if st.button("게시내용 새로고침"):
        st.rerun()
    st.caption("읽기 전용 공개 게시판 · 요청 시 검증본 갱신 · 자동 수집·예약·투찰 제출 기능 없음")
