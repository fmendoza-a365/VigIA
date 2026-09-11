from __future__ import annotations

import asyncio
import sys
import os
import unittest
from unittest import mock
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.live_assistant import (
    GeminiLiveMeeting,
    build_live_config,
    merge_transcript,
    live_enabled,
    live_playback_provider,
    priority_risks,
    query_financial_data,
    normalize_detail_section,
    session_system_instruction,
    scope_tool_arguments,
)


SNAPSHOT = {
    "source": "seguimiento_financiero",
    "period": "2026-07",
    "campaigns": [
        {
            "id": "claro-peru",
            "name": "CLARO PERÚ",
            "aliases": ["Claro Peru"],
            "metrics": {
                "gross_margin": {
                    "label": "Margen bruto",
                    "aliases": ["margen"],
                    "actual": 27.98,
                    "target": 20.0,
                    "direction": "min",
                    "unit": "%",
                },
                "penalidades": {
                    "label": "Penalidades",
                    "actual": 20138.0,
                    "target": 0.0,
                    "direction": "min",
                    "unit": "PEN",
                },
            },
        },
        {
            "id": "seguros",
            "name": "SEGUROS",
            "metrics": {
                "gross_margin": {
                    "label": "Margen bruto",
                    "aliases": ["margen"],
                    "actual": 9.38,
                    "target": 20.0,
                    "direction": "min",
                    "unit": "%",
                }
            },
        },
    ],
}


class LiveAssistantTest(unittest.TestCase):
    def test_query_returns_only_requested_account_and_metric(self) -> None:
        result = query_financial_data(
            SNAPSHOT,
            account="Claro Peru",
            metric="margen bruto",
            query="cuál es el margen",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["account"], "CLARO PERÚ")
        self.assertEqual(len(result["results"][0]["metrics"]), 1)
        self.assertEqual(result["results"][0]["metrics"][0]["actual"], 27.98)

    def test_priority_risks_can_be_scoped_to_one_account(self) -> None:
        result = priority_risks(SNAPSHOT, "Seguros")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["risks"]), 1)
        self.assertEqual(result["risks"][0]["account"], "SEGUROS")
        self.assertEqual(result["risks"][0]["actual"], 9.38)

    def test_transcription_deltas_merge_without_duplication(self) -> None:
        self.assertEqual(merge_transcript("cuál es", "cuál es el margen"), "cuál es el margen")
        self.assertEqual(merge_transcript("cuál es", "el margen"), "cuál es el margen")

    def test_workforce_terms_use_the_aggregated_agents_dataset(self) -> None:
        self.assertEqual(normalize_detail_section("dotación"), "agents")
        self.assertEqual(normalize_detail_section("workforce"), "agents")
        self.assertEqual(normalize_detail_section("planilla_detallada"), "payroll_detail")

    def test_live_config_enables_native_audio_controls(self) -> None:
        config = build_live_config()

        self.assertEqual(config.thinking_config.thinking_budget, 0)
        self.assertEqual(len(config.tools[0].function_declarations), 2)
        activity = config.realtime_input_config.automatic_activity_detection
        self.assertFalse(activity.disabled)
        self.assertEqual(activity.prefix_padding_ms, 180)
        self.assertEqual(activity.silence_duration_ms, 480)

    def test_live_config_applies_all_pre_meeting_parameters(self) -> None:
        state = {
            "meeting_type": "Revisión financiera",
            "executive_profile": "cfo",
            "intervention_level": "critical",
            "voice_mode": True,
            "participants": [{"name": "Ana", "role": "Gerente"}],
            "financial_scope": {"accounts": ["CLARO PERÚ"], "campaigns": [], "label": "CLARO PERÚ"},
        }
        instruction = session_system_instruction(state)
        config = build_live_config(state)

        self.assertIn("Revisión financiera", instruction)
        self.assertIn("Director Financiero", instruction)
        self.assertIn("solo ante riesgos críticos", instruction)
        self.assertIn("Ana (Gerente)", instruction)
        self.assertIn("Alcance financiero obligatorio: CLARO PERÚ", instruction)
        self.assertIn("máximo de dos posiciones", instruction)
        self.assertIn("Revisión financiera", str(config.system_instruction))

    def test_financial_tools_cannot_leave_the_meeting_scope(self) -> None:
        state = {"financial_scope": {"accounts": ["CLARO PERÚ"], "campaigns": [], "label": "CLARO PERÚ"}}

        allowed = scope_tool_arguments(SNAPSHOT, {"cuenta": "CLARO PERÚ", "seccion": "resumen"}, state)
        denied = scope_tool_arguments(SNAPSHOT, {"cuenta": "SEGUROS", "seccion": "resumen"}, state)

        self.assertTrue(allowed["ok"])
        self.assertFalse(denied["ok"])
        self.assertIn("fuera del alcance", denied["error"])

    def test_live_external_audio_is_opt_in(self) -> None:
        with mock.patch.dict(os.environ, {"VIGIA_LIVE_ENABLED": "false"}, clear=False):
            self.assertFalse(live_enabled())

    def test_live_uses_low_latency_native_playback_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIGIA_LIVE_PLAYBACK", None)
            self.assertEqual(live_playback_provider(), "native")

    def test_live_reopens_the_sdk_receiver_after_every_turn(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def receive(self):
                self.calls += 1
                current_call = self.calls

                async def messages():
                    if current_call <= 2:
                        yield object()

                return messages()

        session = FakeSession()
        meeting = GeminiLiveMeeting(mock.MagicMock(), {})
        meeting.handle_gemini_message = mock.AsyncMock()

        asyncio.run(meeting.receive_gemini(session))

        self.assertEqual(session.calls, 3)
        self.assertEqual(meeting.handle_gemini_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
