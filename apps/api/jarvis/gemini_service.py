"""VigIA voice sessions backed by the deterministic fact-check engine.

Gemini may rewrite a validated alert, but it is never allowed to decide
whether the meeting agent should contradict a participant.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from vigia.core import VigIAResponse, analyze_statement
from vigia.executive_writer import enhance_executive_intervention
from vigia.financial_data import FinancialDataError, financial_data_service
from vigia.meeting_context import build_meeting_memory
from vigia.tts import synthesize_tts

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # Go up to VigIA root
load_dotenv(PROJECT_ROOT / ".env")

# Configuration
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Orus")

class JarvisSession:
    """Manage one deterministic meeting fact-check session."""

    def __init__(
        self,
        session_id: str,
        on_transcript: Callable[[str, str], None] | None = None,
        on_audio: Callable[[bytes], None] | None = None,
        on_state_change: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        executive_profile: str = "balanced",
    ):
        self.session_id = session_id
        self.on_transcript = on_transcript
        self.on_audio = on_audio
        self.on_state_change = on_state_change
        self.on_tool_call = on_tool_call
        self.executive_profile = executive_profile

        self.is_active = False
        self.is_speaking = False
        self.is_listening = False
        self.last_audio_content_type = "audio/wav"
        self.last_audio_provider = ""
        self.last_audio_voice = ""
        self.last_analysis: VigIAResponse | None = None
        self.voice_alert_fingerprints: dict[str, float] = {}

        # Conversation history
        self.messages: list[dict[str, Any]] = []

    async def initialize(self) -> bool:
        """Validate the official source and activate the meeting session."""
        try:
            await asyncio.to_thread(financial_data_service.get_snapshot)
            self.is_active = True
            self._notify_state("connected")

            return True

        except Exception as e:
            print(f"Error initializing VigIA: {e}")
            self._notify_state("error")
            return False

    async def send_text(self, text: str) -> str:
        """Fact-check a final transcript and return only a justified response."""
        if not self.is_active:
            return "Error: Sesión no activa"

        try:
            self._notify_state("processing")
            self.last_analysis = None

            meeting_context = [
                {
                    "speaker": "Sala",
                    "role": "Voz",
                    "text": item["content"],
                    "timestamp": item["timestamp"],
                }
                for item in self.messages
                if item.get("role") == "user"
            ]

            current_line = {
                "speaker": "Sala",
                "role": "Voz",
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }
            self.messages.append({
                "role": "user",
                "content": text,
                "timestamp": current_line["timestamp"],
            })

            snapshot = await asyncio.to_thread(financial_data_service.get_snapshot)
            source_status = financial_data_service.status()
            result = analyze_statement(
                speaker="Sala",
                role="Voz",
                text=text,
                snapshot=snapshot,
                meeting_context=meeting_context,
                executive_profile=self.executive_profile,
                source_status=source_status,
            )
            meeting_memory = build_meeting_memory(
                session_state={
                    "transcript": meeting_context,
                    "alerts": [
                        item.get("analysis") for item in self.messages
                        if item.get("analysis")
                    ],
                    "sentiments": [],
                },
                snapshot=snapshot,
                current_line=current_line,
                current_sentiment=result.sentiment,
            )
            result = await asyncio.to_thread(
                enhance_executive_intervention,
                result,
                speaker="Sala",
                role="Voz",
                text=text,
                meeting_context=meeting_context,
                meeting_memory=meeting_memory,
            )
            result = self._apply_voice_cooldown(result)
            self.last_analysis = result

            response_text = ""
            if result.should_respond and result.category not in {"data_confirmation", "smart_silence"}:
                response_text = result.message

            if response_text:
                self.messages.append({
                    "role": "assistant",
                    "content": response_text,
                    "timestamp": datetime.now().isoformat(),
                    "analysis": result.to_dict(),
                })

            if self.on_transcript and response_text:
                self.on_transcript("assistant", response_text)

            self._notify_state("connected")

            return response_text

        except FinancialDataError:
            self.last_analysis = None
            self._notify_state("error")
            return "No puedo validar la afirmación porque seguimiento_financiero no está disponible."
        except Exception as e:
            self.last_analysis = None
            print(f"Error sending text: {e}")
            self._notify_state("error")
            return "No pude analizar esta frase de forma segura."

    async def speak(self, text: str) -> bytes | None:
        """Convert text to speech and return audio data."""
        try:
            self.is_speaking = True
            self._notify_state("assistant_speaking")

            # Truncate text for faster TTS
            truncated_text = text[:500] if len(text) > 500 else text

            tts_result = await asyncio.to_thread(
                synthesize_tts,
                text=truncated_text,
                provider=os.getenv("VIGIA_TTS_PROVIDER", "gemini-cloud"),
                voice_name=os.getenv("VIGIA_GEMINI_TTS_VOICE", GEMINI_VOICE),
                speaking_rate=1.03,
            )
            self.last_audio_content_type = tts_result.content_type
            self.last_audio_provider = tts_result.provider
            self.last_audio_voice = tts_result.voice

            # Notify audio
            if self.on_audio:
                self.on_audio(tts_result.audio)

            self.is_speaking = False
            self._notify_state("connected")

            return tts_result.audio

        except Exception as e:
            print(f"Error generating speech: {e}")
            self.is_speaking = False
            return None

    async def send_audio(self, audio_data: bytes) -> str | None:
        """Audio enters through the shared Speech-to-Text WebSocket."""
        return None

    def interrupt(self) -> None:
        """Interrupt current response."""
        if self.is_speaking:
            self.is_speaking = False
            self._notify_state("interrupted")

    def mute_microphone(self) -> None:
        """Mute the microphone."""
        self.is_listening = False
        self._notify_state("muted")

    def unmute_microphone(self) -> None:
        """Unmute the microphone."""
        self.is_listening = True
        self._notify_state("connected")

    async def close(self) -> None:
        """Close the session and release resources."""
        try:
            self.is_active = False
            self.is_speaking = False
            self.is_listening = False
            self.last_analysis = None
            self.voice_alert_fingerprints = {}
            self._notify_state("disconnected")

        except Exception as e:
            print(f"Error closing session: {e}")

    def _notify_state(self, state: str) -> None:
        """Notify state change."""
        if self.on_state_change:
            self.on_state_change(state)

    def _apply_voice_cooldown(self, result: VigIAResponse) -> VigIAResponse:
        if not result.voice_intervention or result.severity not in {"warning", "critical"}:
            return result
        fingerprint = f"{result.category}|" + "|".join(
            f"{item.campaign}:{item.metric}" for item in result.evidence[:4]
        )
        try:
            cooldown = max(0, int(os.getenv("VIGIA_INTERVENTION_COOLDOWN_SECONDS", "45")))
        except ValueError:
            cooldown = 45
        now = time.monotonic()
        previous = self.voice_alert_fingerprints.get(fingerprint)
        if previous is not None and now - previous < cooldown:
            result.voice_intervention = False
            result.voice_suppressed_reason = "duplicate_cooldown"
            return result
        self.voice_alert_fingerprints[fingerprint] = now
        return result

    def get_history(self) -> list[dict[str, Any]]:
        """Get conversation history."""
        return self.messages


class JarvisService:
    """Service for managing VigIA voice sessions."""

    def __init__(self):
        self.sessions: dict[str, JarvisSession] = {}

    async def create_session(
        self,
        session_id: str,
        on_transcript: Callable[[str, str], None] | None = None,
        on_audio: Callable[[bytes], None] | None = None,
        on_state_change: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        executive_profile: str = "balanced",
    ) -> JarvisSession:
        """Create a new VigIA voice session."""
        session = JarvisSession(
            session_id=session_id,
            on_transcript=on_transcript,
            on_audio=on_audio,
            on_state_change=on_state_change,
            on_tool_call=on_tool_call,
            executive_profile=executive_profile,
        )

        success = await session.initialize()
        if success:
            self.sessions[session_id] = session
            return session
        else:
            raise RuntimeError("Failed to initialize VigIA voice session")

    async def get_session(self, session_id: str) -> JarvisSession | None:
        """Get an existing session."""
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        """Close a session."""
        session = self.sessions.get(session_id)
        if session:
            await session.close()
            del self.sessions[session_id]

    async def close_all_sessions(self) -> None:
        """Close all sessions."""
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)


# Global service instance
jarvis_service = JarvisService()
