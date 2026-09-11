from __future__ import annotations

import os
import re
from typing import Any

from google import genai
from google.genai import types

from vigia.core import VigIAResponse
from vigia.meeting_context import format_meeting_memory_for_prompt


DEFAULT_EXECUTIVE_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_MS = 10000
DEFAULT_THINKING_BUDGET = 0
MAX_CONTEXT_LINES = 5
MAX_INTERVENTION_CHARS = 380

SYSTEM_INSTRUCTION = """Eres vigia a365, una directora presente en una reunión ejecutiva.

Actúas con el perfil ejecutivo indicado en el contexto: Director Financiero, Director de Operaciones o perfil mixto. Escuchas, interrumpes cuando una promesa o dato no cuadra, y propones una decisión concreta desde ese rol.

Reglas obligatorias:
- No vuelvas a analizar ni cambies la severidad; el motor ya decidió.
- No inventes números, campañas, causas, fechas, nombres, promesas ni fuentes.
- Usa solo la evidencia, reclamos detectados, mensaje base y acciones permitidas.
- Usa el estado vivo de la reunión para calibrar contexto, emoción y tono; no lo uses para inventar datos.
- Habla en español latinoamericano natural, directo y profesional.
- Sé confrontacional cuando haya riesgo, sin ser agresivo ni teatral.
- Debe sonar como una persona interviniendo en una reunión, no como reporte, plantilla o chatbot.
- Empieza con la objeción o decisión, no con una etiqueta ni con una explicación de tu función.
- Usa transiciones orales naturales y evita enumeraciones mecánicas.
- Incluye objeción clara, dato que sostiene la objeción y decisión recomendada.
- Cuando haya evidencia numérica, menciona al menos una campaña y una cifra exacta.
- Redondea cualquier decimal a un máximo de dos posiciones.
- Entre 18 y 45 palabras. Una objeción, un dato y una decisión; nada más.
- Sin markdown, sin listas, sin JSON, sin saludo y sin firma."""


