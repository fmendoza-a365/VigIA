from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.api_core.exceptions import GoogleAPICallError, InvalidArgument, PermissionDenied, Unauthenticated
from pydantic import BaseModel, Field

from vigia.core import analyze_statement, close_session, load_bi_snapshot
from vigia.google_speech import configure_google_credentials, stream_transcribe_opus, transcribe_webm_opus


DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data" / "bi_snapshot.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_AUDIO_BYTES = 8 * 1024 * 1024
STREAM_QUEUE_SIZE = 80

load_dotenv(PROJECT_ROOT / ".env")
GOOGLE_CREDENTIALS_PATH = configure_google_credentials(PROJECT_ROOT)


def snapshot_path() -> Path:
    configured = os.getenv("VIGIA_BI_SNAPSHOT_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_SNAPSHOT_PATH


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
    session_started_at: str | None = None
    session_ended_at: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)


class SpeechStatusResponse(BaseModel):
    configured: bool
    credentials_path: str | None
    language: str
    model: str


app = FastAPI(title="A365 VigIA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/speech-status")
def speech_status() -> SpeechStatusResponse:
    credentials_path = GOOGLE_CREDENTIALS_PATH
    return SpeechStatusResponse(
        configured=credentials_path is not None and credentials_path.exists(),
        credentials_path=str(credentials_path) if credentials_path else None,
        language=os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-PE"),
        model=os.getenv("GOOGLE_SPEECH_MODEL", "latest_long"),
    )


@app.get("/api/bi-snapshot")
def bi_snapshot() -> dict[str, Any]:
    return load_bi_snapshot(snapshot_path())


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
        text, confidence = transcribe_webm_opus(
            content,
            language_code=os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-PE"),
            model=os.getenv("GOOGLE_SPEECH_MODEL", "latest_long"),
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
                model=os.getenv("GOOGLE_SPEECH_MODEL", "latest_long"),
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
        except InvalidArgument:
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


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    snapshot = load_bi_snapshot(snapshot_path())
    result = analyze_statement(
        speaker=payload.speaker,
        role=payload.role,
        text=payload.text,
        snapshot=snapshot,
    )
    return result.to_dict()


@app.post("/api/end-session")
def end_session(payload: CloseSessionRequest) -> dict[str, Any]:
    transcript = [line.model_dump() for line in payload.transcript]
    return close_session(
        meeting_type=payload.meeting_type,
        transcript=transcript,
        alerts=payload.alerts,
        session_started_at=payload.session_started_at,
        session_ended_at=payload.session_ended_at,
        duration_seconds=payload.duration_seconds,
    )
