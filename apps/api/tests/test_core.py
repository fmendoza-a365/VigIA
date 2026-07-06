from __future__ import annotations

import unittest
from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.core import analyze_statement, close_session, detect_claims, load_bi_snapshot

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

    def test_silent_when_statement_is_not_business_fact(self) -> None:
        result = analyze_statement(
            speaker="Director",
            role="Director",
            text="Buenos dias, empecemos revisando la agenda.",
            snapshot=self.snapshot,
        )

        self.assertFalse(result.should_respond)
        self.assertEqual(result.category, "smart_silence")

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


if __name__ == "__main__":
    unittest.main()
