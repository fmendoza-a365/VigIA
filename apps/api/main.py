from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError, InvalidArgument, PermissionDenied, Unauthenticated
from pydantic import BaseModel, Field

from vigia.core import (
    VigIAResponse,
    analyze_statement,
    close_session,
    detect_campaigns,
    detect_metric,
    is_metric_compliant,
    normalize,
    SIGNATURE,
)
from vigia.auth import (
    AUTH_COOKIE_NAME,
    cookie_secure,
    create_session_token,
    credentials_are_valid,
    session_ttl_seconds,
    verify_session_token,
)
from vigia.conversational_assistant import answer_executive_question
from vigia.executive_writer import enhance_executive_intervention
from vigia.financial_data import (
    FinancialDataError,
    financial_data_service,
    financial_scope_options,
    snapshot_for_session,
)
from vigia.google_speech import configure_google_credentials, stream_transcribe_opus, transcribe_webm_opus
from vigia.meeting_context import build_meeting_memory, empty_meeting_memory
from vigia.live_assistant import GeminiLiveMeeting
from vigia.official_questions import (
    campaign_catalog_detail,
    coverage_detail,
    detect_official_section,
    extract_official_filters,
    format_official_answer,
)
from vigia.tts import synthesize_tts

# Import VigIA voice endpoints
from jarvis.endpoints import router as vigia_voice_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_AUDIO_BYTES = 8 * 1024 * 1024
STREAM_QUEUE_SIZE = 80

load_dotenv(PROJECT_ROOT / ".env")
GOOGLE_CREDENTIALS_PATH = configure_google_credentials(PROJECT_ROOT)

# Session state (in-memory for now)
session_state: dict[str, Any] = {
    "status": "idle",
    "meeting_type": "",
    "date": "",
    "participants": [],
    "voice_mode": False,
    "executive_profile": "balanced",
    "intervention_level": "warning",
    "financial_scope": {"accounts": [], "campaigns": [], "label": "Todo SIFO"},
    "started_at": None,
    "transcript": [],
    "alerts": [],
    "sentiments": [],
    "meeting_memory": empty_meeting_memory(),
    "voice_alert_fingerprints": {},
}

# TTS cache for faster responses
tts_cache: dict[str, dict[str, Any]] = {}
TTS_CACHE_MAX_SIZE = 50


def get_financial_snapshot(*, force_refresh: bool = False) -> dict[str, Any]:
    try:
        return financial_data_service.get_snapshot(force_refresh=force_refresh)
    except FinancialDataError as exc:
        raise HTTPException(
            status_code=503,
            detail="La fuente seguimiento_financiero no está disponible o devolvió datos inválidos.",
        ) from exc


def speech_hints_from_snapshot(snapshot: dict[str, Any]) -> list[str]:
    """Build STT vocabulary from the same financial source used for fact checks."""
    hints = [
        "seguimiento financiero",
        "margen bruto",
        "facturación",
        "ingreso neto",
        "planilla directa",
        "penalidades",
        "director financiero",
        "director de operaciones",
    ]
    for campaign in snapshot.get("campaigns", []):
        hints.append(str(campaign.get("name", "")))
        for metric in campaign.get("metrics", {}).values():
            hints.append(str(metric.get("label", "")))
            hints.extend(str(alias) for alias in metric.get("aliases", []))
    for catalog_campaign in financial_scope_options(snapshot).get("campaigns", []):
        hints.append(str(catalog_campaign.get("value", "")))
        hints.append(str(catalog_campaign.get("label", "")))
    return list(dict.fromkeys(hint for hint in hints if hint.strip()))


