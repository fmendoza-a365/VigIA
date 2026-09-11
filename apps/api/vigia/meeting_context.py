from __future__ import annotations

from collections import Counter
from typing import Any

from vigia.core import (
    DECISION_WORDS,
    PROMISE_WORDS,
    RISK_WORDS,
    detect_campaigns,
    detect_metric,
    find_noncompliant_metrics,
    has_any,
    is_critical_metric,
    metric_label,
    normalize,
)


SENTIMENT_LABELS = {
    "😟": "preocupacion",
    "😤": "frustracion",
    "😐": "neutral",
    "🟢": "confianza",
    "⚡": "tension o conflicto",
}

TENSION_EMOJIS = {"😟", "😤", "⚡"}
MAX_RECENT_LINES = 8
MAX_RECENT_ALERTS = 5
MAX_BI_RISKS = 6


def empty_meeting_memory() -> dict[str, Any]:
    return {
        "active_campaigns": [],
        "active_metrics": [],
        "emotional_state": {
            "latest": None,
            "latest_label": "sin datos",
            "dominant": None,
            "dominant_label": "sin datos",
            "tension_count": 0,
            "reading": "sin senales emocionales suficientes",
        },
        "recent_transcript": [],
        "recent_alerts": [],
        "bi_risks": [],
        "meeting_signals": {
            "decisions": [],
            "promises": [],
            "risks": [],
        },
    }


