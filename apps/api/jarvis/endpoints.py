"""VigIA A365 - FastAPI Endpoints

This module provides REST and WebSocket endpoints for VigIA voice mode.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field

from .gemini_service import jarvis_service
from vigia.auth import AUTH_COOKIE_NAME, verify_session_token

router = APIRouter(tags=["vigia-voice"])


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST/RESPONSE MODELS
# ─────────────────────────────────────────────────────────────────────────────

class SessionStartRequest(BaseModel):
    user_id: str = Field(default="default")
    voice: str = Field(default="Orus")
    language: str = Field(default="es")
    executive_profile: Literal["cfo", "coo", "balanced"] = "balanced"


class SessionStartResponse(BaseModel):
    session_id: str
    status: str
    message: str
    audio: str | None = None
    content_type: str | None = None
    provider: str | None = None
    voice: str | None = None


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    is_active: bool
    is_speaking: bool
    is_listening: bool
    message_count: int


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


class MessageResponse(BaseModel):
    session_id: str
    response: str
    audio: str | None = None  # Base64 encoded audio
    content_type: str | None = None
    provider: str | None = None
    voice: str | None = None
    timestamp: str
    should_respond: bool = False
    voice_intervention: bool = False
    severity: str = "silent"
    category: str = "smart_silence"
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[dict[str, Any]]


# ─────────────────────────────────────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/session/start", response_model=SessionStartResponse)
async def start_session(request: SessionStartRequest) -> SessionStartResponse:
    """Start a new VigIA voice session."""
    try:
        session_id = str(uuid.uuid4())

        def on_transcript(role: str, text: str):
            print(f"[{session_id}] {role}: {text}")

        def on_state_change(state: str):
            print(f"[{session_id}] State: {state}")

        session = await jarvis_service.create_session(
            session_id=session_id,
            on_transcript=on_transcript,
            on_state_change=on_state_change,
            executive_profile=request.executive_profile,
        )

        # Keep connection fast: do not call Gemini or TTS during startup.
        welcome_text = "vigia a365 conectado."

        return SessionStartResponse(
            session_id=session_id,
            status="active",
            message=welcome_text,
        )

    except Exception as e:
        print(f"Error starting session: {e}")
        raise HTTPException(status_code=503, detail="No se pudo iniciar la sesión de voz.") from e


@router.post("/session/{session_id}/stop")
async def stop_session(session_id: str) -> dict[str, str]:
    """Stop a VigIA voice session."""
    try:
        await jarvis_service.close_session(session_id)
        return {"status": "stopped", "session_id": session_id}

    except Exception as e:
        print(f"Error stopping voice session: {e}")
        raise HTTPException(status_code=500, detail="No se pudo cerrar la sesión de voz.") from e


@router.get("/session/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(session_id: str) -> SessionStatusResponse:
    """Get session status."""
    session = await jarvis_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionStatusResponse(
        session_id=session_id,
        status="active" if session.is_active else "inactive",
        is_active=session.is_active,
        is_speaking=session.is_speaking,
        is_listening=session.is_listening,
        message_count=len(session.messages),
    )


@router.post("/session/{session_id}/message", response_model=MessageResponse)
async def send_message(session_id: str, request: MessageRequest) -> MessageResponse:
    """Send a text message to VigIA."""
    session = await jarvis_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        # Send text and get response
        response_text = await session.send_text(request.text)

        analysis = session.last_analysis
        should_speak = bool(
            response_text
            and analysis
            and analysis.voice_intervention
            and analysis.severity in {"warning", "critical"}
        )
        audio_data = await session.speak(response_text) if should_speak else None
        audio_base64 = base64.b64encode(audio_data).decode() if audio_data else None

        return MessageResponse(
            session_id=session_id,
            response=response_text,
            audio=audio_base64,
            content_type=session.last_audio_content_type if audio_base64 else None,
            provider=session.last_audio_provider if audio_base64 else None,
            voice=session.last_audio_voice if audio_base64 else None,
            timestamp=datetime.now().isoformat(),
            should_respond=bool(response_text),
            voice_intervention=should_speak,
            severity=analysis.severity if analysis else "silent",
            category=analysis.category if analysis else "smart_silence",
            evidence=[item.__dict__ for item in analysis.evidence] if analysis else [],
        )

    except Exception as e:
        print(f"Error processing voice session message: {e}")
        raise HTTPException(status_code=500, detail="No se pudo analizar la frase.") from e


@router.get("/session/{session_id}/history", response_model=HistoryResponse)
async def get_history(session_id: str) -> HistoryResponse:
    """Get conversation history."""
    session = await jarvis_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return HistoryResponse(
        session_id=session_id,
        messages=session.get_history(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def jarvis_websocket(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for real-time conversation."""
    if verify_session_token(websocket.cookies.get(AUTH_COOKIE_NAME)) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    session = await jarvis_service.get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    # Notify client of connection
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "message": "Conectado a vigia a365",
    })

    try:
        while True:
            # Receive message from client
            data = await websocket.receive()

            if data["type"] == "websocket.disconnect":
                break

            if data["type"] == "websocket.receive":
                if "text" in data:
                    try:
                        message = json.loads(data["text"])

                        if message.get("type") == "text":
                            # Text message
                            text = message["text"]

                            # Notify user transcript
                            await websocket.send_json({
                                "type": "transcript",
                                "role": "user",
                                "text": text,
                            })

                            # Get response from Gemini
                            response_text = await session.send_text(text)

                            analysis = session.last_analysis
                            should_speak = bool(
                                response_text
                                and analysis
                                and analysis.voice_intervention
                                and analysis.severity in {"warning", "critical"}
                            )
                            audio_data = await session.speak(response_text) if should_speak else None
                            audio_base64 = base64.b64encode(audio_data).decode() if audio_data else None

                            # Send response
                            if response_text:
                                await websocket.send_json({
                                    "type": "transcript",
                                    "role": "assistant",
                                    "text": response_text,
                                    "severity": analysis.severity if analysis else "silent",
                                    "category": analysis.category if analysis else "smart_silence",
                                })

                            if audio_base64:
                                await websocket.send_json({
                                    "type": "audio",
                                    "data": audio_base64,
                                })

                        elif message.get("type") == "interrupt":
                            session.interrupt()

                        elif message.get("type") == "mute":
                            session.mute_microphone()

                        elif message.get("type") == "unmute":
                            session.unmute_microphone()

                    except json.JSONDecodeError:
                        # Plain text message
                        text = data["text"]
                        response_text = await session.send_text(text)
                        if response_text:
                            await websocket.send_json({
                                "type": "transcript",
                                "role": "assistant",
                                "text": response_text,
                            })

    except WebSocketDisconnect:
        pass

    except Exception as e:
        print(f"WebSocket error: {e}")

    finally:
        # Clean up
        await jarvis_service.close_session(session_id)


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "vigia a365"}
