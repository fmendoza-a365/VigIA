from __future__ import annotations

import os
import re
from typing import Any

from google import genai
from google.genai import types

from vigia.official_questions import detail_for_prompt


DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_MS = 12000
DEFAULT_THINKING_BUDGET = 0
MAX_ANSWER_CHARS = 420
MAX_CONTEXT_LINES = 8

SYSTEM_INSTRUCTION = """Eres VIGIA, una directora ejecutiva que participa en una reunión.

Conversas y analizas con criterio de Dirección Financiera y Dirección de Operaciones. Hablas como una colega presente en la sala: escuchas, conectas contexto, cuestionas supuestos y sugieres un plan claro; la decisión siempre queda en las personas responsables.

Reglas obligatorias:
- Si te llaman por tu nombre o preguntan si escuchas o estás ahí, responde de inmediato y con naturalidad en una o dos frases.
- Responde la intención real de la persona, aunque la transcripción no tenga signos de interrogación.
- Para cualquier cifra, estado, cuenta o hecho interno usa exclusivamente los DATOS OFICIALES incluidos en el contexto.
- Nunca inventes datos internos, causas, fechas, responsables ni resultados que no estén en los datos oficiales.
- Puedes explicar conceptos generales y proponer análisis, planes o siguientes pasos. Distingue claramente una recomendación, una inferencia y un dato confirmado.
- Si falta información para responder con certeza, dilo de forma natural y pide el dato mínimo necesario.
- Mantén continuidad con el contexto reciente de la reunión cuando sea relevante.
- Da primero la conclusión ejecutiva y después la razón; no recites todos los indicadores si no hacen falta.
- Habla en primera persona cuando recomiendes una decisión: "yo revisaría", "no aprobaría" o "mi prioridad sería".
- Habla en español latinoamericano natural, cálido, directo y profesional.
- No suenes como base de datos, formulario, reporte, asistente virtual ni chatbot rígido.
- No menciones JSON, prompts, reglas internas ni el modelo.
- Evita markdown, tablas, encabezados, firmas y saludos innecesarios.
- Normalmente responde en 1 a 3 frases y no superes 55 palabras, salvo que la persona pida detalle.
- No repitas la pregunta, no recites todos los indicadores y no reiteres la misma conclusión con otras palabras.
- Redondea cualquier decimal a un máximo de dos posiciones.
- Si piden un plan de acción, usa históricos y relaciones entre finanzas y operación. Propón acciones aplicables con responsable sugerido, plazo, indicador de control y condición para revisar el plan; no decidas por la empresa.
- Si IFC está en pre-cierre o una fuente tiene cobertura parcial o datos pendientes de validar, indícalo y no lo presentes como definitivo.
"""


