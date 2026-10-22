"""Authenticated review-result publication. No bid data or passwords in source.

Does not import the prediction engine or modify the history ledger.
Published XLSX originals must also be retained in the user's private storage:
Community Cloud's local data directory is not durable across server recreation.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEADERS = ["공고명", "낙찰하한율", "업체1추천", "업체2추천", "업체3추천"]
MAX_FILE = 10 * 1024 * 1024
MAX_EXPANDED = 40 * 1024 * 1024
PUBLICATION_ID = re.compile(r"\d{8}_[0-9a-f]{16}\Z")


class PublicationError(ValueError):
    """A safe-to-display validation failure, without source contents."""


def parse_result(raw: bytes) -> dict:
    """Read bounded OOXML values only; never evaluate formulas or fetch links."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE:
        raise PublicationError("파일 크기가 비정상입니다. 10MB 이하의 검토안 XLSX만 사용하세요.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 500 or len(names) != len(set(names)):
                raise PublicationError("비정상적인 XLSX 구성입니다.")
            if sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise PublicationError("압축 해제 크기가 허용 범위를 넘습니다.")
            if any(i.flag_bits & 1 for i in infos):
                raise PublicationError("암호화된 XLSX는 게시할 수 없습니다.")
            if any("vbaproject" in n.lower() for n in names):
                raise PublicationError("매크로 포함 파일은 게시할 수 없습니다.")

            def xml(name: str):
                if name not in names:
                    raise PublicationError("XLSX 필수 구성요소가 없습니다.")
                data = archive.read(name)
                # XML parser need not resolve DTDs or external entities.
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper() or b"\x00" in data:
                    raise PublicationError("허용하지 않는 XML 형식입니다.")
                return ET.fromstring(data)

            shared = []
            if "xl/sharedStrings.xml" in names:
                for item in xml("xl/sharedStrings.xml").findall("s:si", NS):
                    shared.append("".join(t.text or "" for t in item.iterfind(".//s:t", NS)))
            relationships = xml("xl/_rels/workbook.xml.rels")
            targets = {}
            for rel in relationships:
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                if ".." in PurePosixPath(target).parts or "\\" in target:
                    raise PublicationError("허용하지 않는 시트 경로입니다.")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                targets[rel.get("Id")] = target
            sheets = {}
            summary_formula = False
            for sheet in xml("xl/workbook.xml").findall("s:sheets/s:sheet", NS):
                name = sheet.get("name", "")
                target = targets.get(sheet.get("{" + REL_NS + "}id"))
                if not target:
                    raise PublicationError("시트 연결을 확인할 수 없습니다.")
                rows = []
                for row in xml(target).findall("s:sheetData/s:row", NS):
                    if len(rows) >= 2000:
                        raise PublicationError("검토안 시트가 2,000행을 넘습니다. 원장이 아닌 결과파일을 사용하세요.")
                    values = {}
                    for cell in row.findall("s:c", NS):
                        address = cell.get("r", "")
                        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", address)
                        if not match:
                            raise PublicationError("셀 주소 형식을 확인할 수 없습니다.")
                        col = 0
                        for char in match.group(1):
                            col = col * 26 + ord(char) - 64
                        if col > 100:
                            raise PublicationError("검토안 시트가 100열을 넘습니다.")
                        value_element = cell.find("s:v", NS)
                        value = value_element.text if value_element is not None else ""
                        if cell.get("t") == "inlineStr":
                            value = "".join(t.text or "" for t in cell.iterfind("s:is//s:t", NS))
                        elif cell.get("t") == "s":
                            value = shared[int(value)]
                        if cell.find("s:f", NS) is not None:
                            if name == "투찰전략":
                                summary_formula = True
                            if value in (None, ""):
                                value = "[수식: 원본 Excel에서 확인]"
                        values[col] = str(value or "")
                    if values:
                        rows.append([values.get(c, "") for c in range(1, max(values) + 1)])
                sheets[name] = rows
    except PublicationError:
        raise
    except (zipfile.BadZipFile, KeyError, IndexError, ValueError, ET.ParseError, RuntimeError) as exc:
        raise PublicationError("읽을 수 없는 XLSX입니다. 완성된 검토안 파일을 확인하세요.") from exc

    if summary_formula:
        raise PublicationError("요약 추천값에 수식이 있습니다. 값이 확정된 검토안만 게시하세요.")
    table = sheets.get("투찰전략", [])
    header_index = next((i for i, row in enumerate(table[:20]) if row[:5] == HEADERS), None)
    if header_index is None:
        raise PublicationError("투찰전략 시트의 5개 요약열이 없습니다. 낙찰원장이나 입찰서류함은 이 메뉴에 업로드하지 마세요.")
    records = []
    for row in table[header_index + 1:]:
        row = (row + [""] * 5)[:5]
        if not row[0] or not any(row[1:]):
            continue
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%", row[1]):
            raise PublicationError("낙찰하한율 표기를 확인하세요.")
        if not 0 < float(row[1].rstrip("%")) <= 100:
            raise PublicationError("낙찰하한율 범위를 확인하세요.")
        rates = row[2:]
        for rate in rates:
            if rate not in ("", "참여대상 없음", "없음", "보류", "입력 보류"):
                if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?%", rate):
                    raise PublicationError("요약 추천값 표기를 확인하세요.")
                if abs(float(rate.rstrip("%"))) >= 10:
                    raise PublicationError("0% 기준 추천사정률 범위를 확인하세요.")
        residential = "감리" in row[0] and any(k in row[0] for k in ("공동주택", "주택건설", "주거시설", "아파트", "주상복합"))
        if residential and any(re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?%", r) for r in rates[1:]):
            raise PublicationError("공동주택 감리는 당사 1개사 기준입니다. 업체2·3 값이 없는 수정본을 사용하세요.")
        records.append(dict(zip(HEADERS, row)))
    if not records or len(records) > 500:
        raise PublicationError("게시할 공고 수를 확인하세요.")
    return {"summary": records, "sheets": sheets, "title": table[0][0] if table and table[0] else "", "sha256": hashlib.sha256(raw).hexdigest()}


def analysis_date_from_name(filename: str) -> str:
    match = re.match(r"^(\d{4})(\d{2})(\d{2})_", Path(filename).name)
    if not match:
        raise PublicationError("파일명은 분석일 YYYYMMDD_로 시작해야 합니다.")
    try:
        result = date(*map(int, match.groups()))
    except ValueError as exc:
        raise PublicationError("파일명의 분석일이 올바르지 않습니다.") from exc
    if result > datetime.now(KST).date():
        raise PublicationError("미래 분석일로 게시할 수 없습니다.")
    return result.isoformat()


def storage_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "private_publications"


def verify_publication(folder: Path) -> tuple[dict, bytes, dict]:
    if not PUBLICATION_ID.fullmatch(folder.name):
        raise PublicationError("게시자료 식별자를 확인할 수 없습니다.")
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    result_path = folder / "result.xlsx"
    if result_path.stat().st_size > MAX_FILE:
        raise PublicationError("게시자료 크기가 허용 범위를 넘습니다.")
    raw = result_path.read_bytes()
    result = parse_result(raw)
    expected_id = metadata["analysis_date"].replace("-", "") + "_" + result["sha256"][:16]
    if folder.name != expected_id or metadata.get("sha256") != result["sha256"] or metadata.get("rows") != len(result["summary"]):
        raise PublicationError("게시자료 해시·행수가 기록과 다릅니다. 표시를 중단합니다.")
    return metadata, raw, result


def publish_result(root: Path, filename: str, raw: bytes) -> tuple[dict, bool]:
    result = parse_result(raw)
    analysis_date = analysis_date_from_name(filename)
    title_dates = re.findall(r"20\d{2}[.\-/]\d{2}[.\-/]\d{2}", result["title"])
    if not title_dates or analysis_date not in [x.replace(".", "-").replace("/", "-") for x in title_dates]:
        raise PublicationError("파일명의 분석일과 검토안 제목의 날짜가 다릅니다.")
    publication_id = analysis_date.replace("-", "") + "_" + result["sha256"][:16]
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".publish.lock"
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PublicationError("다른 게시가 진행 중입니다. 기존 자료는 변경하지 않았습니다.") from exc
    os.close(descriptor)
    stage = root / (".staging_" + uuid.uuid4().hex)
    try:
        destination = root / publication_id
        if destination.exists():
            metadata, old_raw, _ = verify_publication(destination)
            if old_raw != raw:
                raise PublicationError("기존 게시자료와 충돌합니다.")
            return metadata, False
        metadata = {"schema": 1, "analysis_date": analysis_date,
                    "published_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "filename": Path(filename).name, "sha256": result["sha256"],
                    "rows": len(result["summary"]), "status": "검토안", "id": publication_id}
        stage.mkdir(mode=0o700)
        with (stage / "result.xlsx").open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        (stage / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if hashlib.sha256((stage / "result.xlsx").read_bytes()).hexdigest() != result["sha256"]:
            raise PublicationError("저장 후 파일 검사에 실패했습니다.")
        os.replace(stage, destination)
        verify_publication(destination)
        return metadata, True
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        lock.unlink(missing_ok=True)


def authenticate(st) -> None:
    try:
        secret = str(st.secrets.get("ADMIN_PWD", ""))
    except Exception:
        secret = ""
    if not secret or secret == "admin1234":
        st.error("게시자료 보호를 위해 Streamlit Secrets의 별도 ADMIN_PWD 설정이 필요합니다. 기본 비밀번호는 허용하지 않습니다.")
        st.stop()
    token = hashlib.sha256(("private-publication:" + secret).encode()).hexdigest()
    if st.session_state.get("publication_auth") == token:
        return

    def check_password():
        candidate = st.session_state.pop("publication_password", "")
        if hmac.compare_digest(candidate.encode(), secret.encode()):
            st.session_state["publication_auth"] = token
        else:
            st.session_state.pop("publication_auth", None)
            st.session_state["publication_error"] = True

    st.info("이 페이지의 전략표와 다운로드는 관리자 인증 후에만 표시됩니다.")
    with st.form("publication_login"):
        st.text_input("게시자료 열람·등록 비밀번호", type="password", key="publication_password")
        st.form_submit_button("인증", on_click=check_password)
    if st.session_state.pop("publication_error", False):
        st.error("인증되지 않았습니다.")
    st.stop()


def render() -> None:
    import streamlit as st
    st.set_page_config(page_title="게시된 투찰전략", page_icon="📋", layout="wide")
    st.title("게시된 투찰전략")
    authenticate(st)  # No private storage reads or download elements before this gate.
    if st.sidebar.button("게시자료 로그아웃"):
        st.session_state.pop("publication_auth", None)
        st.rerun()
    st.caption("결과게시 v1.0.0 · 기존 추천엔진·낙찰원장을 변경하거나 재계산하지 않습니다.")
    st.warning("게시되는 자료는 투찰 전 검토안입니다. 보류 공고·최종 정정·자격·평가점수·마감시각을 확인하세요. 실제 투찰은 제출하지 않습니다.")
    root = storage_root()
    with st.expander("완성된 전략파일 게시", expanded=not root.exists()):
        uploaded = st.file_uploader("투찰전략 검토안 XLSX — 입찰서류함·낙찰원장이 아닙니다", type=["xlsx"], key="publication_upload")
        if uploaded is not None:
            try:
                raw = uploaded.getvalue()
                preview = parse_result(raw)
                as_of = analysis_date_from_name(uploaded.name)
                st.caption(f"분석일 {as_of} · 공고 {len(preview['summary'])}건 · SHA-256 {preview['sha256']}")
                st.dataframe(preview["summary"], hide_index=True, use_container_width=True)
                approved = st.checkbox("수정본·분석일·보류사항을 확인했습니다. 원본 그대로 게시합니다.", key="publication_approve_" + preview["sha256"])
                if st.button("검증 후 게시", disabled=not approved):
                    metadata, created = publish_result(root, uploaded.name, raw)
                    st.success("게시 저장·재검증 완료" if created else "이미 동일한 자료가 게시되어 있습니다. 중복 저장하지 않았습니다.")
                    st.caption(metadata["id"])
            except (PublicationError, OSError, KeyError, json.JSONDecodeError) as exc:
                st.error("게시하지 못했습니다: " + (str(exc) if isinstance(exc, PublicationError) else "저장공간 또는 파일 상태를 확인하세요."))
    publications = []
    broken = 0
    if root.exists():
        for folder in root.iterdir():
            if folder.is_dir() and PUBLICATION_ID.fullmatch(folder.name):
                try:
                    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                    if metadata.get("id") != folder.name:
                        raise ValueError("identifier")
                    publications.append(metadata)
                except (OSError, ValueError):
                    broken += 1
    if broken:
        st.warning(f"게시기록 {broken}개를 읽지 못했습니다. 정상 완료로 처리하지 않습니다.")
    if not publications:
        st.info("아직 게시된 결과가 없습니다. 위 메뉴에서 완성된 수정본을 한 번 업로드하고 '검증 후 게시'를 누르세요.")
        return
    publications.sort(key=lambda m: (m.get("analysis_date", ""), m.get("published_at", "")), reverse=True)
    selected = st.selectbox("게시 이력", publications, format_func=lambda m: f"{m.get('analysis_date', '미확인')} · {m.get('filename', '')} · {m.get('published_at', '')}")
    try:
        metadata, raw, result = verify_publication(root / selected["id"])
    except (PublicationError, OSError, ValueError, KeyError):
        st.error("선택한 게시자료의 저장·해시 검증에 실패했습니다. 표시와 다운로드를 중단합니다.")
        return
    if metadata["analysis_date"] != datetime.now(KST).date().isoformat():
        st.warning("오늘 분석한 결과가 아닙니다. 표시된 분석일과 공고 마감을 확인하세요.")
    st.subheader(f"{metadata['analysis_date']} · {metadata['rows']}건 · 검토안")
    st.dataframe(result["summary"], hide_index=True, use_container_width=True)
    held = sum("보류" in row["공고명"] for row in result["summary"])
    if held:
        st.warning(f"보류 표시 공고 {held}건은 참고값을 자동입력하거나 확정금액으로 사용하지 마세요.")
    st.download_button("게시한 원본 Excel 다운로드", data=raw, file_name=metadata["filename"], mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("파일 SHA-256: " + metadata["sha256"])
    for name in ("전략근거", "데이터출처", "수정이력"):
        if name in result["sheets"]:
            with st.expander(name):
                rows = result["sheets"][name]
                width = max((len(r) for r in rows), default=0)
                st.dataframe([{str(i+1): r[i] if i < len(r) else "" for i in range(width)} for r in rows], hide_index=True, use_container_width=True)
    st.info("원본은 OneDrive에도 보관하세요. 이 서버의 파일은 재생성·재배포 환경에 따라 사라질 수 있으며, 그 경우 같은 검토안을 다시 게시해야 합니다.")


if __name__ == "__main__":
    render()