def enhance_executive_intervention(
    response: VigIAResponse,
    *,
    speaker: str,
    role: str,
    text: str,
    meeting_context: list[dict[str, Any]] | None = None,
    meeting_memory: dict[str, Any] | None = None,
) -> VigIAResponse:
    """Rewrite eligible VigIA responses into natural executive interventions.

    The deterministic response remains the source of truth. If Gemini is not
    configured, unavailable or returns an invalid answer, we keep the original
    message so VigIA never loses the alert.
    """
    if not should_rewrite_response(response):
        return response
    if not writer_enabled():
        return response

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return response

    prompt = build_writer_prompt(
        response,
        speaker=speaker,
        role=role,
        text=text,
        meeting_context=meeting_context or [],
        meeting_memory=meeting_memory,
    )

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(10000, env_int("VIGIA_EXECUTIVE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)),
            ),
        )
        result = client.models.generate_content(
            model=os.getenv("VIGIA_EXECUTIVE_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_EXECUTIVE_MODEL)),
            contents=prompt,
            config=types.GenerateContentConfig(
                systemInstruction=SYSTEM_INSTRUCTION,
                temperature=0.38,
                topP=0.9,
                maxOutputTokens=420,
                thinkingConfig=types.ThinkingConfig(
                    thinkingBudget=thinking_budget(
                        "VIGIA_EXECUTIVE_THINKING_BUDGET",
                        DEFAULT_THINKING_BUDGET,
                    ),
                ),
                responseMimeType="text/plain",
            ),
        )
        rewritten = clean_intervention_text(getattr(result, "text", "") or "")
    except Exception as exc:
        print(f"Executive writer unavailable; using deterministic message: {exc}")
        return response

    if not is_usable_intervention(rewritten, response):
        return response

    response.message = rewritten
    response.executive_summary = response.executive_summary or "Intervención ejecutiva generada sobre evidencia validada."
    return response


def should_rewrite_response(response: VigIAResponse) -> bool:
    if not response.should_respond:
        return False
    if response.severity in {"warning", "critical"}:
        return True
    return response.category in {"executive_suggestion", "suggestion"}


def writer_enabled() -> bool:
    return os.getenv("VIGIA_EXECUTIVE_WRITER_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def build_writer_prompt(
    response: VigIAResponse,
    *,
    speaker: str,
    role: str,
    text: str,
    meeting_context: list[dict[str, Any]],
    meeting_memory: dict[str, Any] | None = None,
) -> str:
    evidence_lines = [
        (
            f"- {item.campaign}: {item.metric} actual {item.actual:g}{item.unit}, "
            f"meta {item.target:g}{item.unit}, regla {item.direction}, fuente {item.source}, "
            f"fecha {item.generated_at}"
        )
        for item in response.evidence[:6]
    ] or ["- Sin evidencia numérica adicional; usa el mensaje base sin inventar."]

    claim_lines = [
        (
            f"- Campaña: {claim.campaign_name or 'no identificada'}; "
            f"métrica: {claim.metric_label or 'no identificada'}; "
            f"valor dicho: {format_optional_number(claim.spoken_value)}; "
            f"postura: {claim.stance}; frase: {claim.raw_text}"
        )
        for claim in response.detected_claims[:6]
    ] or ["- No hay reclamos estructurados; usa la frase escuchada y el mensaje base."]

    action_lines = [
        f"- {action}"
        for action in response.recommended_actions[:5]
    ] or ["- Convertir el acuerdo en responsable, fecha, métrica de éxito y validación con datos."]

    context_lines = [
        f"- {item.get('speaker', 'Participante')}: {item.get('text', '')}"
        for item in meeting_context[-MAX_CONTEXT_LINES:]
        if item.get("text")
    ] or ["- Sin contexto previo relevante."]
    meeting_memory_lines = format_meeting_memory_for_prompt(meeting_memory)
    profile_labels = {
        "cfo": "Director Financiero: prioriza margen, EBITDA, caja, forecast, costo y exposición contractual.",
        "coo": "Director de Operaciones: prioriza SLA, capacidad, productividad, dotación, backlog y continuidad.",
        "balanced": "Perfil mixto: conecta impacto financiero con capacidad y ejecución operativa.",
    }
    profile_instruction = profile_labels.get(response.executive_profile, profile_labels["balanced"])

    return "\n".join(
        [
            "Redacta la intervención final de vigia a365 para decirla en voz alta.",
            "",
            f"Participante que habló: {speaker} ({role})",
            f"Frase escuchada: {text}",
            f"Categoría: {response.category}",
            f"Severidad: {response.severity}",
            f"Perfil ejecutivo: {profile_instruction}",
            f"Resumen ejecutivo interno: {response.executive_summary or 'No definido'}",
            f"Mensaje base validado: {response.message}",
            "",
            "Evidencia disponible:",
            *evidence_lines,
            "",
            "Reclamos detectados:",
            *claim_lines,
            "",
            "Acciones permitidas:",
            *action_lines,
            "",
            *meeting_memory_lines,
            "",
            "Contexto reciente de la reunión:",
            *context_lines,
            "",
            "Devuelve solo la intervención final, sin firma. Usa entre 18 y 45 palabras.",
        ]
    )


def clean_intervention_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = cleaned.strip("\"'`")
    cleaned = re.sub(r"^\s*(respuesta|intervenci[oó]n|alerta|vigia a365)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s*atentamente,\s*vigia\s*a365\.?\s*$", "", cleaned, flags=re.I).strip()
    if len(cleaned) > MAX_INTERVENTION_CHARS:
        cut = cleaned.rfind(".", 0, MAX_INTERVENTION_CHARS)
        if cut < 180:
            cut = cleaned.rfind(" ", 0, MAX_INTERVENTION_CHARS)
        cleaned = cleaned[:cut].rstrip(" ,;:") + "."
    return cleaned


def is_usable_intervention(text: str, response: VigIAResponse) -> bool:
    if len(text.split()) < 7:
        return False
    if not response.evidence:
        return True

    normalized = text.lower().replace(",", ".")
    has_campaign = any(item.campaign.lower() in normalized for item in response.evidence[:4])
    has_number = any(
        format_number_variant(item.actual) in normalized or format_number_variant(item.target) in normalized
        for item in response.evidence[:4]
    )
    return has_campaign and has_number


def format_number_variant(value: float) -> str:
    return f"{value:g}".lower()


def format_optional_number(value: float | None) -> str:
    if value is None:
        return "no mencionado"
    return f"{value:g}"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def thinking_budget(name: str, default: int) -> int:
    value = env_int(name, default)
    if value == -1:
        return value
    return max(0, min(value, 24576))
