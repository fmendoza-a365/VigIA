from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.official_questions import (
    campaign_catalog_detail,
    coverage_detail,
    detect_official_section,
    extract_official_filters,
    format_official_answer,
)


SNAPSHOT = {
    "generated_at": "2026-09-01T12:00:00Z",
    "source": "SIFO",
    "period": "2026-08",
    "campaigns": [
        {"id": "claro", "name": "CLARO PERÚ", "aliases": ["Claro"], "metrics": {}},
        {"id": "entel", "name": "ENTEL CH IN", "aliases": ["Entel"], "metrics": {}},
    ],
    "coverage": {
        "accounts": 2,
        "catalog_campaigns": 4,
        "agents": 120,
        "historical_periods": 18,
    },
    "details": {
        "campaigns": {
            "data": [
                {
                    "campaign_bi": "CLARO-VENTAS",
                    "campaign": "Ventas",
                    "account": "CLARO PERÚ",
                    "status": "ACTIVA",
                    "operation_type": "OUTBOUND",
                    "latest_period": "2026-08",
                },
                {
                    "campaign_bi": "CLARO-ATC",
                    "campaign": "Atención",
                    "account": "CLARO PERÚ",
                    "status": "ACTIVA",
                    "operation_type": "INBOUND",
                    "latest_period": "2026-08",
                },
                {
                    "campaign_bi": "ENTEL-VENTAS",
                    "campaign": "Ventas Entel",
                    "account": "ENTEL CH IN",
                    "status": "ACTIVA",
                    "operation_type": "OUTBOUND",
                    "latest_period": "2026-08",
                },
                {
                    "campaign_bi": "CLARO-ANTERIOR",
                    "campaign": "Anterior",
                    "account": "CLARO PERÚ",
                    "status": "INACTIVA",
                    "operation_type": "INBOUND",
                    "latest_period": "2026-07",
                },
            ]
        },
        "history": [],
        "agents": {},
    },
}


class OfficialQuestionsTest(unittest.TestCase):
    def test_recognizes_campaign_and_all_supported_detail_intents(self) -> None:
        cases = {
            "¿Cuáles son las campañas?": "campaigns",
            "¿Qué información tienes de SIFO?": "coverage",
            "Dame el histórico": "history",
            "¿Cuánta dotación tenemos?": "agents",
            "Dame el detalle de planilla por trabajador": "payroll_detail",
            "Dame la planilla de Ana Pérez": "payroll_detail",
            "Revisa la facturación": "billing",
            "¿Cómo va el presupuesto?": "budget",
            "Dame los KPI": "kpis",
            "Necesito un plan de acción": "executive",
            "Ratio de supervisión": "ratios",
            "¿Cuál es el SLA?": "operations",
            "Métricas operativas": "operational_metrics",
            "Asistencia SIOP": "attendance",
            "Calidad de datos": "data_quality",
            "IFC de agosto": "ifc",
            "IFC anual": "ifc_annual",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(detect_official_section(question), expected)

    def test_campaign_question_returns_current_catalog_instead_of_no_information(self) -> None:
        detail = campaign_catalog_detail(SNAPSHOT)
        answer = format_official_answer("campaigns", detail)

        self.assertEqual(detail["total_current"], 3)
        self.assertEqual(detail["total_catalog"], 4)
        self.assertNotIn("CLARO-ANTERIOR", str(detail))
        self.assertIn("3 campañas con datos", answer)
        self.assertIn("CLARO PERÚ: 2", answer)
        self.assertIn("ENTEL CH IN: 1", answer)

    def test_campaign_question_can_filter_by_account_and_list_every_match(self) -> None:
        filters = extract_official_filters("¿Cuáles son las campañas de Claro Perú?", SNAPSHOT)
        detail = campaign_catalog_detail(SNAPSHOT, account=filters["account"])
        answer = format_official_answer("campaigns", detail)

        self.assertEqual(filters["account"], "CLARO PERÚ")
        self.assertEqual(detail["total_current"], 2)
        self.assertIn("CLARO-ATC", answer)
        self.assertIn("CLARO-VENTAS", answer)
        self.assertNotIn("ENTEL-VENTAS", answer)

    def test_canonical_snapshot_without_detail_still_lists_campaigns(self) -> None:
        snapshot = {
            "source": "snapshot canónico",
            "period": "2026-08",
            "campaigns": [
                {"id": "bcp", "name": "BCP", "metrics": {"margin": {}}},
                {"id": "claro", "name": "CLARO", "metrics": {"margin": {}}},
            ],
        }

        detail = campaign_catalog_detail(snapshot)
        answer = format_official_answer("campaigns", detail)

        self.assertEqual(detail["total_current"], 2)
        self.assertIn("BCP", answer)
        self.assertIn("CLARO", answer)

    def test_coverage_answer_reports_real_sifo_surface(self) -> None:
        answer = format_official_answer("coverage", coverage_detail(SNAPSHOT))

        self.assertIn("2 cuentas", answer)
        self.assertIn("4 campañas", answer)
        self.assertIn("120 personas", answer)
        self.assertIn("18 períodos", answer)
        self.assertIn("planilla individual autorizada", answer)

    def test_extracts_campaign_and_period_filters(self) -> None:
        filters = extract_official_filters(
            "KPI de CLARO-VENTAS para agosto de 2026",
            SNAPSHOT,
        )

        self.assertEqual(filters["campaign"], "CLARO-VENTAS")
        self.assertEqual(filters["account"], "")
        self.assertEqual(filters["year"], 2026)
        self.assertEqual(filters["month"], 8)

    def test_extracts_sensitive_payroll_person_and_page_filters(self) -> None:
        filters = extract_official_filters(
            "Dame la planilla de Ana Pérez de agosto de 2026, página 2, primeros 10",
            SNAPSHOT,
        )

        self.assertEqual(filters["search"], "ana perez")
        self.assertEqual(filters["year"], 2026)
        self.assertEqual(filters["month"], 8)
        self.assertEqual(filters["page"], 2)
        self.assertEqual(filters["limit"], 10)

    def test_formats_one_authorized_payroll_record(self) -> None:
        answer = format_official_answer("payroll_detail", {
            "ok": True,
            "section": "payroll_detail",
            "period": {"year": 2026, "month": 8},
            "page": 1,
            "total": 1,
            "results": [{
                "dni": "00123456",
                "employee_code": "A-17",
                "full_name": "Ana Pérez",
                "job_title": "Asesora",
                "campaign_bi": "CLARO-VENTAS",
                "remuneration": 2100,
                "total_salary": 2250,
                "total_employer_cost": 3150,
            }],
        })

        self.assertIn("Ana Pérez", answer)
        self.assertIn("00123456", answer)
        self.assertIn("remuneración S/ 2 100", answer)
        self.assertIn("costo total empleador S/ 3 150", answer)


if __name__ == "__main__":
    unittest.main()
