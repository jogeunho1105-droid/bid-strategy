"""Shared, result-independent tender classification and participation rules.

These rules use announcement fields only.  They are shared by the app and the
chronological backtest so the backtest cannot silently allow extra companies.
Unknown KEPCO branches retain the existing operational national-bid assumption;
history is classified independently by the actual price threshold.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import math
import re


MODEL_FAMILY_LABELS = {
    "construction_management": "일반 건설사업관리",
    "diagnosis": "일반진단",
    "design": "설계",
    "supervision": "일반감리",
    "kepco_supervision": "한국전력공사감리",
    "residential_supervision": "공동주택 및 주거시설 감리",
    "electric_construction": "전기공사 단일참여",
    "other": "미분류",
}
DIAGNOSIS_SERVICES = frozenset(
    {"VLF", "PD", "optical", "concrete", "ultrasound", "diagnosis_other"}
)
KEPCO_LOCAL_ORGS = {
    "한국전력공사 부산울산본부": 2,
    "한국전력공사 경북본부": 1,
    "한국전력공사 대구본부": 1,
}
KEPCO_NATIONAL_COMPANY_COUNT = 3
KEPCO_BUSAN_ULSAN_ORG = "한국전력공사 부산울산본부"
KEPCO_BUSAN_THREE_COMPANY_BASE_THRESHOLD = 100_000_000


def _text(value):
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "nat", "none", "<na>"} else result


def _amount(value):
    try:
        number = Decimal(_text(value).replace(",", "").replace("원", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def parse_date_value(value):
    """Parse dates without guessing YY.MM.DD as month/day/year.

    Excel serial dates use the Windows workbook epoch 1899-12-30. Missing and
    malformed values return None; callers decide whether a default is allowed.
    """
    text = _text(value)
    if not text:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    match = re.match(
        r"^(\d{4}|\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})",
        text,
    )
    if match:
        year, month, day = map(int, match.groups())
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}", text) and 1900 <= int(text) <= 2100:
        return datetime(int(text), 1, 1)
    try:
        serial = float(text)
        if math.isfinite(serial) and 1 <= serial < 100_000:
            return datetime(1899, 12, 30) + timedelta(days=serial)
    except (ValueError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_date_series(series):
    """Pandas-compatible wrapper retaining index and datetime64 dtype."""
    import pandas as pd

    return pd.to_datetime(series.map(parse_date_value), errors="coerce")


def year_from_value(value, default_year=None):
    parsed = parse_date_value(value)
    return parsed.year if parsed is not None else (default_year or datetime.now().year)


def notice_amount_for_year(year):
    """Existing 2021-2026 operational thresholds; no legal update implied."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = datetime.now().year
    if year <= 2022:
        return 210_000_000
    if year <= 2024:
        return 220_000_000
    return 230_000_000


def is_below_notice(base, year):
    amount = _amount(base)
    if amount is None:
        return False
    # base / 1.1 < notice, with exact arithmetic at the inclusive upper bound.
    return amount * 10 < Decimal(notice_amount_for_year(year)) * 11


def is_kepco(org):
    compact = re.sub(r"\s+", "", _text(org))
    return "한국전력공사" in compact or bool(
        re.fullmatch(r"한전(?:본사|[가-힣]+(?:지역)?본부)?", compact)
    )


def kepco_branch(org):
    if not is_kepco(org):
        return None
    compact = re.sub(r"\s+", "", _text(org))
    for branch in ("부산울산", "경북", "대구"):
        if re.search(branch + r"(?:지역)?본부", compact):
            return branch
    return None


def _service_keyword(text):
    upper = text.upper()
    # Explicit contract work wins over materials/instruments in the project name.
    if "건설사업관리" in text or "감독권한대행" in text:
        return "construction_management"
    if "감리" in text:
        return "supervision"
    if "설계" in text:
        return "design"
    diagnostic_work = bool(re.search(r"(?:진단|측정)(?!\s*(?:장비|기기|장치|시스템))|점검|안전관리대행", text))
    equipment_or_construction = any(word in text for word in ("설치", "교체", "납품", "구매", "구입", "제조", "구축", "공사"))
    if equipment_or_construction and not diagnostic_work:
        return None
    if "VLF" in upper:
        return "VLF"
    if re.search(r"(?<![A-Z])PD(?![A-Z])", upper) or "부분방전" in text:
        return "PD"
    if "광학" in text:
        return "optical"
    if "콘크리트" in text:
        return "concrete"
    if "초음파" in text:
        return "ultrasound"
    if diagnostic_work:
        return "diagnosis_other"
    return None


