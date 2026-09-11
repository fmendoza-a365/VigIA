from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.conversational_assistant import (
    answer_executive_question,
    build_prompt,
    is_usable_answer,
    thinking_budget,
)


SNAPSHOT = {
    "source": "seguimiento_financiero",
    "period": "2026-07",
    "campaigns": [
        {
            "name": "CLARO PERÚ",
            "metrics": {
                "gross_margin": {
                    "label": "Margen bruto",
                    "actual": 27.98,
                    "target": 20.0,
                    "direction": "min",
                    "unit": "%",
                }
            },
        }
    ],
}


class ConversationalAssistantTest(unittest.TestCase):
    def test_prompt_contains_only_the_supplied_official_snapshot(self) -> None:
        prompt = build_prompt(
            question="¿Cómo está Claro?",
            snapshot=SNAPSHOT,
            deterministic_answer="CLARO PERÚ tiene margen de 27.98%.",
            source_status={"trusted": True},
            executive_profile="balanced",
            conversation=[],
        )

        self.assertIn("seguimiento_financiero", prompt)
        self.assertIn("CLARO PERÚ", prompt)
        self.assertIn("27.98%", prompt)
        self.assertIn("CONSULTA ACTUAL: ¿Cómo está Claro?", prompt)

    def test_returns_grounded_fallback_without_api_key(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
            answer = answer_executive_question(
                question="¿Cómo está Claro?",
                snapshot=SNAPSHOT,
                deterministic_answer="Respuesta validada.",
                source_status={"trusted": True},
            )

        self.assertEqual(answer, "Respuesta validada.")

    def test_prompt_includes_the_bounded_official_detail_requested(self) -> None:
        prompt = build_prompt(
            question="¿Cuánta dotación tenemos?",
            snapshot=SNAPSHOT,
            deterministic_answer="SIFO registra 120 personas.",
            source_status={"trusted": True},
            executive_profile="coo",
            conversation=[],
            official_detail={
                "ok": True,
                "section": "agents",
                "people_total": 120,
                "total_groups": 8,
            },
        )

        self.assertIn("DETALLE OFICIAL CONSULTADO", prompt)
        self.assertIn('"people_total":120', prompt)
        self.assertIn('"total_groups":8', prompt)

    def test_requires_the_selected_campaign_and_value(self) -> None:
        self.assertTrue(
            is_usable_answer(
                "CLARO PERÚ tiene un margen confirmado de 27,98 por ciento.",
                required_campaign="CLARO PERÚ",
                required_value=27.98,
            )
        )
        self.assertFalse(
            is_usable_answer(
                "La cuenta se encuentra saludable.",
                required_campaign="CLARO PERÚ",
                required_value=27.98,
            )
        )

    def test_dynamic_reasoning_is_the_default_and_budget_is_bounded(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_THINKING_BUDGET", None)
            self.assertEqual(thinking_budget("TEST_THINKING_BUDGET", -1), -1)
        with patch.dict(os.environ, {"TEST_THINKING_BUDGET": "99999"}, clear=False):
            self.assertEqual(thinking_budget("TEST_THINKING_BUDGET", -1), 24576)


if __name__ == "__main__":
    unittest.main()
