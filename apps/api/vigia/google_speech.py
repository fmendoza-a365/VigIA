from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import speech


def configure_google_credentials(project_root: Path) -> Path | None:
    configured = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if configured:
        return Path(configured).expanduser().resolve()

    local_credentials = project_root / ".secrets" / "google-speech.json"
    if local_credentials.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(local_credentials)
        return local_credentials

    return None


def transcribe_webm_opus(
    audio_content: bytes,
    *,
    language_code: str = "es-PE",
    model: str = "default",
    phrase_hints: Iterable[str] | None = None,
) -> tuple[str, float | None]:
    client = speech.SpeechClient()
    config = recognition_config(
        encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        language_code=language_code,
        model=model,
        phrase_hints=phrase_hints,
    )
    audio = speech.RecognitionAudio(content=audio_content)

    try:
        response = client.recognize(config=config, audio=audio)
    except GoogleAPICallError:
        raise

    texts: list[str] = []
    confidence_values: list[float] = []
    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        if alternative.transcript:
            texts.append(alternative.transcript.strip())
        if alternative.confidence:
            confidence_values.append(float(alternative.confidence))

    confidence = None
    if confidence_values:
        confidence = sum(confidence_values) / len(confidence_values)

    return " ".join(texts).strip(), confidence


def encoding_from_mime_type(mime_type: str) -> speech.RecognitionConfig.AudioEncoding:
    if "ogg" in mime_type:
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS


def recognition_config(
    *,
    encoding: speech.RecognitionConfig.AudioEncoding,
    language_code: str,
    model: str,
    phrase_hints: Iterable[str] | None = None,
) -> speech.RecognitionConfig:
    hints = list(dict.fromkeys(
        phrase.strip()
        for phrase in (phrase_hints or [])
        if phrase and phrase.strip()
    ))[:300]
    try:
        hint_boost = min(20.0, max(0.0, float(os.getenv("GOOGLE_SPEECH_HINT_BOOST", "16"))))
    except ValueError:
        hint_boost = 16.0

    return speech.RecognitionConfig(
        encoding=encoding,
        sample_rate_hertz=48000,
        language_code=language_code,
        model=model,
        enable_automatic_punctuation=True,
        speech_contexts=[speech.SpeechContext(phrases=hints, boost=hint_boost)] if hints else [],
    )


def stream_transcribe_opus(
    audio_chunks: Iterable[bytes],
    *,
    mime_type: str,
    language_code: str = "es-PE",
    model: str = "default",
    phrase_hints: Iterable[str] | None = None,
) -> Iterator[tuple[str, bool, float | None]]:
    client = speech.SpeechClient()
    config = recognition_config(
        encoding=encoding_from_mime_type(mime_type),
        language_code=language_code,
        model=model,
        phrase_hints=phrase_hints,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
        single_utterance=False,
    )

    def requests() -> Iterator[speech.StreamingRecognizeRequest]:
        for chunk in audio_chunks:
            if chunk:
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

    try:
        responses = client.streaming_recognize(config=streaming_config, requests=requests())
        for response in responses:
            for result in response.results:
                if not result.alternatives:
                    continue
                alternative = result.alternatives[0]
                transcript = alternative.transcript.strip()
                if not transcript:
                    continue
                confidence = float(alternative.confidence) if alternative.confidence else None
                yield transcript, bool(result.is_final), confidence
    except GoogleAPICallError:
        raise