def answer_executive_question(
    *,
    question: str,
    snapshot: dict[str, Any],
    deterministic_answer: str,
    source_status: dict[str, Any],
    executive_profile: str = "balanced",
    conversation: list[dict[str, Any]] | None = None,
    required_campaign: str | None = None,
    required_value: float | None = None,
    official_detail: dict[str, Any] | None = None,
) -> str:
    """Turn a grounded calculation into a natural executive answer.

    Gemini is the conversational layer. The official snapshot and deterministic
    answer remain the source of truth, and are returned directly on any failure.
    """
    if not assistant_enabled():
        return deterministic_answer

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return deterministic_answer

    prompt = build_prompt(
        question=question,
        snapshot=snapshot,
        deterministic_answer=deterministic_answer,
        source_status=source_status,
        executive_profile=executive_profile,
        conversation=conversation or [],
        official_detail=official_detail,
    )

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=max(10000, env_int("VIGIA_CONVERSATIONAL_TIMEOUT_MS", DEFAULT_TIMEOUT_MS))),
        )
        result = client.models.generate_content(
            model=os.getenv("VIGIA_CONVERSATIONAL_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_MODEL)),
            contents=prompt,
            config=types.GenerateContentConfig(
                systemInstruction=SYSTEM_INSTRUCTION,
                temperature=0.42,
                topP=0.9,
                maxOutputTokens=512,
                thinkingConfig=types.ThinkingConfig(
                    thinkingBudget=thinking_budget(
                        "VIGIA_CONVERSATIONAL_THINKING_BUDGET",
                        DEFAULT_THINKING_BUDGET,
                    ),
                ),
                responseMimeType="text/plain",
            ),
        )
        answer = clean_answer(getattr(result, "text", "") or "")
    except Exception as exc:
        print(f"Conversational assistant unavailable; using grounded fallback: {exc}")
        return deterministic_answer

    if not is_usable_answer(
        answer,
        required_campaign=required_campaign,
        required_value=required_value,
    ):
        return deterministic_answer
    return answer


def build_prompt(
    *,
    question: str,
    snapshot: dict[str, Any],
    deterministic_answer: str,
    source_status: dict[str, Any],
    executive_profile: str,
    conversation: list[dict[str, Any]],
    official_detail: dict[str, Any] | None = None,
) -> str:
    profile = {
        "cfo": "Director Financiero: margen, ingresos, costos, penalidades, riesgo y sostenibilidad.",
        "coo": "Director de Operaciones: capacidad, ejecución, dotación, productividad, riesgo y continuidad.",
        "balanced": "Director Financiero y de Operaciones: conecta impacto económico con ejecución.",
    }.get(executive_profile, "Director Financiero y de Operaciones.")

    data_lines = format_snapshot(snapshot)
    context_lines = [
        f"- {item.get('speaker', 'Sala')}: {str(item.get('text', '')).strip()}"
        for item in conversation[-MAX_CONTEXT_LINES:]
        if str(item.get("text", "")).strip()
    ] or ["- Sin contexto previo relevante."]
    detail_lines = (
        [
            "",
            "DETALLE OFICIAL CONSULTADO PARA ESTA PREGUNTA:",
            detail_for_prompt(official_detail),
        ]
        if official_detail
        else []
    )

    return "\n".join(
        [
            f"Perfil activo: {profile}",
            f"Fuente oficial: {snapshot.get('source', 'seguimiento_financiero')}",
            f"Periodo: {snapshot.get('period', 'no indicado')}",
            f"Fuente confiable: {'sí' if source_status.get('trusted', True) else 'no'}",
            "",
            "DATOS OFICIALES:",
            *data_lines,
            *detail_lines,
            "",
            "CÁLCULO DETERMINISTA DE RESPALDO:",
            deterministic_answer or "No hay un cálculo específico para esta consulta.",
            "",
            "CONTEXTO RECIENTE:",
            *context_lines,
            "",
            f"CONSULTA ACTUAL: {question}",
            "",
            "Responde directamente a la consulta. Conserva exactamente las cifras oficiales cuando las menciones.",
        ]
    )


def format_snapshot(snapshot: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for campaign in snapshot.get("campaigns", []):
        metrics = []
        for metric in campaign.get("metrics", {}).values():
            actual = format_number(metric.get("actual"))
            target = format_number(metric.get("target"))
            direction = "mínimo" if metric.get("direction") == "min" else "máximo"
            metrics.append(
                f"{metric.get('label', 'Indicador')}: actual {actual}{metric.get('unit', '')}, "
                f"meta {direction} {target}{metric.get('unit', '')}"
            )
        lines.append(f"- {campaign.get('name', 'Cuenta')}: " + "; ".join(metrics))
    details = snapshot.get("details", {})
    executive = details.get("executive", {}).get("snapshot", {})
    if isinstance(executive, dict) and executive:
        summary = executive.get("calculatedSummary", {})
        lines.append(
            "- Consolidado histórico: "
            f"ingreso {format_number(summary.get('income'))}; planilla {format_number(summary.get('payroll'))}; "
            f"estructura {format_number(summary.get('structure_cost'))}; margen {format_number(summary.get('gross_margin_pct'))}%; "
            f"cumplimiento presupuestal {format_number(summary.get('budget_execution_pct'))}%."
        )
        for row in executive.get("financialMonthly", [])[-12:]:
            lines.append(
                f"- Histórico {row.get('period')}: ingreso {format_number(row.get('income'))}; "
                f"margen {format_number(row.get('gross_margin_pct'))}%; agentes {format_number(row.get('effective_agents'))}; "
                f"ratio {format_number(row.get('supervision_ratio'))}."
            )
        supervision = executive.get("supervisorAlerts", {})
        if supervision:
            lines.append(f"- Supervisión al corte: {supervision.get('summary', {})}.")
    quality = details.get("data_quality")
    if isinstance(quality, dict):
        lines.append(
            f"- Cobertura y calidad de fuentes: datasets {quality.get('datasets', [])}; "
            f"validación operativa {quality.get('operational_validation', [])}; mapeos SIOP {quality.get('siop_mappings', [])}."
        )
    ifc = details.get("ifc")
    if isinstance(ifc, dict):
        lines.append(
            f"- IFC {ifc.get('period')}: cierre {ifc.get('close')}; consolidado {ifc.get('consolidated')}; "
            f"alertas de cobertura {ifc.get('warnings', [])}."
        )
    return lines or ["- La fuente no contiene cuentas disponibles."]


def clean_answer(value: str) -> str:
    answer = value.strip().strip("\"'`")
    answer = re.sub(r"^\s*(respuesta|vigia)\s*:\s*", "", answer, flags=re.I)
    answer = re.sub(r"\s+", " ", answer).strip()
    if len(answer) > MAX_ANSWER_CHARS:
        cut = answer.rfind(".", 0, MAX_ANSWER_CHARS)
        if cut < 250:
            cut = answer.rfind(" ", 0, MAX_ANSWER_CHARS)
        answer = answer[:cut].rstrip(" ,;:") + "."
    return answer


def is_usable_answer(
    answer: str,
    *,
    required_campaign: str | None,
    required_value: float | None,
) -> bool:
    if len(answer.split()) < 4:
        return False
    normalized = answer.lower().replace(",", ".")
    if required_campaign and required_campaign.lower() not in normalized:
        return False
    if required_value is not None and format_number(required_value) not in normalized:
        return False
    return True


def format_number(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def assistant_enabled() -> bool:
    # External grounding is opt-in because it can send financial context to the
    # configured model provider. Keep the deterministic local fallback active.
    return os.getenv("VIGIA_CONVERSATIONAL_ENABLED", "false").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def thinking_budget(name: str, default: int) -> int:
    """Return a safe Gemini 2.5 thinking budget; -1 enables dynamic reasoning."""
    value = env_int(name, default)
    if value == -1:
        return value
    return max(0, min(value, 24576))
