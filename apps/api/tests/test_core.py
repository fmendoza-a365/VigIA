from __future__ import annotations

import unittest
from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.core import Evidence, VigIAResponse, analyze_statement, close_session, detect_claims, load_bi_snapshot
from vigia.executive_writer import (
    build_writer_prompt,
    clean_intervention_text,
    is_usable_intervention,
    should_rewrite_response,
)
from vigia.meeting_context import build_meeting_memory

SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "data" / "bi_snapshot.json"


class CoreAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = load_bi_snapshot(SNAPSHOT_PATH)

    def test_detects_campaign_metric_and_percentage(self) -> None:
        claims = detect_claims("BCP esta estable con SLA de 96%", self.snapshot)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].campaign_id, "bcp")
        self.assertEqual(claims[0].metric_key, "sla")
        self.assertEqual(claims[0].spoken_value, 96.0)
        self.assertEqual(claims[0].stance, "positive")

    def test_alerts_when_risk_is_presented_as_normal(self) -> None:
        result = analyze_statement(
            speaker="Gerente BCP",
            role="Gerente",
            text="BCP esta estable, el SLA esta normal y sin riesgo.",
            snapshot=self.snapshot,
        )

        self.assertTrue(result.should_respond)
        self.assertEqual(result.severity, "critical")
        self.assertIn("BCP", result.message)

    def test_understands_negated_risk_as_false_reassurance(self) -> None:
        result = analyze_statement(
            speaker="Gerente BCP",
            role="Gerente",
            text="BCP tiene SLA de 92.1% y no hay ningún riesgo.",
            snapshot=self.snapshot,
        )

        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.category, "threshold_breach")
        self.assertTrue(result.voice_intervention)

    def test_silent_when_statement_is_not_business_fact(self) -> None:
        result = analyze_statement(
            speaker="Director",
            role="Director",
            text="Buenos dias, empecemos revisando la agenda.",
            snapshot=self.snapshot,
        )

        self.assertFalse(result.should_respond)
        self.assertEqual(result.category, "smart_silence")

    def test_uses_recent_context_for_unscoped_metric(self) -> None:
        result = analyze_statement(
            speaker="Gerente Operaciones",
            role="Operaciones",
            text="El SLA esta normal y sin riesgo.",
            snapshot=self.snapshot,
            meeting_context=[
                {"speaker": "Director", "role": "Director", "text": "Revisemos BCP primero."},
            ],
        )

        self.assertTrue(result.should_respond)
        self.assertEqual(result.severity, "critical")
        self.assertIn("BCP", result.message)
        self.assertIn("SLA contractual", result.message)

    def test_unscoped_metric_is_understood_without_crashing(self) -> None:
        result = analyze_statement(
            speaker="Director",
            role="Finanzas",
            text="El margen bruto está en 20%.",
            snapshot=self.snapshot,
        )

        self.assertFalse(result.should_respond)
        self.assertEqual(result.category, "claim_without_scope")
        self.assertTrue(result.detected_claims)
        self.assertEqual(result.evidence, [])
        self.assertIn("falta identificar la cuenta", result.executive_summary or "")

    def test_detects_multiple_metrics_and_their_nearest_values(self) -> None:
        claims = detect_claims(
            "BCP reporta SLA de 92.1% y margen bruto de 18.3%.",
            self.snapshot,
        )

        self.assertEqual([claim.metric_key for claim in claims], ["sla", "gross_margin"])
        self.assertEqual([claim.spoken_value for claim in claims], [92.1, 18.3])
        self.assertTrue(all(claim.campaign_id == "bcp" for claim in claims))

    def test_detects_metric_aliases_supplied_by_financial_system(self) -> None:
        self.snapshot["campaigns"][0]["metrics"]["cash_conversion"] = {
            "label": "Conversión de caja",
            "aliases": ["conversion de caja", "cash conversion"],
            "actual": 10.0,
            "target": 12.0,
            "direction": "min",
            "unit": "%",
        }

        claims = detect_claims("BCP tiene conversión de caja de 15%.", self.snapshot)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].metric_key, "cash_conversion")
        self.assertEqual(claims[0].spoken_value, 15.0)

    def test_understands_thousands_and_millions_in_financial_claims(self) -> None:
        self.snapshot["campaigns"][0]["metrics"]["facturacion"] = {
            "label": "Facturación",
            "aliases": ["facturado"],
            "actual": 2_500_000.0,
            "target": 2_400_000.0,
            "direction": "min",
            "unit": "S/",
        }

        millions = detect_claims("La facturación de BCP es 2.5 millones.", self.snapshot)
        thousands = detect_claims("BCP tiene facturado 850 mil.", self.snapshot)

        self.assertEqual(millions[0].spoken_value, 2_500_000.0)
        self.assertEqual(thousands[0].spoken_value, 850_000.0)

    def test_does_not_interrupt_a_correct_acknowledgement_of_risk(self) -> None:
        result = analyze_statement(
            speaker="Gerente BCP",
            role="Operaciones",
            text="BCP tiene SLA de 92.1%, está fuera de meta y hay riesgo.",
            snapshot=self.snapshot,
        )

        self.assertEqual(result.severity, "info")
        self.assertEqual(result.category, "data_confirmation")
        self.assertFalse(result.voice_intervention)

    def test_corrects_a_false_risk_claim_for_a_healthy_metric(self) -> None:
        result = analyze_statement(
            speaker="Gerente Claro",
            role="Operaciones",
            text="Claro está crítico: el SLA está fuera de meta.",
            snapshot=self.snapshot,
        )

        self.assertEqual(result.severity, "warning")
        self.assertEqual(result.category, "false_risk_claim")
        self.assertTrue(result.voice_intervention)
        self.assertIn("96.4%", result.message)

    def test_accepts_normal_rounding_with_metric_tolerance(self) -> None:
        result = analyze_statement(
            speaker="Gerente BCP",
            role="Operaciones",
            text="BCP tiene SLA de 92%.",
            snapshot=self.snapshot,
        )

        self.assertEqual(result.severity, "info")
        self.assertEqual(result.category, "data_confirmation")

    def test_tailors_actions_to_cfo_and_coo_profiles(self) -> None:
        cfo_result = analyze_statement(
            speaker="Sala",
            role="Voz",
            text="BCP tiene margen bruto de 25%.",
            snapshot=self.snapshot,
            executive_profile="cfo",
        )
        coo_result = analyze_statement(
            speaker="Sala",
            role="Voz",
            text="BCP tiene margen bruto de 25%.",
            snapshot=self.snapshot,
            executive_profile="coo",
        )

        self.assertTrue(cfo_result.recommended_actions[0].startswith("Financiero:"))
        self.assertTrue(coo_result.recommended_actions[0].startswith("Operativo:"))
        self.assertEqual(cfo_result.executive_profile, "cfo")
        self.assertEqual(coo_result.executive_profile, "coo")

    def test_never_corrects_by_voice_with_an_untrusted_source(self) -> None:
        result = analyze_statement(
            speaker="Sala",
            role="Voz",
            text="BCP tiene SLA de 99%.",
            snapshot=self.snapshot,
            source_status={
                "available": True,
                "trusted": False,
                "stale": True,
                "source": "seguimiento_financiero",
            },
        )

        self.assertEqual(result.category, "source_unverified")
        self.assertEqual(result.severity, "info")
        self.assertFalse(result.voice_intervention)
        self.assertIn("desactualizada", result.message)

    def test_blocks_promise_not_supported_by_history(self) -> None:
        result = analyze_statement(
            speaker="Gerente Comercial",
            role="Comercial",
            text="Garantizamos que BCP cerramos en 95% de SLA.",
            snapshot=self.snapshot,
        )

        self.assertTrue(result.should_respond)
        self.assertEqual(result.category, "promise_not_supported")
        self.assertTrue(result.voice_intervention)
        self.assertIn("no validaría esa promesa", result.message)
        self.assertIn("Operativo:", result.message)

    def test_close_session_includes_session_duration(self) -> None:
        result = close_session(
            meeting_type="Comite ejecutivo A365",
            transcript=[],
            alerts=[],
            session_started_at="2026-07-02T15:00:00.000Z",
            session_ended_at="2026-07-02T15:03:05.000Z",
            duration_seconds=185,
        )

        self.assertEqual(result["summary"]["duration_seconds"], 185)
        self.assertEqual(result["session"]["duration_label"], "00:03:05")
        self.assertIn("- Duracion de sesion: 00:03:05", result["executive_minutes"])

    def test_executive_writer_prompt_keeps_evidence_grounded(self) -> None:
        response = VigIAResponse(
            tag="⚠️ ALERTA",
            message="Intervengo: ese dato no cuadra.",
            should_respond=True,
            severity="warning",
            category="value_mismatch",
            evidence=[
                Evidence(
                    source="BI",
                    generated_at="2026-07-02T09:00:00-05:00",
                    campaign="BCP",
                    metric="Margen bruto",
                    actual=18.3,
                    target=20.0,
                    direction="min",
                    unit="%",
                )
            ],
            recommended_actions=["Financiero: recalcular forecast con costos reales."],
        )

        prompt = build_writer_prompt(
            response,
            speaker="Sala",
            role="Voz",
            text="BCP tiene margen de 20%",
            meeting_context=[],
            meeting_memory={
                "active_campaigns": ["BCP"],
                "active_metrics": ["Margen bruto"],
                "emotional_state": {
                    "latest_label": "preocupacion",
                    "dominant_label": "preocupacion",
                    "tension_count": 1,
                    "reading": "tension puntual: sostener la decision con datos y tono calmo",
                },
                "recent_transcript": [{"speaker": "Sala", "role": "Voz", "text": "Estamos preocupados por BCP"}],
                "recent_alerts": [],
                "bi_risks": [
                    {
                        "campaign": "BCP",
                        "metric": "Margen bruto",
                        "actual": 18.3,
                        "target": 20.0,
                        "unit": "%",
                        "severity": "warning",
                    }
                ],
                "meeting_signals": {"decisions": [], "promises": [], "risks": []},
            },
        )

        self.assertTrue(should_rewrite_response(response))
        self.assertIn("BCP: Margen bruto actual 18.3%", prompt)
        self.assertIn("Estado vivo de la reunion", prompt)
        self.assertIn("Clima emocional", prompt)
        self.assertIn("Financiero: recalcular forecast", prompt)
        self.assertIn("No cuadra", clean_intervention_text('"Alerta: No cuadra. Atentamente, vigia a365."'))
        self.assertFalse(is_usable_intervention("Intervengo: ese dato", response))
        self.assertTrue(
            is_usable_intervention(
                "Intervengo: BCP no está en 20%; el BI marca 18.3%. No aprobaría ese compromiso sin recalcular el forecast.",
                response,
            )
        )

    def test_meeting_memory_tracks_context_emotion_and_bi_risks(self) -> None:
        memory = build_meeting_memory(
            session_state={
                "transcript": [
                    {"speaker": "Director", "role": "Direccion", "text": "Revisemos BCP, hay riesgo en margen."},
                    {"speaker": "Operaciones", "role": "Operaciones", "text": "Estamos preocupados por el SLA."},
                ],
                "sentiments": [{"emoji": "😟", "text": "hay riesgo"}],
                "alerts": [],
            },
            snapshot=self.snapshot,
            current_line={"speaker": "Sala", "role": "Voz", "text": "BCP esta tenso y prometen margen."},
            current_sentiment="😟",
        )

        self.assertIn("BCP", memory["active_campaigns"])
        self.assertGreaterEqual(memory["emotional_state"]["tension_count"], 1)
        self.assertTrue(any(risk["campaign"] == "BCP" for risk in memory["bi_risks"]))
        self.assertTrue(memory["meeting_signals"]["risks"])


if __name__ == "__main__":
    unittest.main()
