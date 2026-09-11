from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.tts import TTSResult, normalize_text_for_speech, synthesize_tts


class TextToSpeechNormalizationTest(unittest.TestCase):
    def test_removes_markdown_and_speaks_financial_units_naturally(self) -> None:
        spoken = normalize_text_for_speech(
            "## **CLARO**\n- Margen: *27.98%* ✓\n- Penalidades: S/ 20,138.\n"
            "[Ver detalle](https://example.com/reporte)"
        )

        self.assertNotIn("*", spoken)
        self.assertNotIn("#", spoken)
        self.assertNotIn("http", spoken)
        self.assertIn("CLARO", spoken)
        self.assertIn("27.98 por ciento", spoken)
        self.assertIn("20,138 soles", spoken)
        self.assertIn("cumple", spoken)
        self.assertIn("Ver detalle", spoken)

    def test_understands_currency_when_unit_is_a_suffix(self) -> None:
        self.assertEqual(
            normalize_text_for_speech("Penalidades: 20138S/"),
            "Penalidades: 20138 soles.",
        )

    def test_cloud_gemini_is_default_and_receives_clean_spoken_text(self) -> None:
        expected = TTSResult(b"audio", "audio/mp3", "google-cloud-gemini-tts", "Achernar")
        with patch.dict("os.environ", {"VIGIA_TTS_PROVIDER": "gemini-cloud"}, clear=False):
            with patch("vigia.tts.synthesize_gemini_cloud_tts", return_value=expected) as cloud_tts:
                result = synthesize_tts(text="**Margen:** 20%")

        self.assertEqual(result, expected)
        cloud_tts.assert_called_once_with(text="Margen: 20 por ciento.", voice_name=None)


if __name__ == "__main__":
    unittest.main()
