from __future__ import annotations

import ast
import io
import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook


APP = Path(__file__).resolve().parents[1] / "입찰 앱.py"


def load_app_namespace() -> dict:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    namespace = {
        "pd": pd,
        "np": np,
        "re": re,
        "io": io,
        "os": os,
        "json": json,
        "datetime": datetime,
    }
    bodies = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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


class V2154Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_app_namespace()

    def history(self, org="일반기관", name="일반 설계용역", industry="설계"):
        dates = pd.date_range("2026-06-01", periods=12, freq="7D")
        values = np.linspace(0.10, 0.21, len(dates))
        return pd.DataFrame(
            {
                "개찰일": dates,
                "공고번호": [f"N{i:02d}" for i in range(len(dates))],
                "번호": range(len(dates)),
                "발주기관": org,
                "공고명": name,
                "업종": industry,
                "기초금액": 100_000_000,
                "업체수": 20,
                "지역": "부산",
                "예가/기초(0%)": values,
            }
        )

    def test_stable_sort_and_exact_duplicate_removal(self):
        frame = pd.DataFrame(
            {
                "개찰일": ["2026-01-01"] * 4,
                "공고번호": ["B", "A", "C", "A"],
                "번호": [2, 1, 2, 1],
                "예가/기초(0%)": [0.2, 0.1, 0.3, 0.1],
            }
        )
        ordered = self.app["history_sorted"](frame)
        self.assertEqual(ordered["공고번호"].tolist(), ["A", "B", "C"])
        self.assertEqual(len(ordered), 3)

    def test_general_overlay_uses_v2154_weights(self):
        frame = self.app["enrich_history"](self.history())
        bid = {
            "org": "일반기관",
            "name": "일반 설계용역",
            "industry": "설계",
            "base": 100_000_000,
            "deadline": datetime(2026, 8, 31),
        }
        _, note = self.app["recent_volatility_overlay"](bid, frame, 0.0)
        self.assertIn("v2.15.4 보수화", note)
        self.assertIn("최근가중 0.10", note)
        self.assertIn("×0.04", note)

    def test_electric_overlay_keeps_v2153_weights(self):
        frame = self.app["enrich_history"](
            self.history(name="배전선로 전기공사", industry="전기")
        )
        bid = {
            "org": "일반기관",
            "name": "배전선로 전기공사",
            "industry": "전기",
            "base": 100_000_000,
            "deadline": datetime(2026, 8, 31),
        }
        _, note = self.app["recent_volatility_overlay"](bid, frame, 0.0)
        self.assertIn("v2.15.3 유지", note)
        self.assertIn("최근가중 0.18", note)
        self.assertIn("×0.08", note)

    def test_kepco_local_single_keeps_v2153_weights(self):
        org = "한국전력공사 경북본부"
        frame = self.app["enrich_history"](
            self.history(org=org, name="배전공사 감리용역", industry="전력감리")
        )
        bid = {
            "org": org,
            "name": "배전공사 감리용역",
            "industry": "전력감리",
            "base": 100_000_000,
            "deadline": datetime(2026, 8, 31),
        }
        _, note = self.app["recent_volatility_overlay"](bid, frame, 0.0)
        self.assertIn("v2.15.3 유지", note)
        self.assertIn("최근가중 0.18", note)

    def test_quality_summary_and_reference_amount(self):
        frame = pd.DataFrame(
            [
                {"발주기관": "A", "공고명": "전기공사", "업종": "전기", "지역": "", "예가/기초(0%)": 0.1},
                {"발주기관": "A", "공고명": "전기공사", "업종": "전기", "지역": "", "예가/기초(0%)": 0.1},
                {"발주기관": "B", "공고명": "설계용역", "업종": "설계", "지역": None, "예가/기초(0%)": None},
            ]
        )
        quality = self.app["history_quality_summary"](frame)
        self.assertEqual(quality["exact_duplicates"], 1)
        self.assertEqual(quality["invalid_target_rows"], 1)
        self.assertEqual(quality["electric_region_missing_rows"], 1)
        self.assertEqual(
            self.app["recommendation_reference_amount"](100_000_000, 0.1),
            100_100_000,
        )

    def test_excel_contains_v2154_audit_columns(self):
        results = [
            {
                "bid": {
                    "no": 1,
                    "bid_no": "N-1",
                    "name": "일반 설계용역",
                    "org": "일반기관",
                    "industry": "설계",
                    "region": "부산",
                    "base": 100_000_000,
                    "deadline": "2026-08-31",
                },
                "scope_info": {"applicable": False, "scope": "기존분석", "company_count": 3},
                "recommendations": [
                    {
                        "rate": 0.1,
                        "model_label": "중심모델",
                        "basis_n": 12,
                        "basis": "최근90일 오버레이(v2.15.4 보수화, 기준표본 n=12, 최근 n=10, 변동성 0.2000, 최근가중 0.10)",
                    }
                ],
            }
        ]
        quality = {
            "exact_duplicates": 0,
            "invalid_target_rows": 0,
            "region_missing_rows": 0,
            "region_missing_rate": 0.0,
            "electric_region_missing_rows": 0,
            "electric_rows": 0,
            "electric_region_missing_rate": 0.0,
        }
        output = self.app["make_excel_simple"](results, quality)
        wb = load_workbook(io.BytesIO(output.getvalue()), data_only=False)
        ws = wb["업체별 추천"]
        headers = [cell.value for cell in ws[2]]
        self.assertIn("업체1 추천기준금액(원)", headers)
        self.assertIn("업체1 최근90일가중치", headers)
        self.assertIn("데이터품질경고", headers)
        self.assertEqual(ws.cell(3, 11).value, 100_100_000)
        self.assertEqual(ws.cell(3, len(headers)).value, "v2.15.4")


if __name__ == "__main__":
    unittest.main()