def classify_service(name, industry=None):
    """Keep existing service subtypes, with industry fallback for vague titles.

    Explicit service work in either field prevents a construction-industry token
    from swallowing a supervision/design/diagnostic contract. Residential words
    do not determine service type; they refine supervision only in the family.
    """
    title, industry_text = _text(name), _text(industry)
    title_service = _service_keyword(title)
    if title_service:
        return title_service
    industry_service = _service_keyword(industry_text)
    if industry_service:
        return industry_service
    service_like = any(
        word in title + " " + industry_text
        for word in ("용역", "기술사", "엔지니어링", "안전관리", "건축사")
    )
    electric_industry = bool(
        re.search(r"(?:^|[,/;\s])전기(?:공사(?:업)?|$|[,/;\s])", industry_text)
    )
    if not service_like and (electric_industry or "전기공사" in title):
        return "electric_construction"
    return "other"


def classify_service_row(row):
    return classify_service(row.get("공고명", ""), row.get("업종", ""))


def classify_model_family(name, industry=None, org=None):
    service = classify_service(name, industry)
    if service in DIAGNOSIS_SERVICES:
        return "diagnosis"
    if service == "supervision":
        if is_kepco(org):
            return "kepco_supervision"
        text = _text(name) + " " + _text(industry)
        if any(
            word in text
            for word in (
                "공동주택", "주거시설", "아파트", "주상복합", "주택건설",
                "연립주택", "다세대주택", "오피스텔",
            )
        ):
            return "residential_supervision"
    return service if service in MODEL_FAMILY_LABELS else "other"


def kepco_local_company_count(org, base):
    branch = kepco_branch(org)
    if branch == "부산울산":
        amount = _amount(base)
        return 3 if amount is not None and amount >= 100_000_000 else 2
    return 1 if branch in {"경북", "대구"} else 3


def _default_info():
    return {
        "applicable": False, "scope": "기존분석", "company_count": 3,
        "year": None, "notice_amount": None, "estimated_price": None,
        "scope_assumed": False,
        "basis": "기존 지역별 참여업체 기준에 따른 3개사 추천",
    }


def classify_kepco_scope(bid):
    org = bid.get("org", "")
    service = classify_service(bid.get("name", ""), bid.get("industry", ""))
    if not is_kepco(org) or service != "supervision":
        return _default_info()
    year = year_from_value(bid.get("deadline"))
    notice = notice_amount_for_year(year)
    amount = _amount(bid.get("base"))
    estimated = float(amount / Decimal("1.1")) if amount is not None else None
    branch = kepco_branch(org)
    local = branch is not None and is_below_notice(amount, year)
    if local:
        count = kepco_local_company_count(org, amount)
        basis = f"{year}년 추정가격이 고시금액 {notice / 1e8:.1f}억원 미만; {branch} 지역제한 {count}개사"
        if branch == "부산울산":
            basis += "; 기초금액 1억원 이상 3개사·미만 2개사"
    else:
        count = 3
        if branch is None:
            basis = "타 지역 지역제한 건은 업로드하지 않는 기존 운영규칙에 따라 전국입찰 가정"
        elif amount is None:
            basis = "기초금액 미확인으로 기존 전국입찰 가정; 금액 확인 필요"
        else:
            basis = f"{year}년 추정가격이 고시금액 {notice / 1e8:.1f}억원 이상"
        basis += "; 전국입찰 실적 보유 3개사만 참여 (경북 2개사 제외)"
    return {
        "applicable": True, "scope": "지역제한" if local else "전국입찰",
        "company_count": count, "year": year, "notice_amount": notice,
        "estimated_price": estimated,
        "scope_assumed": branch is None or amount is None or parse_date_value(bid.get("deadline")) is None,
        "basis": basis,
    }


def classify_bid_scope(bid):
    service = classify_service(bid.get("name", ""), bid.get("industry", ""))
    if service == "electric_construction":
        info = _default_info()
        info.update(
            applicable=True, scope="전기공사 단일참여", company_count=1,
            year=year_from_value(bid.get("deadline")),
            basis="전기공사는 당사 1개사 참여 기준 단일 1순위 추천값 적용",
        )
        return info
    if is_kepco(bid.get("org", "")) and service in DIAGNOSIS_SERVICES:
        amount = _amount(bid.get("base"))
        count = 2 if service == "VLF" and (amount is None or amount < 100_000_000) else 3
        info = _default_info()
        info.update(
            company_count=count,
            basis=("한전 VLF진단은 기초금액 1억원 이상 3개사·미만 2개사"
                   if service == "VLF" else "한전 기타진단은 3개사 참여"),
            scope_assumed=service == "VLF" and amount is None,
        )
        # applicable refers specifically to history scope filtering. Diagnostics
        # retain normal family/subtype history, so it deliberately stays False.
        return info
    return classify_kepco_scope(bid)


def historical_scope(name, industry, org, base, date):
    """Label all KEPCO supervision history, including other branches.

    Unknown dates/prices are excluded from both scope-specific pools. In contrast
    with current-bid participation, no national assumption is made for history.
    """
    if not is_kepco(org) or classify_service(name, industry) != "supervision":
        return "해당없음"
    parsed = parse_date_value(date)
    if parsed is None or _amount(base) is None:
        return "확인필요"
    return "지역제한" if is_below_notice(base, parsed.year) else "전국입찰"