def apply_voice_intervention_policy(result: VigIAResponse) -> VigIAResponse:
    """Apply operator controls and a cooldown after the fact-check decision."""
    if not result.voice_intervention:
        return result

    if not session_state.get("voice_mode", False):
        result.voice_intervention = False
        result.voice_suppressed_reason = "voice_mode_disabled"
        return result

    if result.severity not in {"warning", "critical"}:
        result.voice_intervention = False
        result.voice_suppressed_reason = "not_a_fact_check_alert"
        return result

    intervention_level = session_state.get("intervention_level", "warning")
    if intervention_level == "manual":
        result.voice_intervention = False
        result.voice_suppressed_reason = "manual_mode"
        return result
    if intervention_level == "critical" and result.severity != "critical":
        result.voice_intervention = False
        result.voice_suppressed_reason = "below_configured_threshold"
        return result

    evidence_key = "|".join(
        f"{item.campaign}:{item.metric}" for item in result.evidence[:4]
    )
    fingerprint = f"{result.category}|{evidence_key}"
    now = time.monotonic()
    fingerprints = session_state.setdefault("voice_alert_fingerprints", {})
    try:
        cooldown_seconds = max(0, int(os.getenv("VIGIA_INTERVENTION_COOLDOWN_SECONDS", "45")))
    except ValueError:
        cooldown_seconds = 45
    last_intervention = fingerprints.get(fingerprint)
    if last_intervention is not None and now - float(last_intervention) < cooldown_seconds:
        result.voice_intervention = False
        result.voice_suppressed_reason = "duplicate_cooldown"
        return result

    fingerprints[fingerprint] = now
    expired_before = now - max(300, cooldown_seconds * 4)
    session_state["voice_alert_fingerprints"] = {
        key: value for key, value in fingerprints.items() if float(value) >= expired_before
    }
    return result


