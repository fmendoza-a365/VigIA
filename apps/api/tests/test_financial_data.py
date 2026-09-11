from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.financial_data import (
    coerce_external_decimal_strings,
    financial_scope_options,
    FinancialDataError,
    FinancialDataService,
    normalize_financial_scope,
    query_cached_details,
    query_comprehensive_detail,
    query_remote_data_page,
    query_sensitive_data_page,
    round_financial_values,
    restrict_snapshot_to_scope,
    transform_seguimiento_dashboard,
    unwrap_payload,
    validate_financial_snapshot,
)


def valid_payload() -> dict:
    return {
        "generated_at": "2026-08-05T12:00:00-05:00",
        "source": "seguimiento_financiero",
        "campaigns": [
            {
                "id": "cliente-a",
                "name": "Cliente A",
                "aliases": ["Cuenta A"],
                "owner": "Operaciones",
                "metrics": {
                    "gross_margin": {
                        "label": "Margen bruto",
                        "actual": "21.4",
                        "target": 20,
                        "direction": "min",
                        "unit": "%",
                        "tolerance": 0.25,
                    }
                },
            }
        ],
    }


class FinancialDataServiceTest(unittest.TestCase):
    def test_meeting_scope_uses_only_current_sifo_accounts_and_campaigns(self) -> None:
        payload = valid_payload()
        payload["period"] = "2026-08"
        payload["details"] = {
            "campaigns": {
                "data": [
                    {"campaign_bi": "A-ACTUAL", "account": "Cliente A", "latest_period": "2026-08", "operation_type": "INBOUND"},
                    {"campaign_bi": "A-ANTERIOR", "account": "Cliente A", "latest_period": "2026-07", "operation_type": "OUTBOUND"},
                ]
            }
        }
        snapshot = validate_financial_snapshot(payload)

        options = financial_scope_options(snapshot)
        scope = normalize_financial_scope(snapshot, accounts=["Cliente A"], campaigns=["A-ACTUAL"])

        self.assertEqual(options["accounts"], ["Cliente A"])
        self.assertEqual([item["value"] for item in options["campaigns"]], ["A-ACTUAL"])
        # Selecting every compatible value is normalized to the compact all-state.
        self.assertEqual(scope["accounts"], [])
        self.assertEqual(scope["campaigns"], [])

    def test_account_scope_removes_unselected_accounts_from_fact_check_snapshot(self) -> None:
        payload = valid_payload()
        second = json.loads(json.dumps(payload["campaigns"][0]))
        second.update({"id": "cliente-b", "name": "Cliente B", "aliases": []})
        second["metrics"]["gross_margin"]["actual"] = 9
        payload["campaigns"].append(second)
        snapshot = validate_financial_snapshot(payload)
        scope = normalize_financial_scope(snapshot, accounts=["Cliente A"], campaigns=[])

        scoped = restrict_snapshot_to_scope(snapshot, scope)

        self.assertEqual([item["name"] for item in scoped["campaigns"]], ["Cliente A"])
        self.assertEqual(scoped["financial_scope"]["label"], "Cliente A")
        self.assertEqual(scoped["details"], {})

    def test_meeting_scope_rejects_unknown_accounts(self) -> None:
        snapshot = validate_financial_snapshot(valid_payload())

        with self.assertRaisesRegex(FinancialDataError, "fuera del alcance"):
            normalize_financial_scope(snapshot, accounts=["Cuenta inexistente"], campaigns=[])

    def test_validates_and_normalizes_the_canonical_contract(self) -> None:
        snapshot = validate_financial_snapshot(valid_payload())

        metric = snapshot["campaigns"][0]["metrics"]["gross_margin"]
        self.assertEqual(metric["actual"], 21.4)
        self.assertEqual(metric["target"], 20.0)
        self.assertEqual(metric["tolerance"], 0.25)

    def test_rounds_every_nested_decimal_to_two_places(self) -> None:
        rounded = round_financial_values({
            "summary": {"margin": 12.34567},
            "rows": [{"amount": 987.6543, "count": 7}],
        })

        self.assertEqual(rounded["summary"]["margin"], 12.35)
        self.assertEqual(rounded["rows"][0]["amount"], 987.65)
        self.assertEqual(rounded["rows"][0]["count"], 7)

    def test_coerces_postgres_decimals_before_rounding(self) -> None:
        value = coerce_external_decimal_strings({"margin": "12.34567", "period": "2026-07"})

        self.assertEqual(round_financial_values(value)["margin"], 12.35)
        self.assertEqual(value["period"], "2026-07")

    def test_rejects_an_invalid_direction(self) -> None:
        payload = valid_payload()
        payload["campaigns"][0]["metrics"]["gross_margin"]["direction"] = "higher"

        with self.assertRaises(FinancialDataError):
            validate_financial_snapshot(payload)

    def test_json_provider_keeps_last_valid_snapshot_after_failed_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_payload()), encoding="utf-8")
            service = FinancialDataService(
                project_root=root,
                default_snapshot_path=snapshot_path,
            )
            env = {
                "VIGIA_DATA_PROVIDER": "json",
                "VIGIA_BI_SNAPSHOT_PATH": str(snapshot_path),
                "VIGIA_DATA_REFRESH_SECONDS": "0",
                "VIGIA_DATA_MAX_AGE_SECONDS": "0",
            }
            with patch.dict(os.environ, env, clear=False):
                first = service.get_snapshot(force_refresh=True)
                snapshot_path.write_text("{invalid", encoding="utf-8")
                fallback = service.get_snapshot(force_refresh=True)

            self.assertEqual(first, fallback)
            self.assertIn("invalid JSON", service.status()["last_error"])

    def test_marks_an_old_http_snapshot_as_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_path = root / "snapshot.json"
            payload = valid_payload()
            payload["generated_at"] = "2020-01-01T00:00:00Z"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            service = FinancialDataService(
                project_root=root,
                default_snapshot_path=snapshot_path,
            )
            env = {
                "VIGIA_DATA_PROVIDER": "json",
                "VIGIA_BI_SNAPSHOT_PATH": str(snapshot_path),
                "VIGIA_DATA_MAX_AGE_SECONDS": "60",
            }
            with patch.dict(os.environ, env, clear=False):
                service.get_snapshot(force_refresh=True)
                status = service.status()

            self.assertTrue(status["stale"])
            self.assertFalse(status["trusted"])

    def test_unwraps_common_api_envelopes(self) -> None:
        payload = valid_payload()
        self.assertEqual(unwrap_payload({"data": payload}), payload)
        self.assertEqual(unwrap_payload({"snapshot": payload}), payload)

    def test_http_provider_uses_read_only_auth_and_unwraps_data(self) -> None:
        service = FinancialDataService()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"data": valid_payload()}
        ).encode("utf-8")
        captured_request = None

        def fake_urlopen(request, timeout):
            nonlocal captured_request
            captured_request = request
            self.assertEqual(timeout, 3.0)
            return response

        env = {
            "VIGIA_DATA_PROVIDER": "http",
            "SEGUIMIENTO_FINANCIERO_URL": "https://finanzas.example.test/api/v1/vigia",
            "SEGUIMIENTO_FINANCIERO_TOKEN": "read-only-token",
            "VIGIA_DATA_TIMEOUT_SECONDS": "3",
            "VIGIA_DATA_MAX_AGE_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "vigia.financial_data.urlopen", side_effect=fake_urlopen
        ):
            snapshot = service.get_snapshot(force_refresh=True)

        self.assertEqual(snapshot["source"], "seguimiento_financiero")
        self.assertEqual(captured_request.get_header("Authorization"), "Bearer read-only-token")

    def test_transforms_real_seguimiento_dashboard_without_demo_metrics(self) -> None:
        dashboard = {
            "summary": {"total_net_income": 1_000_000},
            "by_account": [
                {
                    "account": "CLARO IN",
                    "gross_income": 1_100_000,
                    "net_income": 1_000_000,
                    "payroll": 790_000,
                    "structure_payroll": 80_000,
                    "penalties": 10_000,
                    "billed": 950_000,
                    "agents": 125,
                    "supervisors": 8,
                    "campaigns": 3,
                }
            ],
        }
        budget = {
            "data": [
                {
                    "month": "2026-07-01T00:00:00.000Z",
                    "account": "CLARO IN",
                    "budget": 1_050_000,
                }
            ]
        }

        snapshot = transform_seguimiento_dashboard(
            dashboard,
            period="2026-07",
            budget_payload=budget,
            campaign_payload={
                "page": 1,
                "limit": 100,
                "total": 1,
                "data": [{
                    "campaign_bi": "CLARO-IN-01",
                    "account": "CLARO IN",
                    "campaign": "Renovaciones",
                    "status": "ACTIVA",
                    "latest_income": 1000.129,
                    "latest_agents": 12,
                }],
            },
            billing_payload={
                "period": {"year": 2026, "month": 7},
                "data": [{
                    "account": "CLARO IN",
                    "campaign": "Renovaciones",
                    "total": 1199.999,
                }],
            },
            agents_payload={"period": {"year": 2026, "month": 7}, "total": 125},
            fetched_at="2026-08-05T22:00:00+00:00",
        )
        validated = validate_financial_snapshot(snapshot)
        campaign = validated["campaigns"][0]

        self.assertEqual(snapshot["period"], "2026-07")
        self.assertEqual(campaign["name"], "CLARO IN")
        self.assertEqual(campaign["metrics"]["gross_margin"]["actual"], 21.0)
        self.assertEqual(campaign["metrics"]["gross_income"]["actual"], 1_100_000.0)
        self.assertEqual(campaign["metrics"]["structure_payroll"]["actual"], 80_000.0)
        self.assertEqual(campaign["metrics"]["supervisors"]["actual"], 8.0)
        self.assertEqual(campaign["metrics"]["campaign_count"]["actual"], 3.0)
        self.assertEqual(campaign["metrics"]["facturacion"]["actual"], 950_000.0)
        self.assertAlmostEqual(
            campaign["metrics"]["budget_compliance"]["actual"],
            95.24,
        )
        self.assertNotIn("sla", campaign["metrics"])
        self.assertNotIn("ebitda", campaign["metrics"])
        self.assertEqual(snapshot["coverage"]["campaigns"], 1)
        self.assertEqual(snapshot["coverage"]["agents"], 125)
        self.assertEqual(snapshot["details"]["billing"]["data"][0]["total"], 1200.0)
        self.assertEqual(
            snapshot["details"]["campaigns"]["data"][0]["latest_income"],
            1000.13,
        )

    def test_cached_budget_detail_filters_and_summarizes_real_rows(self) -> None:
        snapshot = valid_payload()
        snapshot["period"] = "2026-07"
        snapshot["details"] = {
            "budget": {
                "year": 2026,
                "data": [
                    {"account": "Cuenta A", "campaign": "Uno", "budget": 100.129, "actual": 90.128},
                    {"account": "Cuenta B", "campaign": "Dos", "budget": 50.0, "actual": 55.0},
                ],
            }
        }
        result = query_cached_details(
            snapshot,
            section="budget",
            account="Cuenta A",
            campaign="",
            year=2026,
            month=7,
            page=1,
            limit=20,
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["summary"]["budget"], 100.13)
        self.assertEqual(result["summary"]["actual"], 90.13)

    def test_executive_detail_is_compact_rounded_and_has_no_personal_fields(self) -> None:
        snapshot = valid_payload()
        snapshot["details"] = {
            "executive": {
                "source": "seguimiento_financiero/executive-snapshot",
                "coverage": {"min_period": "2023-11", "max_period": "2026-07"},
                "snapshot": {
                    "calculatedSummary": {"gross_margin_pct": 21.2345},
                    "financialMonthly": [{"period": "2026-07", "income": 10.129}],
                    "performanceTable": {
                        "level": "account",
                        "rows": [{"account_name": "Cuenta A", "gross_margin_pct": 21.2345}],
                    },
                    "supervisorAlerts": {
                        "rows": [{"account": "Cuenta A", "supervisor_name": "Persona", "ratio": 18.789}],
                    },
                    "campaignBreakdown": [
                        {
                            "account_name": "Cuenta A",
                            "campaign_bi": "A-UNO",
                            "unified_campaign": "Uno",
                            "income": 10.129,
                        }
                    ],
                    "campaignsByAccount": [
                        {
                            "account_name": "Cuenta A",
                            "operation_type": "INBOUND",
                            "campaigns": [
                                {"campaign_bi": "A-UNO", "campaign_name": "Uno", "income": 10.129}
                            ],
                        }
                    ],
                },
            }
        }

        result = query_comprehensive_detail(
            snapshot, section="executive", account="Cuenta A", campaign="", page=1, limit=20
        )

        self.assertEqual(result["summary"]["gross_margin_pct"], 21.23)
        self.assertEqual(result["performance"]["results"][0]["gross_margin_pct"], 21.23)
        self.assertEqual(result["campaign_breakdown"]["results"][0]["campaign_bi"], "A-UNO")
        self.assertEqual(result["campaigns"]["results"][0]["operation_type"], "INBOUND")
        self.assertNotIn("supervisor_name", str(result))

    def test_reads_every_campaign_page_when_server_clamps_the_limit(self) -> None:
        service = FinancialDataService()
        first_rows = [{"campaign_bi": f"C-{index}"} for index in range(100)]
        second_rows = [{"campaign_bi": f"C-{index}"} for index in range(100, 150)]

        with patch.object(
            service,
            "_request_json",
            side_effect=[
                {"page": 1, "limit": 100, "total": 150, "data": first_rows},
                {"page": 2, "limit": 100, "total": 150, "data": second_rows},
            ],
        ) as request_json:
            result = service._request_all_pages(
                "https://sifo.example.test/api/external/campaigns",
                {"X-API-Key": "redacted"},
                limit=200,
            )

        self.assertEqual(len(result["data"]), 150)
        self.assertEqual(result["limit"], 100)
        self.assertEqual(request_json.call_count, 2)
        self.assertIn("page=2", request_json.call_args_list[1].args[0])

    def test_workforce_uses_group_total_and_supports_account_filter(self) -> None:
        service = FinancialDataService()
        snapshot = {
            "period": "2026-08",
            "details": {"campaigns": {"data": []}},
        }
        payload = {
            "period": {"year": 2026, "month": 8},
            "page": 1,
            "limit": 50,
            "total": 3663,
            "total_groups": 373,
            "summary": {"people": 3663, "total_cost": 123.456},
            "data": [
                {
                    "account": "Cuenta A",
                    "campaign": "A-UNO",
                    "role": "AGENTES",
                    "status": "ACTIVO",
                    "people": 25,
                }
            ],
            "privacy": "Sin identidad personal.",
        }
        with patch.object(service, "get_snapshot", return_value=snapshot), patch.object(
            service,
            "_external_api_connection",
            return_value=("https://sifo.example.test/api/external", {"X-API-Key": "redacted"}),
        ), patch.object(service, "_request_json", return_value=payload) as request_json:
            result = service.query_official_details(section="workforce", account="Cuenta A")

        self.assertEqual(result["section"], "agents")
        self.assertEqual(result["total"], 373)
        self.assertEqual(result["people_total"], 3663)
        self.assertEqual(result["results"][0]["people"], 25)
        self.assertIn("/workforce?", request_json.call_args.args[0])
        self.assertIn("account=Cuenta+A", request_json.call_args.args[0])

    def test_normalizes_unpaged_node_detail_before_local_pagination(self) -> None:
        payload = {
            "from": "2026-01-01",
            "to": "2026-08-31",
            "total": 3,
            "data": [
                {"account": "A", "period_start": "2026-01-01", "metric_value": 1.111},
                {"account": "A", "period_start": "2026-02-01", "metric_value": 2.222},
                {"account": "A", "period_start": "2026-03-01", "metric_value": 3.333},
            ],
        }

        result = query_remote_data_page(
            payload,
            section="operational_metrics",
            account="A",
            campaign="",
            page=2,
            limit=2,
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["metric_value"], 3.33)

    def test_sensitive_payroll_page_keeps_authorized_personal_and_salary_fields(self) -> None:
        payload = {
            "period": {"year": 2026, "month": 8},
            "page": 1,
            "limit": 20,
            "total": 1,
            "sensitive": True,
            "data": [{
                "dni": "00123456",
                "employee_code": "A-17",
                "full_name": "Persona Autorizada",
                "remuneration": "2100.129",
                "vacation_amount": "125.555",
                "total_employer_cost": "3150.987",
            }],
        }

        result = query_sensitive_data_page(
            payload, section="payroll_detail", page=1, limit=20
        )

        self.assertEqual(result["results"][0]["dni"], "00123456")
        self.assertEqual(result["results"][0]["full_name"], "Persona Autorizada")
        self.assertEqual(result["results"][0]["remuneration"], 2100.13)
        self.assertEqual(result["results"][0]["vacation_amount"], 125.56)
        self.assertEqual(result["results"][0]["total_employer_cost"], 3150.99)

    def test_queries_permission_gated_payroll_detail_with_all_filters(self) -> None:
        service = FinancialDataService()
        snapshot = {"period": "2026-08", "campaigns": [], "details": {"campaigns": {"data": []}}}
        payload = {
            "period": {"year": 2026, "month": 8},
            "page": 2,
            "limit": 10,
            "total": 1,
            "data": [{"dni": "00123456", "full_name": "Persona Autorizada"}],
        }
        with patch.object(service, "get_snapshot", return_value=snapshot), patch.object(
            service,
            "_external_api_connection",
            return_value=("https://sifo.example.test/api/external", {"X-API-Key": "redacted"}),
        ), patch.object(service, "_request_json", return_value=payload) as request_json:
            result = service.query_official_details(
                section="payroll_detail",
                account="Cuenta A",
                campaign="Campaña A",
                year=2026,
                month=8,
                search="Persona Autorizada",
                page=2,
                limit=10,
            )

        requested_url = request_json.call_args.args[0]
        self.assertIn("/agents-sensitive?", requested_url)
        self.assertIn("account=Cuenta+A", requested_url)
        self.assertIn("campaign=Campa%C3%B1a+A", requested_url)
        self.assertIn("search=Persona+Autorizada", requested_url)
        self.assertEqual(result["results"][0]["dni"], "00123456")

    def test_sensitive_payroll_can_use_an_explicit_private_sifo_url(self) -> None:
        service = FinancialDataService()
        env = {
            "SEGUIMIENTO_FINANCIERO_URL": "https://sifo.example.test/api/external",
            "SEGUIMIENTO_FINANCIERO_SENSITIVE_URL": "http://app:3000/api/external",
            "SEGUIMIENTO_FINANCIERO_API_KEY": "redacted",
            "VIGIA_ALLOW_INSECURE_SENSITIVE_DATA_URL": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            base_url, headers = service._sensitive_api_connection()

        self.assertEqual(base_url, "http://app:3000/api/external")
        self.assertEqual(headers["X-API-Key"], "redacted")

    def test_sensitive_private_http_url_requires_explicit_opt_in(self) -> None:
        service = FinancialDataService()
        env = {
            "SEGUIMIENTO_FINANCIERO_URL": "https://sifo.example.test/api/external",
            "SEGUIMIENTO_FINANCIERO_SENSITIVE_URL": "http://app:3000/api/external",
            "SEGUIMIENTO_FINANCIERO_API_KEY": "redacted",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(FinancialDataError):
                service._sensitive_api_connection()


if __name__ == "__main__":
    unittest.main()
