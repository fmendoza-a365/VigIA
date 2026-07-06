from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


METRIC_ALIASES = {
    "sla": [
        "sla",
        "nivel de servicio",
        "servicio contractual",
        "nivel contractual",
    ],
    "gross_margin": [
        "margen",
        "margen bruto",
        "gross margin",
        "rentabilidad",
    ],
    "ebitda": [
        "ebitda",
        "ebida",
        "utilidad operativa",
    ],
    "attrition": [
        "rotacion",
        "rotación",
        "atricion",
        "atrición",
        "attrition",
        "bajas",
    ],
}

POSITIVE_ASSERTIONS = [
    "estable",
    "normal",
    "bien",
    "cumplimos",
    "cumple",
    "dentro de rango",
    "sin riesgo",
    "controlado",
    "por encima",
    "sobre la meta",
    "no hay problema",
]

NEGATIVE_ASSERTIONS = [
    "critico",
    "critico",
    "riesgo",
    "debajo",
    "por debajo",
    "incumplimos",
    "incumple",
    "fuera de rango",
    "caida",
    "caída",
]


@dataclass
class DetectedClaim:
    campaign_id: str | None
    campaign_name: str | None
    metric_key: str | None
    metric_label: str | None
    spoken_value: float | None
    stance: str
    raw_text: str


@dataclass
class Evidence:
    source: str
    generated_at: str
    campaign: str
    metric: str
    actual: float
    target: float
    direction: str
    unit: str


