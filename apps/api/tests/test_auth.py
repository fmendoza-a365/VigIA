from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from vigia.auth import create_session_token, credentials_are_valid, verify_session_token


class AuthTest(unittest.TestCase):
    def test_validates_configured_credentials_without_fallback_password(self) -> None:
        with patch.dict(
            os.environ,
            {"VIGIA_ADMIN_USER": "director", "VIGIA_ADMIN_PASSWORD": "segura"},
            clear=False,
        ):
            self.assertTrue(credentials_are_valid("director", "segura"))
            self.assertFalse(credentials_are_valid("director", "incorrecta"))

    def test_signed_session_expires_and_rejects_tampering(self) -> None:
        env = {
            "VIGIA_ADMIN_USER": "director",
            "VIGIA_SESSION_SECRET": "a-test-secret-with-sufficient-entropy",
            "VIGIA_SESSION_TTL_SECONDS": "900",
        }
        with patch.dict(os.environ, env, clear=False):
            token = create_session_token("director", now=1_000)
            self.assertEqual(verify_session_token(token, now=1_100)["sub"], "director")
            self.assertIsNone(verify_session_token(f"{token}x", now=1_100))
            self.assertIsNone(verify_session_token(token, now=1_901))


if __name__ == "__main__":
    unittest.main()
