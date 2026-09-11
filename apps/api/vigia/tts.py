from __future__ import annotations

import base64
import html
import io
import os
import re
import wave
from dataclasses import dataclass

from google import genai
from google.cloud import texttospeech


DEFAULT_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_GEMINI_TTS_VOICE = "Achernar"
DEFAULT_GEMINI_TTS_LANGUAGE = "es-419"
DEFAULT_CLOUD_TTS_LANGUAGE = "es-US"
DEFAULT_CLOUD_TTS_VOICE = "es-US-Chirp3-HD-Orus"
DEFAULT_TTS_STYLE = (
    "Habla en español latinoamericano como una directora financiera y de operaciones "
    "conversando con otros directores en una reunión real. Usa una voz cálida, segura "
    "y cercana, con variaciones sutiles de entonación, ritmo humano y pausas naturales. "
    "Da énfasis moderado a la conclusión y a las cifras importantes, sin sonar como "
    "locutora, anuncio, lectura de reporte ni asistente virtual. Nunca pronuncies marcas "
    "de formato como asterisco, numeral, guion de lista o URL."
)


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    content_type: str
    provider: str
    voice: str


def synthesize_tts(
    *,
    text: str,
    provider: str | None = None,
    voice_name: str | None = None,
    cloud_voice_name: str | None = None,
    speaking_rate: float = 1.05,
) -> TTSResult:
    spoken_text = normalize_text_for_speech(text)
    if not spoken_text:
        raise ValueError("Text does not contain speakable content")

    selected_provider = (provider or os.getenv("VIGIA_TTS_PROVIDER", "gemini-cloud")).lower()

    if selected_provider in {"gemini-cloud", "cloud-gemini"}:
        try:
            return synthesize_gemini_cloud_tts(text=spoken_text, voice_name=voice_name)
        except Exception as exc:
            print(f"Cloud Gemini TTS failed; trying Gemini API fallback: {exc}")
        try:
            return synthesize_gemini_tts(text=spoken_text, voice_name=voice_name)
        except Exception as exc:
            print(f"Gemini API TTS failed; falling back to Chirp TTS: {exc}")
        return synthesize_cloud_tts(
            text=spoken_text,
            voice_name=cloud_voice_name,
            speaking_rate=speaking_rate,
        )

    if selected_provider == "gemini":
        try:
            return synthesize_gemini_tts(text=spoken_text, voice_name=voice_name)
        except Exception as exc:
            print(f"Gemini TTS failed; falling back to Cloud TTS: {exc}")

    return synthesize_cloud_tts(
        text=spoken_text,
        voice_name=cloud_voice_name,
        speaking_rate=speaking_rate,
    )


def normalize_text_for_speech(text: str) -> str:
    """Convert display-oriented Markdown and symbols into natural spoken text."""
    value = html.unescape(str(text or ""))
    value = re.sub(r"```(?:[^\n`]*)\n?(.*?)```", r"\1", value, flags=re.S)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)

    # Financial units are frequently stored as display suffixes, for example
    # "20138S/". Reorder them so both TTS providers read them naturally.
    # Accept common Spanish and English grouping/decimal styles without
    # swallowing the sentence-ending period after an amount.
    number = r"[-+]?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?"
    value = re.sub(rf"S/\s*({number})", r"\1 soles", value, flags=re.I)
    value = re.sub(rf"({number})\s*S/", r"\1 soles", value, flags=re.I)
    value = re.sub(rf"({number})\s*PEN\b", r"\1 soles", value, flags=re.I)
    value = re.sub(rf"({number})\s*%", r"\1 por ciento", value)
    value = value.replace("&", " y ")
    value = re.sub(r"[✓✔]", " cumple ", value)
    value = re.sub(r"[✗✘]", " no cumple ", value)

    spoken_lines: list[str] = []
    for raw_line in value.splitlines():
        line = re.sub(r"^\s{0,3}(?:#{1,6}\s*|[-*+•]\s+|>\s*)", "", raw_line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        line = re.sub(r"[*_~`#]+", "", line)
        line = re.sub(r"[|]+", ", ", line)
        line = strip_emoji(line)
        line = re.sub(r"\s+", " ", line).strip(" ,")
        if not line:
            continue
        if not re.search(r"[.!?;:]$", line):
            line += "."
        spoken_lines.append(line)

    return re.sub(r"\s+", " ", " ".join(spoken_lines)).strip()


def strip_emoji(value: str) -> str:
    return re.sub(
        "["
        "\U0001F1E6-\U0001F1FF"
        "\U0001F300-\U0001FAFF"
        "\U00002700-\U000027BF"
        "\U0000FE0F"
        "\U0000200D"
        "]+",
        " ",
        value,
    )


def synthesize_gemini_cloud_tts(*, text: str, voice_name: str | None = None) -> TTSResult:
    """Use the promptable Gemini model through Cloud Text-to-Speech."""
    model = os.getenv("VIGIA_GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
    voice = voice_name or os.getenv("VIGIA_GEMINI_TTS_VOICE", DEFAULT_GEMINI_TTS_VOICE)
    language = os.getenv("VIGIA_GEMINI_TTS_LANGUAGE", DEFAULT_GEMINI_TTS_LANGUAGE)
    style = os.getenv("VIGIA_TTS_STYLE", DEFAULT_TTS_STYLE)

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text, prompt=style)
    voice_config = texttospeech.VoiceSelectionParams(
        language_code=language,
        name=voice,
        model_name=model,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
    )
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice_config,
        audio_config=audio_config,
    )
    return TTSResult(
        audio=response.audio_content,
        content_type="audio/mp3",
        provider="google-cloud-gemini-tts",
        voice=voice,
    )


def synthesize_gemini_tts(*, text: str, voice_name: str | None = None) -> TTSResult:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not configured")

    model = os.getenv("VIGIA_GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
    voice = voice_name or os.getenv("VIGIA_GEMINI_TTS_VOICE", DEFAULT_GEMINI_TTS_VOICE)
    style = os.getenv("VIGIA_TTS_STYLE", DEFAULT_TTS_STYLE)
    prompt = f"{style}\n\nTexto:\n{text}"

    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(
        model=model,
        input=prompt,
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": voice},
            ],
        },
    )

    output_audio = getattr(interaction, "output_audio", None)
    audio_data = getattr(output_audio, "data", None)
    if not audio_data:
        raise RuntimeError("Gemini TTS did not return audio data")

    pcm = base64.b64decode(audio_data)
    return TTSResult(
        audio=pcm_to_wav(pcm),
        content_type="audio/wav",
        provider="gemini",
        voice=voice,
    )


def synthesize_cloud_tts(
    *,
    text: str,
    voice_name: str | None = None,
    speaking_rate: float = 1.05,
) -> TTSResult:
    language_code = os.getenv("VIGIA_CLOUD_TTS_LANGUAGE", DEFAULT_CLOUD_TTS_LANGUAGE)
    voice = voice_name or os.getenv("VIGIA_CLOUD_TTS_VOICE", DEFAULT_CLOUD_TTS_VOICE)

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice_config = texttospeech.VoiceSelectionParams(
        language_code=language_code,
        name=voice,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
        effects_profile_id=["headphone-class-device"],
    )
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice_config,
        audio_config=audio_config,
    )

    return TTSResult(
        audio=response.audio_content,
        content_type="audio/mp3",
        provider="google-cloud-tts",
        voice=voice,
    )


def pcm_to_wav(
    pcm: bytes,
    *,
    channels: int = 1,
    sample_rate: int = 24000,
    sample_width: int = 2,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()