@dataclass
class AnalysisResult:
    should_respond: bool
    severity: str
    category: str
    message: str
    detected_claims: list[DetectedClaim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_bi_snapshot(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def analyze_statement(
    *,
    speaker: str,
    role: str,
    text: str,
    snapshot: dict[str, Any],
) -> AnalysisResult:
    claims = detect_claims(text, snapshot)
    evidence: list[Evidence] = []
    alert_messages: list[str] = []
    highest_severity = "silent"

    for claim in claims:
        metric = resolve_metric(snapshot, claim)
        if not metric:
            continue

        ev = Evidence(
            source=snapshot.get("source", "Fuente BI"),
            generated_at=snapshot.get("generated_at", "sin fecha"),
            campaign=claim.campaign_name or "Campana no identificada",
            metric=metric["label"],
            actual=float(metric["actual"]),
            target=float(metric["target"]),
            direction=metric["direction"],
            unit=metric["unit"],
        )
        evidence.append(ev)

        metric_ok = is_metric_compliant(ev.actual, ev.target, ev.direction)
        value_conflict = claim.spoken_value is not None and abs(claim.spoken_value - ev.actual) >= 1.0
        disguised_risk = claim.stance == "positive" and not metric_ok

        if disguised_risk:
            highest_severity = max_severity(highest_severity, "critical")
            alert_messages.append(
                f"{claim.campaign_name}: se presento {ev.metric} como controlado, "
                f"pero BI reporta {ev.actual:g}{ev.unit} contra meta {ev.target:g}{ev.unit}."
            )
        elif value_conflict:
            highest_severity = max_severity(highest_severity, "warning")
            alert_messages.append(
                f"{claim.campaign_name}: el valor mencionado ({claim.spoken_value:g}{ev.unit}) "
                f"no coincide con BI ({ev.actual:g}{ev.unit})."
            )
        elif not metric_ok and claim.stance != "negative":
            highest_severity = max_severity(highest_severity, "warning")
            alert_messages.append(
                f"{claim.campaign_name}: {ev.metric} esta fuera de umbral "
                f"({ev.actual:g}{ev.unit} vs {ev.target:g}{ev.unit})."
            )

    if not alert_messages:
        return AnalysisResult(
            should_respond=False,
            severity="silent",
            category="smart_silence",
            message="Sin intervencion: no se detecto contradiccion verificable.",
            detected_claims=claims,
            evidence=evidence,
        )

    prefix = "ALERTA" if highest_severity == "critical" else "REVISION"
    message = (
        f"{prefix}: {speaker} menciono un dato que requiere contraste. "
        + " ".join(alert_messages)
        + " Recomendacion: validar el corte BI antes de tomar una decision."
    )

    return AnalysisResult(
        should_respond=True,
        severity=highest_severity,
        category="business_fact_check",
        message=message,
        detected_claims=claims,
        evidence=evidence,
    )


def close_session(
    *,
    meeting_type: str,
    transcript: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    session_started_at: str | None = None,
    session_ended_at: str | None = None,
    duration_seconds: int | None = None,
) -> dict[str, Any]:
    decisions = [
        item
        for item in transcript
        if has_any(item.get("text", ""), ["decision", "decidimos", "acordamos", "compromiso"])
    ]
    risks = [
        item
        for item in transcript
        if has_any(item.get("text", ""), ["riesgo", "critico", "incumple", "penalidad", "caida", "caída"])
    ]

    return {
        "meeting_type": meeting_type,
        "summary": {
            "transcript_lines": len(transcript),
            "alerts": len(alerts),
            "decisions": len(decisions),
            "risks": len(risks),
            "duration_seconds": duration_seconds,
        },
        "session": {
            "started_at": session_started_at,
            "ended_at": session_ended_at,
            "duration_seconds": duration_seconds,
            "duration_label": format_duration_seconds(duration_seconds),
        },
        "executive_minutes": build_minutes(
            meeting_type,
            transcript,
            decisions,
            risks,
            alerts,
            session_started_at=session_started_at,
            session_ended_at=session_ended_at,
            duration_seconds=duration_seconds,
        ),
        "decisions": decisions,
        "risks": risks,
    }


def detect_claims(text: str, snapshot: dict[str, Any]) -> list[DetectedClaim]:
    normalized = normalize(text)
    metric_key = detect_metric(normalized)
    if not metric_key:
        return []

    campaigns = detect_campaigns(normalized, snapshot)
    if not campaigns:
        campaigns = [(None, None)]

    spoken_value = detect_percentage(normalized)
    stance = detect_stance(normalized)

    return [
        DetectedClaim(
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            metric_key=metric_key,
            metric_label=metric_label(metric_key),
            spoken_value=spoken_value,
            stance=stance,
            raw_text=text,
        )
        for campaign_id, campaign_name in campaigns
    ]


def detect_metric(normalized_text: str) -> str | None:
    for key, aliases in METRIC_ALIASES.items():
        if any(alias in normalized_text for alias in aliases):
            return key
    return None


def detect_campaigns(normalized_text: str, snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for campaign in snapshot.get("campaigns", []):
        campaign_id = str(campaign["id"]).lower()
        campaign_name = str(campaign["name"])
        if campaign_id in normalized_text or normalize(campaign_name) in normalized_text:
            found.append((campaign["id"], campaign_name))
    return found


def detect_percentage(normalized_text: str) -> float | None:
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:%|por ciento|puntos)", normalized_text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def detect_stance(normalized_text: str) -> str:
    if has_any(normalized_text, POSITIVE_ASSERTIONS):
        return "positive"
    if has_any(normalized_text, NEGATIVE_ASSERTIONS):
        return "negative"
    return "neutral"


def resolve_metric(snapshot: dict[str, Any], claim: DetectedClaim) -> dict[str, Any] | None:
    if not claim.campaign_id or not claim.metric_key:
        return None
    for campaign in snapshot.get("campaigns", []):
        if campaign["id"] == claim.campaign_id:
            return campaign["metrics"].get(claim.metric_key)
    return None


def is_metric_compliant(actual: float, target: float, direction: str) -> bool:
    if direction == "min":
        return actual >= target
    if direction == "max":
        return actual <= target
    raise ValueError(f"Unknown metric direction: {direction}")


def build_minutes(
    meeting_type: str,
    transcript: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    *,
    session_started_at: str | None = None,
    session_ended_at: str | None = None,
    duration_seconds: int | None = None,
) -> str:
    lines = [
        f"# Minuta VigIA - {meeting_type}",
        "",
        "## Resumen",
        f"- Duracion de sesion: {format_duration_seconds(duration_seconds)}",
        f"- Lineas de transcripcion: {len(transcript)}",
        f"- Alertas generadas: {len(alerts)}",
        f"- Decisiones detectadas: {len(decisions)}",
        f"- Riesgos detectados: {len(risks)}",
        "",
        "## Alertas principales",
    ]

    if alerts:
        for alert in alerts[:5]:
            lines.append(f"- {alert.get('message', 'Alerta sin mensaje')}")
    else:
        lines.append("- Sin alertas registradas.")

    lines.extend(["", "## Decisiones"])
    if decisions:
        for item in decisions:
            lines.append(f"- {item.get('speaker', 'Participante')}: {item.get('text', '')}")
    else:
        lines.append("- No se detectaron decisiones explicitas.")

    lines.extend(["", "## Riesgos"])
    if risks:
        for item in risks:
            lines.append(f"- {item.get('speaker', 'Participante')}: {item.get('text', '')}")
    else:
        lines.append("- No se detectaron riesgos explicitos.")

    lines.extend(["", "## Sesion"])
    if session_started_at:
        lines.append(f"- Inicio: {session_started_at}")
    if session_ended_at:
        lines.append(f"- Cierre: {session_ended_at}")
    lines.append(f"- Duracion: {format_duration_seconds(duration_seconds)}")

    return "\n".join(lines)


def format_duration_seconds(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return "no registrada"
    total_seconds = max(0, duration_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def metric_label(metric_key: str) -> str:
    labels = {
        "sla": "SLA contractual",
        "gross_margin": "Margen bruto",
        "ebitda": "EBITDA",
        "attrition": "Rotacion",
    }
    return labels[metric_key]


def max_severity(current: str, candidate: str) -> str:
    rank = {"silent": 0, "info": 1, "warning": 2, "critical": 3}
    return candidate if rank[candidate] > rank[current] else current


def has_any(text: str, needles: list[str]) -> bool:
    normalized_text = normalize(text)
    return any(normalize(needle) in normalized_text for needle in needles)


def normalize(value: str) -> str:
    return value.lower().strip()