def official_detail_for_question(
    *,
    section: str,
    question: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Read the requested SIFO section without escaping the meeting scope."""
    filters = extract_official_filters(question, snapshot)
    if section == "campaigns":
        return campaign_catalog_detail(
            snapshot,
            account=str(filters["account"]),
            campaign=str(filters["campaign"]),
        )
    if section == "coverage":
        return coverage_detail(snapshot)

    account = str(filters["account"])
    campaign = str(filters["campaign"])
    scope = session_state.get("financial_scope", {})
    scope_accounts = scope.get("accounts", []) if isinstance(scope, dict) else []
    scope_campaigns = scope.get("campaigns", []) if isinstance(scope, dict) else []
    if not account and len(scope_accounts) == 1:
        account = str(scope_accounts[0])
    if not campaign and len(scope_campaigns) == 1:
        campaign = str(scope_campaigns[0])
    if not account and len(scope_accounts) > 1:
        return {
            "ok": False,
            "section": section,
            "error": "Indica una cuenta del alcance de la reunión para consultar ese detalle.",
        }
    if not campaign and len(scope_campaigns) > 1:
        return {
            "ok": False,
            "section": section,
            "error": "Indica una campaña del alcance de la reunión para consultar ese detalle.",
        }

    try:
        return financial_data_service.query_official_details(
            section=section,
            account=account,
            campaign=campaign,
            year=filters["year"],
            month=filters["month"],
            date_from=str(filters["date_from"]),
            date_to=str(filters["date_to"]),
            search=str(filters["search"]),
            page=int(filters["page"]),
            limit=int(filters["limit"]),
        )
    except FinancialDataError:
        return {
            "ok": False,
            "section": section,
            "error": "El detalle oficial solicitado no está disponible en este momento.",
        }


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST/RESPONSE MODELS
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    speaker: str = Field(default="Participante")
    role: str = Field(default="Invitado")
    text: str = Field(min_length=1)


class TranscriptLine(BaseModel):
    speaker: str
    role: str = "Invitado"
    text: str
    timestamp: str | None = None


class CloseSessionRequest(BaseModel):
    meeting_type: str = "Comite ejecutivo"
    transcript: list[TranscriptLine]
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    sentiments: list[dict[str, Any]] = Field(default_factory=list)
    session_started_at: str | None = None
    session_ended_at: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)


class SessionStartRequest(BaseModel):
    meeting_type: str = Field(default="Comite ejecutivo")
    date: str = Field(default_factory=lambda: datetime.now().isoformat())
    participants: list[dict[str, str]] = Field(default_factory=list)
    voice_mode: bool = Field(default=False)
    executive_profile: Literal["cfo", "coo", "balanced"] = "balanced"
    intervention_level: Literal["warning", "critical", "manual"] = "warning"
    financial_scope: dict[str, list[str]] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class SpeechStatusResponse(BaseModel):
    configured: bool
    credentials_path: str | None
    language: str
    model: str


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    provider: str | None = Field(default=None)
    voice_name: str | None = Field(default=None)
    cloud_voice_name: str | None = Field(default=None)
    speaking_rate: float = Field(default=1.05, ge=0.25, le=4.0)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


def configured_cors_origins() -> list[str]:
    configured = os.getenv("VIGIA_CORS_ORIGINS", "").strip()
    if configured:
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# APP INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="vigia a365 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_authenticated_api(request: Request, call_next: Any) -> Response:
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        session = verify_session_token(request.cookies.get(AUTH_COOKIE_NAME))
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Debes iniciar sesión para acceder a VigIA."},
            )
        request.state.auth_user = session["sub"]
    return await call_next(request)

# Include VigIA voice router. The jarvis prefix remains as a temporary alias
# for old local tabs/bookmarks.
app.include_router(vigia_voice_router, prefix="/api/vigia")
app.include_router(vigia_voice_router, prefix="/api/jarvis")


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH & STATUS ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
def auth_login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    if not credentials_are_valid(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

    token = create_session_token(payload.username.strip())
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=session_ttl_seconds(),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "user": {"username": payload.username.strip(), "role": "Administrador"},
    }


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    session = verify_session_token(request.cookies.get(AUTH_COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Sesión no válida o expirada.")
    return {
        "authenticated": True,
        "user": {"username": session["sub"], "role": "Administrador"},
    }


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"authenticated": False}

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/speech-status")
def speech_status() -> SpeechStatusResponse:
    credentials_path = GOOGLE_CREDENTIALS_PATH
    return SpeechStatusResponse(
        configured=credentials_path is not None and credentials_path.exists(),
        credentials_path="configured" if credentials_path else None,
        language=os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-PE"),
        model=os.getenv("GOOGLE_SPEECH_MODEL", "default"),
    )


@app.get("/api/bi-snapshot")
def bi_snapshot() -> dict[str, Any]:
    snapshot = get_financial_snapshot()
    # El navegador solo necesita el contrato canónico para mostrar estado y
    # cuentas. El detalle integral permanece en el servidor para no transferir
    # históricos, asistencia e IFC cada 60 segundos.
    return {
        key: snapshot.get(key)
        for key in ("generated_at", "source", "period", "campaigns", "summary", "coverage")
    } | {"scope_options": financial_scope_options(snapshot)}


@app.get("/api/data-source/status")
def data_source_status() -> dict[str, Any]:
    try:
        financial_data_service.get_snapshot()
    except FinancialDataError:
        pass
    return financial_data_service.status()


@app.post("/api/data-source/refresh")
def refresh_data_source() -> dict[str, Any]:
    get_financial_snapshot(force_refresh=True)
    return financial_data_service.status()


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/session/start")
def session_start(payload: SessionStartRequest) -> dict[str, Any]:
    """SESSION_START - Initialize a new meeting session."""
    global session_state
    get_financial_snapshot(force_refresh=True)
    try:
        scoped_snapshot = financial_data_service.get_snapshot_for_scope(
            accounts=payload.financial_scope.get("accounts", []),
            campaigns=payload.financial_scope.get("campaigns", []),
        )
    except FinancialDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    financial_scope = scoped_snapshot.get("financial_scope", {})
    session_state = {
        "status": "active",
        "meeting_type": payload.meeting_type,
        "date": payload.date,
        "participants": payload.participants,
        "voice_mode": payload.voice_mode,
        "executive_profile": payload.executive_profile,
        "intervention_level": payload.intervention_level,
        "financial_scope": financial_scope,
        "_financial_snapshot": scoped_snapshot,
        "started_at": datetime.now().isoformat(),
        "transcript": [],
        "alerts": [],
        "sentiments": [],
        "meeting_memory": empty_meeting_memory(),
        "voice_alert_fingerprints": {},
    }

    participants_str = ", ".join([f"{p.get('name', '')} ({p.get('role', '')})" for p in payload.participants])

    return {
        "status": "active",
        "message": f"vigia a365 activo. Siempre atento. Siempre a tu lado. Listo para iniciar la sesión.",
        "meeting_type": payload.meeting_type,
        "date": payload.date,
        "participants": participants_str,
        "voice_mode": payload.voice_mode,
        "executive_profile": payload.executive_profile,
        "intervention_level": payload.intervention_level,
        "financial_scope": financial_scope,
        "data_source": financial_data_service.status(),
        "signature": SIGNATURE,
    }


@app.post("/api/session/pause")
def session_pause() -> dict[str, Any]:
    """SESSION_PAUSE - Pause the current session."""
    session_state["status"] = "paused"
    return {
        "status": "paused",
        "message": "Sesión en pausa. Análisis y monitoreo suspendidos temporalmente.",
        "signature": SIGNATURE,
    }


@app.post("/api/session/resume")
def session_resume() -> dict[str, Any]:
    """SESSION_RESUME - Resume the current session."""
    session_state["status"] = "active"
    return {
        "status": "active",
        "message": "Sesión reanudada. vigia a365 atento en tiempo real.",
        "signature": SIGNATURE,
    }


@app.get("/api/session/status")
def session_status() -> dict[str, Any]:
    """Get current session status."""
    return {
        "status": session_state.get("status", "idle"),
        "meeting_type": session_state.get("meeting_type", ""),
        "voice_mode": session_state.get("voice_mode", False),
        "executive_profile": session_state.get("executive_profile", "balanced"),
        "intervention_level": session_state.get("intervention_level", "warning"),
        "financial_scope": session_state.get("financial_scope", {"accounts": [], "campaigns": [], "label": "Todo SIFO"}),
        "transcript_count": len(session_state.get("transcript", [])),
        "alerts_count": len(session_state.get("alerts", [])),
        "meeting_memory": session_state.get("meeting_memory", empty_meeting_memory()),
        "data_source": financial_data_service.status(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRANSCRIPTION ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, Any]:
    if GOOGLE_CREDENTIALS_PATH is None or not GOOGLE_CREDENTIALS_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Google Speech credentials are not configured.",
        )

    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio chunk is too large.")

    try:
        snapshot = snapshot_for_session(session_state)
        text, confidence = transcribe_webm_opus(
            content,
            language_code=os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-PE"),
            model=os.getenv("GOOGLE_SPEECH_MODEL", "default"),
            phrase_hints=speech_hints_from_snapshot(snapshot),
        )
    except (PermissionDenied, Unauthenticated) as exc:
        raise HTTPException(
            status_code=403,
            detail="Google Speech rejected credentials or permissions.",
        ) from exc
    except InvalidArgument as exc:
        raise HTTPException(
            status_code=400,
            detail="Google Speech could not process this audio format.",
        ) from exc
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=502,
            detail="Google Speech request failed.",
        ) from exc

    return {
        "text": text,
        "confidence": confidence,
        "provider": "google-cloud-speech",
        "content_type": audio.content_type,
    }


@app.websocket("/api/transcribe-stream")
async def transcribe_stream(websocket: WebSocket, mime_type: str = "audio/webm;codecs=opus") -> None:
    if verify_session_token(websocket.cookies.get(AUTH_COOKIE_NAME)) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    if GOOGLE_CREDENTIALS_PATH is None or not GOOGLE_CREDENTIALS_PATH.exists():
        await websocket.send_json(
            {
                "type": "error",
                "message": "Google Speech credentials are not configured.",
            },
        )
        await websocket.close(code=1011)
        return

    loop = asyncio.get_running_loop()
    audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=STREAM_QUEUE_SIZE)
    outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    stop_event = threading.Event()
    phrase_hints = speech_hints_from_snapshot(snapshot_for_session(session_state))

    def audio_chunks() -> Any:
        while not stop_event.is_set():
            chunk = audio_queue.get()
            if chunk is None:
                break
            yield chunk

    def put_outgoing(message: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(outgoing.put(message), loop)

    def recognizer_worker() -> None:
        try:
            for text, is_final, confidence in stream_transcribe_opus(
                audio_chunks(),
                mime_type=mime_type,
                language_code=os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-PE"),
                model=os.getenv("GOOGLE_SPEECH_MODEL", "default"),
                phrase_hints=phrase_hints,
            ):
                put_outgoing(
                    {
                        "type": "transcript",
                        "text": text,
                        "is_final": is_final,
                        "confidence": confidence,
                    },
                )
        except (PermissionDenied, Unauthenticated):
            put_outgoing({"type": "error", "message": "Google Speech rejected credentials or permissions."})
        except InvalidArgument as exc:
            print(f"Google Speech rejected the streaming audio configuration: {exc}")
            put_outgoing({"type": "error", "message": "Google Speech could not process this audio format."})
        except GoogleAPICallError:
            put_outgoing({"type": "error", "message": "Google Speech streaming request failed."})
        finally:
            put_outgoing({"type": "closed"})

    async def send_messages() -> None:
        while True:
            message = await outgoing.get()
            if message.get("type") == "closed":
                break
            await websocket.send_json(message)

    def stop_audio_queue() -> None:
        stop_event.set()
        try:
            audio_queue.put_nowait(None)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                audio_queue.get_nowait()
            with contextlib.suppress(queue.Full):
                audio_queue.put_nowait(None)

    worker = threading.Thread(target=recognizer_worker, daemon=True)
    worker.start()
    sender = asyncio.create_task(send_messages())
    await websocket.send_json({"type": "ready"})

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            chunk = message.get("bytes")
            if not chunk:
                if message.get("text") == "stop":
                    break
                continue
            try:
                audio_queue.put_nowait(chunk)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    audio_queue.get_nowait()
                with contextlib.suppress(queue.Full):
                    audio_queue.put_nowait(chunk)
    except WebSocketDisconnect:
        pass
    finally:
        stop_audio_queue()
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender


@app.websocket("/api/live-meeting")
async def live_meeting(websocket: WebSocket) -> None:
    """Bidirectional native-audio meeting session backed by Gemini Live."""
    if verify_session_token(websocket.cookies.get(AUTH_COOKIE_NAME)) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    await GeminiLiveMeeting(websocket, session_state).run()


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    """Analyze a statement and return formatted VigIA response."""
    snapshot = snapshot_for_session(session_state)
    source_status = financial_data_service.status()
    meeting_context = session_state.get("transcript", [])
    timestamp = datetime.now().isoformat()
    current_line = {
        "speaker": payload.speaker,
        "role": payload.role,
        "text": payload.text,
        "timestamp": timestamp,
    }
    result = analyze_statement(
        speaker=payload.speaker,
        role=payload.role,
        text=payload.text,
        snapshot=snapshot,
        meeting_context=meeting_context,
        executive_profile=session_state.get("executive_profile", "balanced"),
        source_status=source_status,
    )
    meeting_memory = build_meeting_memory(
        session_state=session_state,
        snapshot=snapshot,
        current_line=current_line,
        current_sentiment=result.sentiment,
    )
    result = enhance_executive_intervention(
        result,
        speaker=payload.speaker,
        role=payload.role,
        text=payload.text,
        meeting_context=meeting_context,
        meeting_memory=meeting_memory,
    )
    result = apply_voice_intervention_policy(result)
    final_meeting_memory = build_meeting_memory(
        session_state=session_state,
        snapshot=snapshot,
        current_line=current_line,
        current_sentiment=result.sentiment,
        current_alert=result.to_dict() if result.should_respond and result.severity != "silent" else None,
    )

    # Track in session state
    if session_state.get("status") == "active":
        session_state["transcript"].append(current_line)
        if result.should_respond and result.severity in {"warning", "critical"}:
            session_state["alerts"].append(result.to_dict())
        if result.sentiment:
            session_state["sentiments"].append({
                "emoji": result.sentiment,
                "text": payload.text,
                "timestamp": timestamp,
            })
        session_state["meeting_memory"] = final_meeting_memory

    return result.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# TTS ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/tts")
async def text_to_speech(payload: TTSRequest) -> dict[str, Any]:
    # Check cache first
    cache_key = (
        f"{payload.provider}:{payload.voice_name}:{payload.cloud_voice_name}:"
        f"{payload.speaking_rate}:{payload.text}"
    )
    if cache_key in tts_cache:
        return {**tts_cache[cache_key], "cached": True}

    try:
        # Truncate text for faster response
        text = payload.text[:500] if len(payload.text) > 500 else payload.text

        tts_result = await asyncio.to_thread(
            synthesize_tts,
            text=text,
            provider=payload.provider,
            voice_name=payload.voice_name,
            cloud_voice_name=payload.cloud_voice_name,
            speaking_rate=payload.speaking_rate,
        )
        audio_base64 = base64.b64encode(tts_result.audio).decode("utf-8")
        response_payload = {
            "audio": audio_base64,
            "content_type": tts_result.content_type,
            "provider": tts_result.provider,
            "voice": tts_result.voice,
        }

        # Cache the result
        if len(tts_cache) < TTS_CACHE_MAX_SIZE:
            tts_cache[cache_key] = response_payload

        return {
            **response_payload,
            "cached": False,
        }
    except Exception as e:
        print(f"TTS request failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="No se pudo generar audio para la intervención.",
        ) from e


# ─────────────────────────────────────────────────────────────────────────────
# ASK VIGIA ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/ask")
def ask_vigia(payload: AskRequest) -> dict[str, Any]:
    """Ask VigIA a question about the data."""
    snapshot = snapshot_for_session(session_state)
    # Speech-to-text may preserve or omit accents. Normalize once so campaign
    # and metric aliases are matched identically in typed and spoken questions.
    question = normalize(payload.question)
    answer = ""
    action = "answer"
    asking_readiness = any(
        phrase in question
        for phrase in [
            "me escuchas",
            "puedes escucharme",
            "estas ahi",
            "estás ahí",
            "respondeme",
            "respóndeme",
            "puedes oirme",
            "puedes oírme",
        ]
    )

    # Detect campaign in question
    campaign = None
    campaign_matches = detect_campaigns(question, snapshot)
    if campaign_matches:
        campaign_id = campaign_matches[0][0]
        campaign = next(
            (item for item in snapshot.get("campaigns", []) if item["id"] == campaign_id),
            None,
        )

    # Detect metric in question
    metric_key = detect_metric(question, snapshot)

    # Detect if user is asking about status/health
    status_words = ["como esta", "cómo está", "como va", "cómo va", "como van", "cómo van", "estado", "situacion", "situación"]
    asking_status = any(word in question for word in status_words)

    # Detect if user is asking for comparison
    comparison_words = ["comparar", "comparativa", "diferencia", "mejor", "peor", "cual es mejor", "cuál es mejor"]
    asking_comparison = any(word in question for word in comparison_words)

    # Route natural-language questions to the complete read-only SIFO surface.
    # The canonical campaign metrics remain the fast path for simple KPI checks;
    # every detailed dataset is retrieved only when the question asks for it.
    detail_section = detect_official_section(question)
    official_detail: dict[str, Any] | None = None
    if detail_section and not asking_readiness:
        official_detail = official_detail_for_question(
            section=detail_section,
            question=payload.question,
            snapshot=snapshot,
        )

    # Generate answer based on what was asked
    if asking_readiness:
        answer = "Sí, aquí estoy. Te escucho."

    elif detail_section and official_detail is not None:
        answer = format_official_answer(detail_section, official_detail)

    elif campaign and metric_key:
        metric = campaign["metrics"].get(metric_key)
        if metric:
            compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
            status = "cumple" if compliant else "no cumple"
            answer = (
                f"{campaign['name']}: {metric['label']} está en {metric['actual']}{metric['unit']}, "
                f"con meta de {metric['target']}{metric['unit']}. "
                f"Actualmente {status} el objetivo."
            )
        else:
            answer = f"No encontré la métrica {metric_key} para {campaign['name']}."

    elif campaign and asking_status:
        lines = [f"Situación de {campaign['name']}:"]
        issues = []
        for key, metric in campaign["metrics"].items():
            compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
            if not compliant:
                issues.append(metric['label'])
                lines.append(f"- {metric['label']}: {metric['actual']}{metric['unit']} (meta: {metric['target']}{metric['unit']}) - NO CUMPLE")
            else:
                lines.append(f"- {metric['label']}: {metric['actual']}{metric['unit']} (meta: {metric['target']}{metric['unit']}) - OK")

        if issues:
            answer = f"{campaign['name']} tiene problemas con: {', '.join(issues)}. " + "\n".join(lines)
        else:
            answer = f"{campaign['name']} está en buen estado. " + "\n".join(lines)

    elif campaign:
        lines = [f"Datos de {campaign['name']}:"]
        for key, metric in campaign["metrics"].items():
            compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
            status = "✓" if compliant else "✗"
            lines.append(
                f"- {metric['label']}: {metric['actual']}{metric['unit']} (meta: {metric['target']}{metric['unit']}) {status}"
            )
        answer = "\n".join(lines)

    elif metric_key and asking_comparison:
        lines = [f"Comparativa de {metric_key}:"]
        best = None
        worst = None
        for c in snapshot.get("campaigns", []):
            metric = c["metrics"].get(metric_key)
            if metric:
                compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
                status = "✓" if compliant else "✗"
                lines.append(
                    f"- {c['name']}: {metric['actual']}{metric['unit']} (meta: {metric['target']}{metric['unit']}) {status}"
                )
                value = float(metric["actual"])
                direction = metric["direction"]
                score = value if direction == "min" else -value
                if best is None or score > best[1]:
                    best = (c["name"], score, value, metric["unit"])
                if worst is None or score < worst[1]:
                    worst = (c["name"], score, value, metric["unit"])

        if best and worst:
            lines.append(f"\nMejor: {best[0]} ({best[2]}{best[3]})")
            lines.append(f"Peor: {worst[0]} ({worst[2]}{worst[3]})")
        answer = "\n".join(lines)

    elif metric_key:
        lines = [f"Comparativa de {metric_key}:"]
        for c in snapshot.get("campaigns", []):
            metric = c["metrics"].get(metric_key)
            if metric:
                compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
                status = "✓" if compliant else "✗"
                lines.append(
                    f"- {c['name']}: {metric['actual']}{metric['unit']} (meta: {metric['target']}{metric['unit']}) {status}"
                )
        answer = "\n".join(lines)

    elif asking_status:
        lines = ["Situación general del sistema:"]
        total_issues = 0
        for c in snapshot.get("campaigns", []):
            campaign_issues = []
            for key, metric in c["metrics"].items():
                compliant = is_metric_compliant(metric["actual"], metric["target"], metric["direction"])
                if not compliant:
                    campaign_issues.append(metric['label'])
            if campaign_issues:
                total_issues += len(campaign_issues)
                lines.append(f"- {c['name']}: {len(campaign_issues)} problemas ({', '.join(campaign_issues)})")
            else:
                lines.append(f"- {c['name']}: OK")

        if total_issues > 0:
            answer = f"Hay {total_issues} problemas en total. " + "\n".join(lines)
        else:
            answer = "Todo está en orden. " + "\n".join(lines)

    else:
        answer = (
            "Soy vigia a365, tu asistente de monitoreo ejecutivo. "
            "Puedo consultar las campañas y métricas disponibles en seguimiento_financiero. "
            "Pregúntame por una cuenta, indicador o comparativa. "
            f"{SIGNATURE}"
        )

    source_status = financial_data_service.status()
    # Campaign and coverage answers stay deterministic: their counts and names
    # must never be replaced by an ungrounded "no tengo información" response.
    if not asking_readiness and detail_section not in {"campaigns", "coverage"}:
        required_value = None
        if campaign and metric_key:
            selected_metric = campaign.get("metrics", {}).get(metric_key)
            if selected_metric:
                required_value = float(selected_metric["actual"])
        answer = answer_executive_question(
            question=payload.question,
            snapshot=snapshot,
            deterministic_answer=answer,
            source_status=source_status,
            executive_profile=str(session_state.get("executive_profile", "balanced")),
            conversation=list(session_state.get("transcript", [])),
            required_campaign=campaign["name"] if campaign else None,
            required_value=required_value,
            official_detail=official_detail,
        )
    if not source_status.get("trusted", True):
        answer = f"Advertencia: el corte financiero necesita actualización. {answer}"
    return {
        "answer": answer,
        "campaign": campaign["name"] if campaign else None,
        "data_section": detail_section,
        "action": action,
        "data_source": source_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SESSION CLOSE ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/end-session")
def end_session(payload: CloseSessionRequest) -> dict[str, Any]:
    """SESSION_CLOSE - Generate all 4 post-meeting documents."""
    transcript = [line.model_dump() for line in payload.transcript]
    snapshot = snapshot_for_session(session_state)
    result = close_session(
        meeting_type=payload.meeting_type,
        transcript=transcript,
        alerts=payload.alerts,
        sentiments=payload.sentiments,
        session_started_at=payload.session_started_at,
        session_ended_at=payload.session_ended_at,
        duration_seconds=payload.duration_seconds,
        snapshot=snapshot,
    )
    result["data_source"] = financial_data_service.status()

    # Update session state
    session_state["status"] = "closed"

    return result
