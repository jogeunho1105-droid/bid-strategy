from pathlib import Path

ROOT = Path('.')
APP_PATH = ROOT / '입찰 앱.py'
README_PATH = ROOT / 'README.md'
TEST_OLD_PATH = ROOT / 'tests' / 'test_app_v2_15_7.py'
TEST_159_PATH = ROOT / 'tests' / 'test_app_v2_15_9.py'
TEST_1510_PATH = ROOT / 'tests' / 'test_app_v2_15_10.py'
VERIFY_PATH = ROOT / 'docs' / 'change_requests' / 'v2.15.10-implementation-verification.md'


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
    '# ║  투찰전략 분석 시스템 v2.15.9                                   ║',
    '# ║  투찰전략 분석 시스템 v2.15.10                                  ║',
    'header version',
)
app = replace_version(
    app,
    'MODEL_VERSION = "v2.15.9"',
    'MODEL_VERSION = "v2.15.10"',
    'model version',
)
app = replace_version(
    app,
    '<h2>📊 투찰전략 분석 시스템 v2.15.9</h2>',
    '<h2>📊 투찰전략 분석 시스템 v2.15.10</h2>',
    'screen version',
)

summary_marker = '    # ── 요약 테이블: 최종 추천값과 근거만 표시'
detail_marker = '    # ── 건별 상세'
summary_start = app.index(summary_marker)
summary_end = app.index(detail_marker, summary_start)
summary = app[summary_start:summary_end]

old_rows = '''        rows.append({
            "중심모델군":model_family_info(b)[1],
            "공고명":b["name"][:40]+"…" if len(b["name"])>40 else b["name"],
            "업체1추천":display_vals[0],
            "업체2추천":display_vals[1],
            "업체3추천":display_vals[2],
            "비고":"\\n".join(notes)
        })
'''
new_rows = '''        rows.append({
            "중심모델군":model_family_info(b)[1],
            "공고명":b["name"][:40]+"…" if len(b["name"])>40 else b["name"],
            "낙찰하한율":format_lower_limit_rate(b.get("lower_limit_rate")),
            "업체1추천":display_vals[0],
            "업체2추천":display_vals[1],
            "업체3추천":display_vals[2],
            "비고":"\\n".join(notes)
        })
'''
if new_rows not in summary:
    if summary.count(old_rows) != 1:
        raise RuntimeError('summary row block not found exactly once')
    summary = summary.replace(old_rows, new_rows, 1)

old_columns = '''    summary_columns=[
        "중심모델군",
        "공고명",
        "업체1추천",
        "업체2추천",
        "업체3추천",
        "비고",
    ]
'''
new_columns = '''    summary_columns=[
        "중심모델군",
        "공고명",
        "낙찰하한율",
        "업체1추천",
        "업체2추천",
        "업체3추천",
        "비고",
    ]
'''
if new_columns not in summary:
    if summary.count(old_columns) != 1:
        raise RuntimeError('summary columns block not found exactly once')
    summary = summary.replace(old_columns, new_columns, 1)

app = app[:summary_start] + summary + app[summary_end:]
APP_PATH.write_text(app, encoding='utf-8')

