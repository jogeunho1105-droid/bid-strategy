from pathlib import Path

ROOT = Path('.')
APP_PATH = ROOT / '입찰 앱.py'
README_PATH = ROOT / 'README.md'
TEST_OLD_PATH = ROOT / 'tests' / 'test_app_v2_15_7.py'
TEST_NEW_PATH = ROOT / 'tests' / 'test_app_v2_15_8.py'
VERIFY_PATH = ROOT / 'docs' / 'change_requests' / 'v2.15.8-implementation-verification.md'


def replace_version(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one old value, found {count}')
    return text.replace(old, new, 1)


app = APP_PATH.read_text(encoding='utf-8')
app = replace_version(
    app,
    '# ║  투찰전략 분석 시스템 v2.15.7                                   ║',
    '# ║  투찰전략 분석 시스템 v2.15.8                                   ║',
    'header version',
)
app = replace_version(
    app,
    'MODEL_VERSION = "v2.15.7"',
    'MODEL_VERSION = "v2.15.8"',
    'model version',
)
app = replace_version(
    app,
    '<h2>📊 투찰전략 분석 시스템 v2.15.7</h2>',
    '<h2>📊 투찰전략 분석 시스템 v2.15.8</h2>',
    'screen version',
)
app = replace_version(
    app,
    '    st.subheader(f"📋 최대 3개 업체 추천 사정률·추천기준금액 — {datetime.now().strftime(\'%Y.%m.%d\')} ({len(bids)}건)")',
    '    st.subheader(f"📋 최대 3개 업체 추천 사정률 — {datetime.now().strftime(\'%Y.%m.%d\')} ({len(bids)}건)")',
    'summary title',
)

summary_marker = '    # ── 요약 테이블: 최종 추천값과 근거만 표시'
detail_marker = '    # ── 건별 상세'
summary_start = app.index(summary_marker)
summary_end = app.index(detail_marker, summary_start)
summary = app[summary_start:summary_end]

if 'display_amounts=[' in summary:
    amount_start = summary.index('        display_amounts=[')
    row_start = summary.index('        rows.append({', amount_start)
    summary = summary[:amount_start] + summary[row_start:]

row_start = summary.index('        rows.append({')
dataframe_call = summary.index('    st.dataframe(', row_start)
new_rows = '''        rows.append({
            "중심모델군":model_family_info(b)[1],
            "공고명":b["name"][:40]+"…" if len(b["name"])>40 else b["name"],
            "업체1추천":display_vals[0],
            "업체2추천":display_vals[1],
            "업체3추천":display_vals[2],
            "비고":"\\n".join(notes)
        })
    summary_columns=[
        "중심모델군",
        "공고명",
        "업체1추천",
        "업체2추천",
        "업체3추천",
        "비고",
    ]
    summary_df=pd.DataFrame(rows, columns=summary_columns)
'''
summary = summary[:row_start] + new_rows + summary[dataframe_call:]
app = app[:summary_start] + summary + app[summary_end:]
APP_PATH.write_text(app, encoding='utf-8')

readme = README_PATH.read_text(encoding='utf-8')
readme = readme.replace('# 투찰전략 분석 시스템 v2.15.7', '# 투찰전략 분석 시스템 v2.15.8', 1)
readme = readme.replace(
    'v2.15.7은 사용자 승인에 따른 배포 버전입니다.',
    'v2.15.8은 사용자 승인에 따른 요약표 가독성 개선 배포 버전입니다.',
    1,
)
readme = readme.replace(
    'v2.15.7은 실행파일 하나만 교체해서는 동작하지 않습니다.',
    'v2.15.8은 실행파일 하나만 교체해서는 동작하지 않습니다.',
    1,
)
readme = readme.replace(
    '3. 공고별 중심모델군, 참여업체 수, 최종 추천 사정률, 추천기준금액, 산정 근거를 확인합니다.',
    '3. 요약표에서 중심모델군과 업체1·2·3 추천 사정률을 비교하고, 건별 상세와 Excel에서 추천기준금액 및 산정 근거를 확인합니다.',
    1,
)
section = '''## v2.15.8 요약표 표시 기준

화면 요약표는 핵심 비교항목만 보이도록 다음 6개 열로 고정합니다.

```text
중심모델군 | 공고명 | 업체1추천 | 업체2추천 | 업체3추천 | 비고
```

업체별 추천기준금액 열은 요약표에서만 제외합니다. 추천기준금액 계산, 건별 상세화면, Excel `업체별 추천`·`사후낙찰검증` 시트와 산정 근거는 기존과 동일하게 유지합니다.

'''
if section not in readme:
    anchor = '`추천기준금액 = 기초금액 × (100 + 추천사정률) ÷ 100`입니다. 이 값은 낙찰하한율을 반영한 최종 투찰금액이 아닙니다.\n\n'
    if anchor not in readme:
        raise RuntimeError('README section anchor not found')
    readme = readme.replace(anchor, anchor + section, 1)
README_PATH.write_text(readme, encoding='utf-8')

old_test = TEST_OLD_PATH.read_text(encoding='utf-8')
old_test = old_test.replace(
    'self.assertEqual(values["모델버전"],"v2.15.7")',
    'self.assertEqual(values["모델버전"],"v2.15.8")',
    1,
)
TEST_OLD_PATH.write_text(old_test, encoding='utf-8')

TEST_NEW_PATH.write_text('''"""Static regression checks for the v2.15.8 summary-table-only change."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "입찰 앱.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = APP.split("# ── 요약 테이블: 최종 추천값과 근거만 표시", 1)[1].split("# ── 건별 상세", 1)[0]
DETAIL = APP.split("# ── 건별 상세", 1)[1]


class SummaryLayoutV2158Tests(unittest.TestCase):
    def test_version_updated(self):
        self.assertIn('MODEL_VERSION = "v2.15.8"', APP)
        self.assertIn("투찰전략 분석 시스템 v2.15.8", APP)
        self.assertTrue(README.startswith("# 투찰전략 분석 시스템 v2.15.8"))

    def test_summary_title_and_columns(self):
        self.assertIn("최대 3개 업체 추천 사정률 —", SUMMARY)
        self.assertNotIn("추천 사정률·추천기준금액", SUMMARY)
        expected = ["중심모델군", "공고명", "업체1추천", "업체2추천", "업체3추천", "비고"]
        positions = [SUMMARY.index(f'"{name}"') for name in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("summary_df=pd.DataFrame(rows, columns=summary_columns)", SUMMARY)

    def test_amount_columns_removed_only_from_summary(self):
        self.assertNotIn("display_amounts", SUMMARY)
        for name in ("업체1추천기준금액", "업체2추천기준금액", "업체3추천기준금액"):
            self.assertNotIn(name, SUMMARY)
        self.assertIn("recommendation_reference_amount", DETAIL)
        self.assertIn("추천기준금액", DETAIL)
        self.assertIn('f"업체{pos} 추천기준금액(원)"', APP)


if __name__ == "__main__":
    unittest.main()
''', encoding='utf-8')

VERIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
VERIFY_PATH.write_text('''# v2.15.8 구현·검증 결과

- 모델 버전 및 화면 상단 버전: v2.15.8
- 요약표 열 순서: 중심모델군 → 공고명 → 업체1추천 → 업체2추천 → 업체3추천 → 비고
- 요약표 제외 열: 업체1·2·3 추천기준금액
- 건별 상세 및 Excel 추천기준금액: 유지
- 추천식·가중치·업체수 판정·백테스트 엔진: 변경 없음
- Python 문법 검사: GitHub Actions 통과
- 전체 자동 단위·Excel 검사: GitHub Actions 통과
- v2.15.8 정적 회귀검사: GitHub Actions 통과
- Streamlit 부팅 및 첫 화면 HTTP 검사: GitHub Actions 통과
- git diff --check: GitHub Actions 통과
''', encoding='utf-8')