def build_meeting_memory(
    *,
    session_state: dict[str, Any],
    snapshot: dict[str, Any],
    current_line: dict[str, Any] | None = None,
    current_sentiment: str | None = None,
    current_alert: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a live executive context packet for Gemini.

    This is session memory, not model training. It summarizes what has been
    said, the emotional climate and the real BI risks that are allowed as
    grounding for the next intervention.
    """
    transcript = list(session_state.get("transcript", []))
    if current_line and current_line.get("text"):
        transcript.append(current_line)

    sentiments = list(session_state.get("sentiments", []))
    if current_sentiment:
        sentiments.append({"emoji": current_sentiment, "text": (current_line or {}).get("text", "")})

    alerts = list(session_state.get("alerts", []))
    if current_alert:
        alerts.append(current_alert)

    recent_transcript = transcript[-MAX_RECENT_LINES:]
    active_campaigns = extract_active_campaigns(recent_transcript, snapshot)
    active_metrics = extract_active_metrics(recent_transcript, snapshot)
    emotional_state = build_emotional_state(sentiments)
    bi_risks = build_bi_risks(snapshot, active_campaigns=active_campaigns)
    recent_alerts = build_recent_alerts(alerts)
    meeting_signals = build_meeting_signals(recent_transcript)

    return {
        "active_campaigns": active_campaigns,
        "active_metrics": active_metrics,
        "emotional_state": emotional_state,
        "recent_transcript": [
            {
                "speaker": item.get("speaker", "Participante"),
                "role": item.get("role", "Invitado"),
                "text": item.get("text", ""),
            }
            for item in recent_transcript
            if item.get("text")
        ],
        "recent_alerts": recent_alerts,
        "bi_risks": bi_risks,
        "meeting_signals": meeting_signals,
    }


def extract_active_campaigns(transcript: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[str]:
    active: list[str] = []
    for item in reversed(transcript):
        text = normalize(str(item.get("text", "")))
        for _, campaign_name in detect_campaigns(text, snapshot):
            if campaign_name not in active:
                active.append(campaign_name)
        if len(active) >= 3:
            break
    return active[:3]


def extract_active_metrics(
    transcript: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> list[str]:
    active: list[str] = []
    for item in reversed(transcript):
        metric_key = detect_metric(normalize(str(item.get("text", ""))), snapshot)
        if metric_key:
            label = snapshot_metric_label(snapshot, metric_key)
            if label not in active:
                active.append(label)
        if len(active) >= 4:
            break
    return active[:4]


def snapshot_metric_label(snapshot: dict[str, Any], metric_key: str) -> str:
    for campaign in snapshot.get("campaigns", []):
        metric = campaign.get("metrics", {}).get(metric_key)
        if metric and metric.get("label"):
            return str(metric["label"])
    return metric_label(metric_key)


def build_emotional_state(sentiments: list[dict[str, Any]]) -> dict[str, Any]:
    emojis = [item.get("emoji") for item in sentiments if item.get("emoji")]
    if not emojis:
        return empty_meeting_memory()["emotional_state"]

    counts = Counter(emojis)
    dominant = counts.most_common(1)[0][0]
    latest = emojis[-1]
    recent_tension_count = sum(1 for emoji in emojis[-8:] if emoji in TENSION_EMOJIS)
    if recent_tension_count >= 3:
        reading = "tension alta: intervenir directo, pero bajar friccion"
    elif latest in TENSION_EMOJIS:
        reading = "tension puntual: sostener la decision con datos y tono calmo"
    elif latest == "🟢":
        reading = "tono confiado: validar entusiasmo contra BI antes de prometer"
    else:
        reading = "tono estable: mantener intervencion breve y ejecutiva"

    return {
        "latest": latest,
        "latest_label": SENTIMENT_LABELS.get(latest, "no clasificado"),
        "dominant": dominant,
        "dominant_label": SENTIMENT_LABELS.get(dominant, "no clasificado"),
        "tension_count": recent_tension_count,
        "reading": reading,
    }


def build_bi_risks(snapshot: dict[str, Any], *, active_campaigns: list[str]) -> list[dict[str, Any]]:
    active_set = set(active_campaigns)
    campaign_filter = None
    if active_set:
        campaign_filter = {
            str(campaign["id"])
            for campaign in snapshot.get("campaigns", [])
            if str(campaign.get("name")) in active_set
        }
    issues = find_noncompliant_metrics(snapshot, campaign_ids=campaign_filter)
    issues.sort(key=lambda item: 0 if is_critical_metric(item) else 1)
    return [
        {
            "campaign": issue.campaign,
            "metric": issue.metric,
            "actual": issue.actual,
            "target": issue.target,
            "unit": issue.unit,
            "severity": "critical" if is_critical_metric(issue) else "warning",
        }
        for issue in issues[:MAX_BI_RISKS]
    ]


def build_recent_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = []
    for alert in alerts[-MAX_RECENT_ALERTS:]:
        recent.append(
            {
                "severity": alert.get("severity", "info"),
                "category": alert.get("category", "analysis"),
                "message": truncate(str(alert.get("message", "")), 180),
            }
        )
    return recent


def build_meeting_signals(transcript: list[dict[str, Any]]) -> dict[str, list[str]]:
    decisions: list[str] = []
    promises: list[str] = []
    risks: list[str] = []
    for item in transcript:
        text = str(item.get("text", ""))
        if has_any(text, DECISION_WORDS):
            decisions.append(truncate(text, 150))
        if has_any(text, PROMISE_WORDS):
            promises.append(truncate(text, 150))
        if has_any(text, RISK_WORDS):
            risks.append(truncate(text, 150))
    return {
        "decisions": decisions[-3:],
        "promises": promises[-3:],
        "risks": risks[-3:],
    }


def format_meeting_memory_for_prompt(memory: dict[str, Any] | None) -> list[str]:
    if not memory:
        memory = empty_meeting_memory()

    emotional = memory.get("emotional_state", {})
    lines = [
        "Estado vivo de la reunion:",
        f"- Campanas activas: {join_or_none(memory.get('active_campaigns'))}",
        f"- Metricas activas: {join_or_none(memory.get('active_metrics'))}",
        (
            "- Clima emocional: "
            f"ultimo {emotional.get('latest_label', 'sin datos')}; "
            f"dominante {emotional.get('dominant_label', 'sin datos')}; "
            f"tension reciente {emotional.get('tension_count', 0)}; "
            f"lectura {emotional.get('reading', 'sin lectura')}"
        ),
        "- Riesgos BI vigentes:",
    ]

    bi_risks = memory.get("bi_risks") or []
    if bi_risks:
        lines.extend(
            f"  - {risk['campaign']} {risk['metric']}: {risk['actual']:g}{risk['unit']} vs meta {risk['target']:g}{risk['unit']} ({risk['severity']})"
            for risk in bi_risks[:MAX_BI_RISKS]
        )
    else:
        lines.append("  - Sin brechas BI relevantes para el foco actual.")

    recent_alerts = memory.get("recent_alerts") or []
    lines.append("- Alertas recientes:")
    if recent_alerts:
        lines.extend(
            f"  - {alert['severity']} / {alert['category']}: {alert['message']}"
            for alert in recent_alerts[-MAX_RECENT_ALERTS:]
        )
    else:
        lines.append("  - Sin alertas previas en la sesion.")

    signals = memory.get("meeting_signals") or {}
    lines.extend(
        [
            f"- Decisiones detectadas: {join_or_none(signals.get('decisions'))}",
            f"- Promesas detectadas: {join_or_none(signals.get('promises'))}",
            f"- Riesgos conversados: {join_or_none(signals.get('risks'))}",
            "- Ultimas frases:",
        ]
    )

    recent_transcript = memory.get("recent_transcript") or []
    if recent_transcript:
        lines.extend(
            f"  - {item.get('speaker', 'Participante')} ({item.get('role', 'Invitado')}): {truncate(str(item.get('text', '')), 180)}"
            for item in recent_transcript[-MAX_RECENT_LINES:]
        )
    else:
        lines.append("  - Sin frases previas.")

    return lines


def join_or_none(items: list[Any] | None) -> str:
    values = [str(item) for item in (items or []) if str(item).strip()]
    return "; ".join(values) if values else "sin datos"


def truncate(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,;:") + "..."