readme = README_PATH.read_text(encoding='utf-8')
readme = replace_version(
    readme,
    '# 투찰전략 분석 시스템 v2.15.9',
    '# 투찰전략 분석 시스템 v2.15.10',
    'README title',
)
readme = replace_version(
    readme,
    'v2.15.9는 사용자 승인에 따라 낙찰하한율과 업체별 추천을 담은 투찰전략 요약 Excel 다운로드를 추가한 배포 버전입니다.',
    'v2.15.10은 사용자 승인에 따라 기본 요약화면에도 입찰서류함 원본 낙찰하한율을 표시하는 배포 버전입니다.',
    'README release sentence',
)
readme = replace_version(
    readme,
    'v2.15.9는 실행파일 하나만 교체해서는 동작하지 않습니다.',
    'v2.15.10은 실행파일 하나만 교체해서는 동작하지 않습니다.',
    'README deployment version',
)
section = '''## v2.15.10 기본화면 낙찰하한율 표시

입찰서류함 업로드 후 기본 요약표는 다음 순서로 표시합니다.

```text
중심모델군 | 공고명 | 낙찰하한율 | 업체1추천 | 업체2추천 | 업체3추천 | 비고
```

낙찰하한율은 업로드한 입찰서류함 원본값만 사용하고 앱에서 재계산하거나 추정하지 않습니다. 누락·비정상 값은 `미확인`으로 표시합니다. 기존 상세 Excel과 v2.15.9 투찰전략 요약 Excel은 그대로 유지합니다.

'''
if section not in readme:
    anchor = '## 중심모델군과 세부업종\n'
    if anchor not in readme:
        raise RuntimeError('README insertion anchor not found')
    readme = readme.replace(anchor, section + anchor, 1)
README_PATH.write_text(readme, encoding='utf-8')

old_test = TEST_OLD_PATH.read_text(encoding='utf-8')
old_test = old_test.replace(
    'self.assertEqual(values["모델버전"],"v2.15.9")',
    'self.assertEqual(values["모델버전"],"v2.15.10")',
    1,
)
TEST_OLD_PATH.write_text(old_test, encoding='utf-8')

test_159 = TEST_159_PATH.read_text(encoding='utf-8')
test_159 = test_159.replace(
    'self.assertIn(\'MODEL_VERSION = "v2.15.9"\',app_text)',
    'self.assertIn(\'MODEL_VERSION = "v2.15.10"\',app_text)',
    1,
)
TEST_159_PATH.write_text(test_159, encoding='utf-8')

TEST_1510_PATH.write_text('''"""Regression checks for the v2.15.10 default-screen lower-limit column."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "입찰 앱.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = APP.split("# ── 요약 테이블: 최종 추천값과 근거만 표시", 1)[1].split("# ── 건별 상세", 1)[0]


class DefaultScreenLowerLimitV21510Tests(unittest.TestCase):
    def test_version_updated(self):
        self.assertIn('MODEL_VERSION = "v2.15.10"', APP)
        self.assertIn("투찰전략 분석 시스템 v2.15.10", APP)
        self.assertTrue(README.startswith("# 투찰전략 분석 시스템 v2.15.10"))

    def test_summary_includes_lower_limit_after_name(self):
        expected = ["중심모델군", "공고명", "낙찰하한율", "업체1추천", "업체2추천", "업체3추천", "비고"]
        columns = SUMMARY.split("summary_columns=[", 1)[1].split("]", 1)[0]
        positions = [columns.index(f'"{name}"') for name in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('"낙찰하한율":format_lower_limit_rate(b.get("lower_limit_rate"))', SUMMARY)

    def test_original_value_formatter_and_download_remain(self):
        self.assertIn("def format_lower_limit_rate", APP)
        self.assertIn("📥 투찰전략 요약 다운로드", APP)
        self.assertIn("def make_strategy_summary_excel", APP)
        self.assertIn("누락·비정상 값은 `미확인`", README)


if __name__ == "__main__":
    unittest.main()
''', encoding='utf-8')

VERIFY_PATH.write_text('''# v2.15.10 구현·검증 결과

- 모델 버전 및 화면 상단 버전: v2.15.10
- 기본 요약표 낙찰하한율 열: 반영
- 열 순서: 중심모델군 → 공고명 → 낙찰하한율 → 업체1추천 → 업체2추천 → 업체3추천 → 비고
- 낙찰하한율 출처: 업로드 입찰서류함 원본
- 누락·비정상값: 미확인
- 기존 상세 Excel 및 투찰전략 요약 Excel: 유지
- 추천식·가중치·업체수 판정·백테스트 엔진: 변경 없음
- 검증 상태: GitHub Actions 실행 중
''', encoding='utf-8')
