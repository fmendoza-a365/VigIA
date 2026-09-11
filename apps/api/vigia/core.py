from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS CONTEXT & THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_NAME = "A365 Peru"
SIGNATURE = "Atentamente, vigia a365."

CRITICAL_THRESHOLDS = {
    "gross_margin": {"threshold": 20.0, "direction": "min", "unit": "%"},
    "ebitda": {"threshold": 10.0, "direction": "min", "unit": "%"},
    "attrition": {"threshold": 8.0, "direction": "max", "unit": "%"},
    "revenue_drop_mom": {"threshold": 15.0, "direction": "max", "unit": "%"},
}

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
    "tmo": [
        "tmo",
        "tiempo medio",
        "tiempo promedio",
    ],
    "occupancy": [
        "ocupacion",
        "ocupación",
        "occupancy",
    ],
    "facturacion": [
        "facturacion",
        "facturación",
        "ingresos",
        "revenue",
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
    "no hay riesgo",
    "ningun riesgo",
    "no existe riesgo",
    "cero riesgo",
    "controlado",
    "por encima",
    "sobre la meta",
    "no hay problema",
    "saludable",
    "positivo",
]

NEGATIVE_ASSERTIONS = [
    "critico",
    "crítico",
    "riesgo",
    "debajo",
    "por debajo",
    "incumplimos",
    "incumple",
    "fuera de rango",
    "fuera de meta",
    "no cumple la meta",
    "no llega a la meta",
    "caida",
    "caída",
    "problema",
    "preocupante",
]

CONFLICT_WORDS = [
    "no estoy de acuerdo",
    "eso no es correcto",
    "estás equivocado",
    "estas equivocado",
    "eso es mentira",
    "no puede ser",
    "es inaceptable",
    "están peleando",
    "se están atacando",
    "basta",
]

DECISION_WORDS = [
    "decision",
    "decisión",
    "decidimos",
    "acordamos",
    "compromiso",
    "aprobado",
    "vamos a hacer",
    "se aprueba",
]

RISK_WORDS = [
    "riesgo",
    "critico",
    "crítico",
    "incumple",
    "penalidad",
    "caida",
    "caída",
    "multa",
    "sancion",
]

PROMISE_WORDS = [
    "prometo",
    "prometemos",
    "garantizo",
    "garantizamos",
    "aseguro",
    "aseguramos",
    "me comprometo",
    "nos comprometemos",
    "vamos a llegar",
    "vamos a cumplir",
    "cerramos en",
    "cerrar en",
    "llegaremos",
    "recuperaremos",
    "sin problema llegamos",
]

BROAD_HEALTH_WORDS = [
    "todo esta bien",
    "todo está bien",
    "todo bajo control",
    "sin problemas",
    "no hay riesgo",
    "estamos bien",
    "vamos bien",
    "estamos en verde",
    "situacion controlada",
    "situación controlada",
]

SOLUTION_REQUEST_WORDS = [
    "que hacemos",
    "qué hacemos",
    "que propones",
    "qué propones",
    "solucion",
    "solución",
    "plan de accion",
    "plan de acción",
    "siguiente paso",
]

EXECUTIVE_ACTIONS = {
    "sla": [
        "Operativo: congelar promesas externas hasta validar capacidad real y backlog.",
        "Operativo: abrir plan de recuperación de SLA con responsable, fecha y seguimiento diario.",
    ],
    "gross_margin": [
        "Financiero: recalcular forecast con costos reales antes de aprobar descuentos o ampliaciones.",
        "Comercial: no comprometer precio ni volumen sin validar margen mínimo con Finanzas.",
    ],
    "ebitda": [
        "Financiero: revisar palancas de costo y rentabilidad antes de aceptar nuevos compromisos.",
        "Operativo: priorizar eficiencia y capacidad antes de prometer crecimiento.",
    ],
    "attrition": [
        "Operativo: activar plan de retención y cobertura antes de prometer estabilidad del servicio.",
        "Comercial: advertir riesgo de continuidad si la rotación sigue sobre meta.",
    ],
    "default": [
        "Director: asignar dueño, fecha límite y evidencia de avance antes de cerrar el acuerdo.",
        "Directorio: validar la promesa contra BI antes de comunicarla al cliente.",
    ],
}

PROFILE_LABELS = {
    "cfo": "Director Financiero",
    "coo": "Director de Operaciones",
    "balanced": "Director Financiero y de Operaciones",
}

PROFILE_ACTIONS = {
    "cfo": {
        "sla": [
            "Financiero: cuantificar penalidades, costo de recuperación e impacto en forecast antes de comprometer el SLA.",
        ],
        "gross_margin": [
            "Financiero: recalcular margen y forecast con costos reales antes de aprobar precio, descuento o volumen.",
        ],
        "ebitda": [
            "Financiero: revisar rentabilidad, palancas de costo y caja antes de aceptar el compromiso.",
        ],
        "attrition": [
            "Financiero: cuantificar el costo de reposición y su impacto en margen antes de prometer continuidad.",
        ],
        "default": [
            "Financiero: validar impacto en margen, caja y forecast; dejar responsable y fecha antes de aprobar.",
        ],
    },
    "coo": {
        "sla": [
            "Operativo: validar capacidad, backlog y plan de recuperación con responsable y seguimiento diario.",
        ],
        "gross_margin": [
            "Operativo: revisar productividad, dotación y sobrecostos que explican la brecha de margen.",
        ],
        "ebitda": [
            "Operativo: identificar ineficiencias y capacidad ociosa antes de prometer crecimiento.",
        ],
        "attrition": [
            "Operativo: activar retención y cobertura de posiciones críticas antes de prometer estabilidad.",
        ],
        "default": [
            "Operativo: validar capacidad real y convertir el acuerdo en responsable, fecha y métrica de éxito.",
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

SENTIMENT_MAP = {
    "preocupado": {"emoji": "😟", "label": "Preocupado/Tenso"},
    "frustrado": {"emoji": "😤", "label": "Frustrado"},
    "neutral": {"emoji": "😐", "label": "Neutral/Informativo"},
    "positivo": {"emoji": "🟢", "label": "Positivo/Confiado"},
    "conflictivo": {"emoji": "⚡", "label": "Conflictivo"},
}

WORRIED_WORDS = [
    "perdida",
    "pérdida",
    "perdidas",
    "pérdidas",
    "caida",
    "caída",
    "riesgo",
    "penalidad",
    "multa",
    "sancion",
    "churn",
    "abandono",
    "preocupado",
    "preocupante",
    "critico",
    "crítico",
]

FRUSTRATED_WORDS = [
    "frustrado",
    "frustración",
    "frustracion",
    "enojado",
    "molesto",
    "cansado",
    "harto",
    "siempre lo mismo",
    "otra vez",
    "no funciona",
    "no sirve",
]

POSITIVE_SENTIMENT_WORDS = [
    "logramos",
    "conseguimos",
    "excelente",
    "perfecto",
    "bien hecho",
    "felicidades",
    "meta cumplida",
    "objetivo alcanzado",
    "mejora",
    "superamos",
]


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

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
class VigIAResponse:
    """Response from VigIA with tag format."""
    tag: str  # "💬 ANÁLISIS", "⚠️ ALERTA", "💡 SUGERENCIA", "[EMOJI] SENTIMIENTO"
    message: str
    should_respond: bool
    severity: str  # silent, info, warning, critical
    category: str
    detected_claims: list[DetectedClaim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    sentiment: str | None = None
    voice_intervention: bool = False
    voice_suppressed_reason: str | None = None
    executive_summary: str | None = None
    recommended_actions: list[str] = field(default_factory=list)
    executive_profile: str = "balanced"
    source_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def formatted_message(self) -> str:
        """Returns the message with tag and signature."""
        if not self.should_respond:
            return ""
        return f"{self.tag} — {self.message} {SIGNATURE}"


@dataclass
class SessionState:
    """Tracks the state of a meeting session."""
    meeting_type: str = ""
    date: str = ""
    participants: list[dict[str, str]] = field(default_factory=list)
    voice_mode: bool = False
    status: str = "idle"  # idle, active, paused, closed
    started_at: str | None = None
    paused_at: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    sentiments: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    commitments: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def load_bi_snapshot(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def detect_sentiment(text: str) -> dict[str, str]:
    """Detect sentiment from text and return emoji + label."""
    normalized = normalize(text)

    # Check for conflict first
    if has_any(normalized, CONFLICT_WORDS):
        return SENTIMENT_MAP["conflictivo"]

    # Check for worried/concerned
    if has_any(normalized, WORRIED_WORDS):
        return SENTIMENT_MAP["preocupado"]

    # Check for frustrated
    if has_any(normalized, FRUSTRATED_WORDS):
        return SENTIMENT_MAP["frustrado"]

    # Check for positive
    if has_any(normalized, POSITIVE_SENTIMENT_WORDS):
        return SENTIMENT_MAP["positivo"]

    # Default to neutral
    return SENTIMENT_MAP["neutral"]


def analyze_statement(
    *,
    speaker: str,
    role: str,
    text: str,
    snapshot: dict[str, Any],
    meeting_context: list[dict[str, Any]] | None = None,
    executive_profile: str = "balanced",
    source_status: dict[str, Any] | None = None,
) -> VigIAResponse:
    """Analyze a statement and return a formatted VigIA response."""
    executive_profile = normalize_executive_profile(executive_profile)
    normalized = normalize(text)
    recent_campaigns = infer_recent_campaigns(meeting_context or [], snapshot)
    promise_detected = is_promise(normalized)
    broad_health_signal = is_broad_health_signal(normalized)
    solution_requested = is_solution_request(normalized)
    claims = scope_claims(
        detect_claims(text, snapshot),
        snapshot,
        recent_campaigns=recent_campaigns,
        expand_unscoped=promise_detected or broad_health_signal,
    )
    evidence: list[Evidence] = []
    alert_messages: list[str] = []
    recommended_actions: list[str] = []
    highest_severity = "silent"
    response_tag = "💬 ANÁLISIS"
    response_category = "analysis"
    executive_summary: str | None = None

    # Detect sentiment
    sentiment = detect_sentiment(text)

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
        value_conflict = (
            claim.spoken_value is not None
            and abs(claim.spoken_value - ev.actual) > metric_tolerance(metric)
        )
        history_conflict = (
            promise_detected
            and claim.spoken_value is not None
            and not is_promise_supported_by_history(metric, claim.spoken_value)
        )
        disguised_risk = claim.stance == "positive" and not metric_ok
        promise_conflict = promise_detected and (value_conflict or history_conflict or not metric_ok)

        if promise_conflict:
            highest_severity = max_severity(highest_severity, "critical")
            response_tag = "⚠️ ALERTA"
            response_category = "promise_not_supported"
            executive_summary = "Promesa no respaldada por el dato disponible."
            add_actions(recommended_actions, actions_for_metric(claim.metric_key, executive_profile))
            promised = (
                f" Se prometió {claim.spoken_value:g}{ev.unit}."
                if claim.spoken_value is not None
                else ""
            )
            alert_messages.append(
                f"Intervengo: no validaría esa promesa. {claim.campaign_name}: "
                f"{ev.metric} está en {ev.actual:g}{ev.unit} contra meta {ev.target:g}{ev.unit}."
                f"{promised} Primero necesito plan, responsable y fecha antes de comprometerlo."
            )
        elif disguised_risk:
            highest_severity = max_severity(highest_severity, "critical")
            response_tag = "⚠️ ALERTA"
            response_category = "threshold_breach"
            executive_summary = "Riesgo presentado como normalidad."
            add_actions(recommended_actions, actions_for_metric(claim.metric_key, executive_profile))
            alert_messages.append(
                f"Intervengo: no llamaría normal a este dato. {claim.campaign_name}: "
                f"{ev.metric} está en {ev.actual:g}{ev.unit} contra meta {ev.target:g}{ev.unit}."
            )
        elif value_conflict:
            highest_severity = max_severity(highest_severity, "warning")
            response_tag = "⚠️ ALERTA"
            response_category = "value_mismatch"
            executive_summary = "Dato mencionado no coincide con BI."
            add_actions(recommended_actions, actions_for_metric(claim.metric_key, executive_profile))
            alert_messages.append(
                f"Intervengo: ese dato no cuadra. {claim.campaign_name}: el valor mencionado ({claim.spoken_value:g}{ev.unit}) "
                f"no coincide con BI ({ev.actual:g}{ev.unit})."
            )
        elif claim.stance == "negative" and metric_ok:
            highest_severity = max_severity(highest_severity, "warning")
            response_tag = "⚠️ ALERTA"
            response_category = "false_risk_claim"
            executive_summary = "Se declaró un riesgo que no coincide con el dato oficial."
            add_actions(recommended_actions, actions_for_metric(claim.metric_key, executive_profile))
            alert_messages.append(
                f"Intervengo: ese riesgo no está respaldado por el dato. {claim.campaign_name}: "
                f"{ev.metric} está en {ev.actual:g}{ev.unit} contra meta {ev.target:g}{ev.unit}."
            )

    if not alert_messages and (promise_detected or broad_health_signal or solution_requested):
        campaign_ids = {claim.campaign_id for claim in claims if claim.campaign_id}
        if not campaign_ids:
            campaign_ids = {campaign_id for campaign_id, _ in detect_campaigns(normalized, snapshot)}
        if not campaign_ids and recent_campaigns:
            campaign_ids = {campaign_id for campaign_id, _ in recent_campaigns}

        issues = find_noncompliant_metrics(snapshot, campaign_ids=campaign_ids or None)
        if issues:
            evidence.extend(issues[:4])
            critical_issue_present = any(is_critical_metric(issue) for issue in issues)
            highest_severity = max_severity(highest_severity, "critical" if critical_issue_present else "warning")
            response_tag = "⚠️ ALERTA"
            response_category = "executive_review"
            executive_summary = "La afirmación general no está respaldada por los indicadores."
            for issue in issues[:3]:
                add_actions(
                    recommended_actions,
                    actions_for_metric(metric_key_from_label(issue.metric), executive_profile),
                )
            alert_messages.append(
                "Intervengo: no cerraría esa afirmación como válida. "
                f"{format_evidence_summary(issues[:3])}. "
                "Solución: separar operación, finanzas y comercial; asignar dueño por brecha y no prometer al cliente hasta validar recuperación."
            )
        elif solution_requested or promise_detected:
            recommended_actions = [
                "Director: mantener el acuerdo, pero dejar dueño, fecha y métrica de éxito por escrito.",
                "Financiero: confirmar impacto en margen antes de mover compromisos comerciales.",
                "Operativo: validar capacidad antes de prometer fecha o volumen.",
            ]
            return finalize_response(VigIAResponse(
                tag="💡 SUGERENCIA",
                message="La propuesta no contradice el BI disponible. La aprobaría solo con dueño, fecha, métrica de éxito y validación de capacidad.",
                should_respond=True,
                severity="info",
                category="executive_suggestion",
                detected_claims=claims,
                evidence=evidence,
                sentiment=sentiment["emoji"],
                voice_intervention=True,
                executive_summary="Sugerencia ejecutiva con control de compromiso.",
                recommended_actions=recommended_actions,
            ), executive_profile=executive_profile, source_status=source_status)

    if not alert_messages and not claims:
        # No claims detected - check if it's a suggestion or just casual
        if is_suggestion(text):
            return finalize_response(VigIAResponse(
                tag="💡 SUGERENCIA",
                message="Sugiero validar esta propuesta con el equipo de Controlling.",
                should_respond=True,
                severity="info",
                category="suggestion",
                sentiment=sentiment["emoji"],
                executive_summary="Sugerencia detectada sin evidencia contradictoria.",
                recommended_actions=[
                    "Director: convertir la propuesta en responsable, fecha y métrica de éxito.",
                ],
            ), executive_profile=executive_profile, source_status=source_status)
        # Smart silence - don't respond
        return finalize_response(VigIAResponse(
            tag="",
            message="",
            should_respond=False,
            severity="silent",
            category="smart_silence",
            sentiment=sentiment["emoji"],
        ), executive_profile=executive_profile, source_status=source_status)

    if not alert_messages and claims and not evidence:
        # A metric or value was understood, but it could not be tied to a real
        # account/metric pair. Keep listening without inventing evidence and
        # let the UI ask for the missing scope.
        return finalize_response(VigIAResponse(
            tag="",
            message="",
            should_respond=False,
            severity="silent",
            category="claim_without_scope",
            detected_claims=claims,
            sentiment=sentiment["emoji"],
            executive_summary="Afirmación entendida; falta identificar la cuenta para contrastarla.",
        ), executive_profile=executive_profile, source_status=source_status)

    if not alert_messages:
        # Claims detected but no issues
        return finalize_response(VigIAResponse(
            tag="💬 ANÁLISIS",
            message=f"Dato registrado correctamente. {evidence[0].campaign}: {evidence[0].metric} en {evidence[0].actual:g}{evidence[0].unit}.",
            should_respond=True,
            severity="info",
            category="data_confirmation",
            detected_claims=claims,
            evidence=evidence,
            sentiment=sentiment["emoji"],
            executive_summary="Dato verificado contra BI.",
            recommended_actions=recommended_actions,
        ), executive_profile=executive_profile, source_status=source_status)

    if recommended_actions:
        message = " ".join(alert_messages)
        message = f"{message} Acciones: {' '.join(unique_items(recommended_actions[:3]))}"
    else:
        message = " ".join(alert_messages)
    voice_intervention = highest_severity in {"warning", "critical"}

    return finalize_response(VigIAResponse(
        tag=response_tag,
        message=message,
        should_respond=True,
        severity=highest_severity,
        category=response_category,
        detected_claims=claims,
        evidence=evidence,
        sentiment=sentiment["emoji"],
        voice_intervention=voice_intervention,
        executive_summary=executive_summary,
        recommended_actions=unique_items(recommended_actions),
    ), executive_profile=executive_profile, source_status=source_status)


def check_thresholds(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Check all campaigns against critical thresholds."""
    alerts = []
    for campaign in snapshot.get("campaigns", []):
        for metric_key, metric_data in campaign.get("metrics", {}).items():
            if metric_key in CRITICAL_THRESHOLDS:
                threshold_info = CRITICAL_THRESHOLDS[metric_key]
                actual = float(metric_data["actual"])
                threshold = threshold_info["threshold"]
                direction = threshold_info["direction"]

                if direction == "min" and actual < threshold:
                    alerts.append({
                        "severity": "critical",
                        "campaign": campaign["name"],
                        "metric": metric_data["label"],
                        "actual": actual,
                        "threshold": threshold,
                        "message": (
                            f"{campaign['name']}: {metric_data['label']} está en {actual:g}%, "
                            f"por debajo del umbral del {threshold:g}%."
                        ),
                    })
                elif direction == "max" and actual > threshold:
                    alerts.append({
                        "severity": "critical",
                        "campaign": campaign["name"],
                        "metric": metric_data["label"],
                        "actual": actual,
                        "threshold": threshold,
                        "message": (
                            f"{campaign['name']}: {metric_data['label']} está en {actual:g}%, "
                            f"por encima del umbral del {threshold:g}%."
                        ),
                    })
    return alerts


def infer_recent_campaigns(
    meeting_context: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    limit: int = 6,
) -> list[tuple[str, str]]:
    """Infer the active campaign from the latest transcript lines."""
    found: list[tuple[str, str]] = []
    for item in reversed(meeting_context[-limit:]):
        text = normalize(str(item.get("text", "")))
        for campaign in detect_campaigns(text, snapshot):
            if campaign not in found:
                found.append(campaign)
        if found:
            break
    return found[:2]


def scope_claims(
    claims: list[DetectedClaim],
    snapshot: dict[str, Any],
    *,
    recent_campaigns: list[tuple[str, str]],
    expand_unscoped: bool,
) -> list[DetectedClaim]:
    """Attach claims without campaign to the recent context or, for promises, all campaigns."""
    if not claims:
        return []

    scoped: list[DetectedClaim] = []
    all_campaigns = [
        (str(campaign["id"]), str(campaign["name"]))
        for campaign in snapshot.get("campaigns", [])
    ]

    for claim in claims:
        if claim.campaign_id:
            scoped.append(claim)
            continue

        candidate_campaigns = recent_campaigns
        if not candidate_campaigns and expand_unscoped:
            candidate_campaigns = all_campaigns

        if candidate_campaigns:
            scoped.extend(
                replace(claim, campaign_id=campaign_id, campaign_name=campaign_name)
                for campaign_id, campaign_name in candidate_campaigns
            )
        else:
            scoped.append(claim)

    return scoped


def is_promise(normalized_text: str) -> bool:
    return has_any(normalized_text, PROMISE_WORDS)


def is_broad_health_signal(normalized_text: str) -> bool:
    return has_any(normalized_text, BROAD_HEALTH_WORDS)


def is_solution_request(normalized_text: str) -> bool:
    return has_any(normalized_text, SOLUTION_REQUEST_WORDS)


def actions_for_metric(metric_key: str | None, executive_profile: str = "balanced") -> list[str]:
    profile = normalize_executive_profile(executive_profile)
    if profile in PROFILE_ACTIONS:
        profile_actions = PROFILE_ACTIONS[profile]
        return profile_actions.get(metric_key or "default", profile_actions["default"])
    if not metric_key:
        return EXECUTIVE_ACTIONS["default"]
    return EXECUTIVE_ACTIONS.get(metric_key, EXECUTIVE_ACTIONS["default"])


def normalize_executive_profile(value: str) -> str:
    normalized = normalize(value)
    aliases = {
        "financiero": "cfo",
        "director financiero": "cfo",
        "operaciones": "coo",
        "director de operaciones": "coo",
        "mixto": "balanced",
        "dual": "balanced",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in PROFILE_LABELS else "balanced"


def metric_tolerance(metric: dict[str, Any]) -> float:
    configured = metric.get("tolerance")
    if configured is not None:
        try:
            return max(0.0, float(configured))
        except (TypeError, ValueError):
            pass
    return 0.5 if str(metric.get("unit", "")).strip() == "%" else 0.01


def finalize_response(
    response: VigIAResponse,
    *,
    executive_profile: str,
    source_status: dict[str, Any] | None,
) -> VigIAResponse:
    response.executive_profile = normalize_executive_profile(executive_profile)
    response.source_status = dict(source_status or {})

    if response.severity not in {"warning", "critical"}:
        return response
    if not source_status or source_status.get("trusted", True):
        return response

    source = str(source_status.get("source") or "seguimiento_financiero")
    if source_status.get("stale"):
        reason = "está desactualizada"
    elif not source_status.get("available", False):
        reason = "no está disponible"
    else:
        reason = "no pudo validarse"

    response.tag = "⚠️ VALIDACIÓN PENDIENTE"
    response.message = (
        f"No corregiré esta afirmación todavía: la fuente {source} {reason}. "
        "Actualiza los datos oficiales y vuelve a contrastar antes de tomar una decisión."
    )
    response.should_respond = True
    response.severity = "info"
    response.category = "source_unverified"
    response.voice_intervention = False
    response.executive_summary = "La fuente no tiene la vigencia necesaria para corregir a un participante."
    response.recommended_actions = [
        "Datos: actualizar seguimiento_financiero y confirmar su fecha de corte antes de intervenir.",
    ]
    return response


def add_actions(target: list[str], actions: list[str]) -> None:
    for action in actions:
        if action not in target:
            target.append(action)


def unique_items(items: list[str]) -> list[str]:
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


def is_promise_supported_by_history(metric: dict[str, Any], promised_value: float) -> bool:
    """Check whether a promised value is plausible against current and recent historical data."""
    actual = float(metric["actual"])
    direction = metric["direction"]

    if direction == "min":
        required_improvement = promised_value - actual
    elif direction == "max":
        required_improvement = actual - promised_value
    else:
        return True

    if required_improvement <= 0:
        return True

    history = metric.get("history") or []
    values = [
        float(item["actual"])
        for item in history
        if isinstance(item, dict) and item.get("actual") is not None
    ]
    values.append(actual)

    if len(values) < 2:
        return required_improvement <= 1.0

    improvements: list[float] = []
    for previous, current in zip(values, values[1:]):
        if direction == "min":
            delta = current - previous
        else:
            delta = previous - current
        if delta > 0:
            improvements.append(delta)

    typical_improvement = sum(improvements) / len(improvements) if improvements else 0.0
    reasonable_jump = max(1.0, typical_improvement * 2.0)
    return required_improvement <= reasonable_jump


def find_noncompliant_metrics(
    snapshot: dict[str, Any],
    *,
    campaign_ids: set[str] | None = None,
    metric_keys: set[str] | None = None,
) -> list[Evidence]:
    issues: list[Evidence] = []
    for campaign in snapshot.get("campaigns", []):
        campaign_id = str(campaign["id"])
        if campaign_ids and campaign_id not in campaign_ids:
            continue
        for key, metric in campaign.get("metrics", {}).items():
            if metric_keys and key not in metric_keys:
                continue
            if is_metric_compliant(float(metric["actual"]), float(metric["target"]), metric["direction"]):
                continue
            issues.append(
                Evidence(
                    source=snapshot.get("source", "Fuente BI"),
                    generated_at=snapshot.get("generated_at", "sin fecha"),
                    campaign=str(campaign["name"]),
                    metric=str(metric["label"]),
                    actual=float(metric["actual"]),
                    target=float(metric["target"]),
                    direction=str(metric["direction"]),
                    unit=str(metric["unit"]),
                )
            )
    return issues


def is_critical_metric(issue: Evidence) -> bool:
    metric_key = metric_key_from_label(issue.metric)
    if metric_key not in CRITICAL_THRESHOLDS:
        return False
    threshold = CRITICAL_THRESHOLDS[metric_key]
    return not is_metric_compliant(issue.actual, threshold["threshold"], threshold["direction"])


def metric_key_from_label(label: str) -> str | None:
    normalized_label = normalize(label)
    for key in METRIC_ALIASES:
        if normalize(metric_label(key)) == normalized_label:
            return key
    return None


def format_evidence_summary(issues: list[Evidence]) -> str:
    if not issues:
        return "no hay brechas visibles en BI"
    return "; ".join(
        f"{issue.campaign} {issue.metric} {issue.actual:g}{issue.unit} vs meta {issue.target:g}{issue.unit}"
        for issue in issues
    )


def is_suggestion(text: str) -> bool:
    """Check if the text is a suggestion."""
    normalized = normalize(text)
    suggestion_words = [
        "sugiero",
        "propongo",
    "recomiendo",
    "deberíamos",
    "deberiamos",
    "podríamos",
    "podriamos",
    "qué tal si",
    "que tal si",
    ]
    return has_any(normalized, suggestion_words)


def close_session(
    *,
    meeting_type: str,
    transcript: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    sentiments: list[dict[str, Any]] | None = None,
    session_started_at: str | None = None,
    session_ended_at: str | None = None,
    duration_seconds: int | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close session and generate all 4 post-meeting documents."""
    decisions = [
        item for item in transcript
        if has_any(item.get("text", ""), DECISION_WORDS)
    ]
    risks = [
        item for item in transcript
        if has_any(item.get("text", ""), RISK_WORDS)
    ]

    # Generate all 4 documents
    doc1 = build_executive_minutes(
        meeting_type, transcript, decisions, risks, alerts,
        session_started_at=session_started_at,
        session_ended_at=session_ended_at,
        duration_seconds=duration_seconds,
    )
    doc2 = build_financial_summary(alerts, transcript, snapshot=snapshot)
    doc3 = build_sentiment_report(sentiments or [], transcript)
    doc4 = build_structured_transcript(transcript, alerts, decisions, risks)

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
        "documents": {
            "executive_minutes": doc1,
            "financial_summary": doc2,
            "sentiment_report": doc3,
            "structured_transcript": doc4,
        },
        "executive_minutes": doc1,
        "financial_summary": doc2,
        "sentiment_report": doc3,
        "structured_transcript": doc4,
        "decisions": decisions,
        "risks": risks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST-MEETING DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────────

def build_executive_minutes(
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
    """Documento 1: Minuta Ejecutiva"""
    lines = [
        f"# MINUTA DE REUNIÓN",
        "",
        f"**Fecha:** {session_started_at or 'No registrada'} | **Tipo:** {meeting_type}",
        f"**Duración:** {format_duration_seconds(duration_seconds)}",
        f"- Duracion de sesion: {format_duration_seconds(duration_seconds)}",
        "",
        "## 1. TEMAS TRATADOS",
        "",
    ]

    # Extract topics from transcript
    topics = extract_topics(transcript)
    for topic in topics:
        lines.append(f"- {topic}")

    lines.extend(["", "## 2. DECISIONES TOMADAS", ""])
    if decisions:
        for i, item in enumerate(decisions, 1):
            lines.append(f"{i}. {item.get('speaker', 'Participante')}: {item.get('text', '')}")
    else:
        lines.append("- No se tomaron decisiones explícitas en esta sesión.")

    lines.extend(["", "## 3. COMPROMISOS Y PRÓXIMOS PASOS", ""])
    commitments = extract_commitments(transcript)
    if commitments:
        lines.append("| Responsable | Acción | Fecha límite |")
        lines.append("|-------------|--------|--------------|")
        for c in commitments:
            lines.append(f"| {c.get('responsible', 'Por definir')} | {c.get('action', '')} | {c.get('deadline', 'Por definir')} |")
    else:
        lines.append("- No se registraron compromisos explícitos.")

    lines.extend(["", "## 4. TEMAS PENDIENTES PARA PRÓXIMA SESIÓN", ""])
    pending = extract_pending_topics(transcript, decisions)
    if pending:
        for p in pending:
            lines.append(f"- {p}")
    else:
        lines.append("- Todos los temas fueron abordados.")

    return "\n".join(lines)


def build_financial_summary(
    alerts: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    *,
    snapshot: dict[str, Any] | None = None,
) -> str:
    """Documento 2: Resumen Financiero y Operativo"""
    lines = [
        "# RESUMEN FINANCIERO Y OPERATIVO",
        "",
        "## Semáforo de Estatus por Campaña",
        "",
        "| Campaña | Indicador | Actual | Meta | Estado |",
        "|---------|-----------|--------|------|--------|",
    ]

    has_financial_rows = False
    if snapshot:
        for campaign in snapshot.get("campaigns", []):
            for metric in campaign.get("metrics", {}).values():
                has_financial_rows = True
                actual = float(metric["actual"])
                target = float(metric["target"])
                compliant = is_metric_compliant(actual, target, metric["direction"])
                status = "🟢 Cumple" if compliant else "🔴 Fuera de meta"
                lines.append(
                    f"| {campaign['name']} | {metric['label']} | {actual:g}{metric['unit']} | "
                    f"{target:g}{metric['unit']} | {status} |"
                )
    else:
        kpis = extract_kpis(transcript)
        for campaign, data in kpis.items():
            has_financial_rows = True
            status = "🟢 Registrado" if data else "⚪ Sin dato"
            lines.append(f"| {campaign} | KPI mencionado | — | — | {status} |")

    if not has_financial_rows:
        lines.append("| Sin datos | — | — | — | ⚪ No disponible |")

    lines.extend(["", "## Top 3 Riesgos Identificados", ""])
    if alerts:
        for i, alert in enumerate(alerts[:3], 1):
            lines.append(f"{i}. {alert.get('message', 'Riesgo no especificado')}")
    else:
        lines.append("- No se identificaron riesgos críticos.")

    return "\n".join(lines)


def build_sentiment_report(
    sentiments: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
) -> str:
    """Documento 3: Reporte de Sentimiento y Dinámica"""
    lines = [
        "# REPORTE DE SENTIMIENTO Y DINÁMICA",
        "",
        "## Tono General de la Sesión",
        "",
    ]

    # Calculate overall sentiment
    if sentiments:
        sentiment_counts = {}
        for s in sentiments:
            emoji = s.get("emoji", "😐")
            sentiment_counts[emoji] = sentiment_counts.get(emoji, 0) + 1

        dominant = max(sentiment_counts, key=sentiment_counts.get)
        lines.append(f"**Tono predominante:** {dominant}")
        lines.append("")
        lines.append("### Distribución de Sentimientos")
        lines.append("")
        for emoji, count in sentiment_counts.items():
            lines.append(f"- {emoji}: {count} menciones")
    else:
        lines.append("- No se registraron datos de sentimiento suficientes.")

    lines.extend(["", "## Momentos de Tensión", ""])
    tension_moments = [s for s in sentiments if s.get("emoji") == "⚡"]
    if tension_moments:
        for tm in tension_moments:
            lines.append(f"- **{tm.get('timestamp', '')}**: {tm.get('text', 'Sin detalle')}")
    else:
        lines.append("- No se detectaron momentos de tensión significativos.")

    lines.extend(["", "## Nivel de Participación por Área", ""])
    participation = calculate_participation(transcript)
    for area, count in participation.items():
        lines.append(f"- **{area}**: {count} intervenciones")

    lines.extend(["", "## Recomendaciones", ""])
    lines.append("- Mantener el enfoque en datos objetivos para las próximas sesiones.")
    lines.append("- Validar compromisos pendientes antes de la próxima reunión.")

    return "\n".join(lines)


def build_structured_transcript(
    transcript: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    risks: list[dict[str, Any]],
) -> str:
    """Documento 4: Transcripción Estructurada"""
    lines = [
        "# TRANSCRIPCIÓN ESTRUCTURADA",
        "",
        "---",
        "",
    ]

    for item in transcript:
        speaker = item.get("speaker", "Participante")
        role = item.get("role", "")
        text = item.get("text", "")
        timestamp = item.get("timestamp", "")

        lines.append(f"**[{timestamp}] {speaker} ({role})**")
        lines.append(f"> {text}")

        # Add event markers
        if is_decision(text):
            lines.append("📌 **[DECISIÓN]**")
        if is_commitment(text):
            lines.append("📌 **[COMPROMISO]**")
        if is_risk(text):
            lines.append("⚠️ **[ALERTA]**")

        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def extract_topics(transcript: list[dict[str, Any]]) -> list[str]:
    """Extract main topics discussed in the meeting."""
    topics = []
    for item in transcript:
        text = item.get("text", "")
        # Look for topic indicators
        if any(word in text.lower() for word in ["tema", "punto", "agenda", "hablar de", "discutir"]):
            topics.append(text)
    return topics[:5] if topics else ["Temas generales de operación"]


def extract_commitments(transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract commitments and action items."""
    commitments = []
    for item in transcript:
        text = item.get("text", "")
        if is_commitment(text):
            commitments.append({
                "responsible": item.get("speaker", "Por definir"),
                "action": text,
                "deadline": "Por definir",
            })
    return commitments


def extract_pending_topics(
    transcript: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[str]:
    """Extract topics that were discussed but not resolved."""
    # Simple heuristic: topics mentioned but not in decisions
    pending = []
    decision_texts = [d.get("text", "") for d in decisions]
    for item in transcript:
        text = item.get("text", "")
        if "pendiente" in text.lower() or "para la próxima" in text.lower():
            if text not in decision_texts:
                pending.append(text)
    return pending


def extract_kpis(transcript: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract KPI mentions from transcript."""
    kpis = {}
    for item in transcript:
        text = item.get("text", "").lower()
        # Simple extraction - would need NLP for production
        for campaign in ["bcp", "claro", "entel"]:
            if campaign in text:
                if campaign not in kpis:
                    kpis[campaign] = {}
                # Check for metric mentions
                if "sla" in text:
                    kpis[campaign]["sla_mentioned"] = True
                if "margen" in text:
                    kpis[campaign]["margin_mentioned"] = True
    return kpis


def calculate_participation(transcript: list[dict[str, Any]]) -> dict[str, int]:
    """Calculate participation by area/role."""
    participation = {}
    for item in transcript:
        role = item.get("role", "Sin rol")
        participation[role] = participation.get(role, 0) + 1
    return participation


def is_decision(text: str) -> bool:
    return has_any(normalize(text), DECISION_WORDS)


def is_commitment(text: str) -> bool:
    commitment_words = ["me comprometo", "voy a", "haré", "hare", "entregaré", "entregare"]
    return has_any(normalize(text), commitment_words)


def is_risk(text: str) -> bool:
    return has_any(normalize(text), RISK_WORDS)


def detect_claims(text: str, snapshot: dict[str, Any]) -> list[DetectedClaim]:
    normalized = normalize(text)
    metric_mentions = detect_metric_mentions(normalized, snapshot)
    if not metric_mentions:
        return []

    campaign_mentions = detect_campaign_mentions(normalized, snapshot)
    claims: list[DetectedClaim] = []
    for index, (metric_key, metric_start, metric_end) in enumerate(metric_mentions):
        previous_end = metric_mentions[index - 1][2] if index > 0 else 0
        next_start = metric_mentions[index + 1][1] if index + 1 < len(metric_mentions) else len(normalized)
        context = claim_context(
            normalized,
            metric_start=metric_start,
            segment_start=previous_end,
            segment_end=next_start,
        )
        campaign_id: str | None = None
        campaign_name: str | None = None
        if campaign_mentions:
            campaign_id, campaign_name, _, _ = min(
                campaign_mentions,
                key=lambda item: phrase_distance(metric_start, metric_end, item[2], item[3]),
            )
        claims.append(
            DetectedClaim(
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                metric_key=metric_key,
                metric_label=metric_label(metric_key),
                spoken_value=detect_value_near_metric(
                    normalized,
                    metric_start=metric_start,
                    metric_end=metric_end,
                    segment_start=previous_end,
                    segment_end=next_start,
                ),
                stance=detect_stance(context),
                raw_text=text,
            )
        )
    return claims


def detect_metric(normalized_text: str, snapshot: dict[str, Any] | None = None) -> str | None:
    mentions = detect_metric_mentions(normalized_text, snapshot)
    return mentions[0][0] if mentions else None


def detect_metric_mentions(
    normalized_text: str,
    snapshot: dict[str, Any] | None = None,
) -> list[tuple[str, int, int]]:
    candidates: list[tuple[str, int, int]] = []
    aliases_by_key = {key: list(aliases) for key, aliases in METRIC_ALIASES.items()}
    for campaign in (snapshot or {}).get("campaigns", []):
        for key, metric in campaign.get("metrics", {}).items():
            aliases_by_key.setdefault(str(key), [])
            aliases_by_key[str(key)].extend(
                [
                    str(key).replace("_", " "),
                    str(metric.get("label", "")),
                    *[str(alias) for alias in metric.get("aliases", [])],
                ]
            )

    for key, aliases in aliases_by_key.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for match in phrase_matches(normalized_text, normalize(alias)):
                candidates.append((key, match.start(), match.end()))

    candidates.sort(key=lambda item: (item[1], -(item[2] - item[1])))
    selected: list[tuple[str, int, int]] = []
    for candidate in candidates:
        _, start, end = candidate
        if any(start < existing_end and end > existing_start for _, existing_start, existing_end in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item[1])


def detect_campaigns(normalized_text: str, snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for campaign_id, campaign_name, _, _ in detect_campaign_mentions(normalized_text, snapshot):
        item = (campaign_id, campaign_name)
        if item not in found:
            found.append(item)
    return found


def detect_campaign_mentions(
    normalized_text: str,
    snapshot: dict[str, Any],
) -> list[tuple[str, str, int, int]]:
    found: list[tuple[str, str, int, int]] = []
    for campaign in snapshot.get("campaigns", []):
        campaign_id = normalize(str(campaign["id"]))
        campaign_name = str(campaign["name"])
        aliases = [campaign_id, normalize(campaign_name)] + [
            normalize(str(alias)) for alias in campaign.get("aliases", [])
        ]
        campaign_matches = []
        for alias in unique_items(aliases):
            campaign_matches.extend(phrase_matches(normalized_text, alias))
        for match in campaign_matches:
            found.append((str(campaign["id"]), campaign_name, match.start(), match.end()))
    return sorted(found, key=lambda item: item[2])


def detect_percentage(normalized_text: str) -> float | None:
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:%|por ciento|puntos)", normalized_text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def detect_value_near_metric(
    normalized_text: str,
    *,
    metric_start: int,
    metric_end: int,
    segment_start: int,
    segment_end: int,
) -> float | None:
    segment = normalized_text[segment_start:segment_end]
    number_pattern = re.compile(
        r"(?<![\w])(-?\d+(?:[\.,]\d+)?)\s*"
        r"(%|por\s+ciento|puntos?|mil(?:es)?|millon(?:es)?)?(?![\w])"
    )
    candidates: list[tuple[int, float, bool]] = []
    metric_center = (metric_start + metric_end) // 2
    for match in number_pattern.finditer(segment):
        absolute_start = segment_start + match.start()
        raw_value = match.group(1).replace(",", ".")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        spoken_unit = (match.group(2) or "").strip()
        if spoken_unit.startswith("millon"):
            value *= 1_000_000
        elif spoken_unit.startswith("mil"):
            value *= 1_000
        has_business_unit = bool(spoken_unit)
        if not has_business_unit:
            local_prefix = segment[max(0, match.start() - 16):match.start()]
            local_suffix = segment[match.end():match.end() + 8]
            linked_to_metric = (
                abs(absolute_start - metric_center) <= 28
                or bool(re.search(r"\b(?:esta|es|en|de|a|al)\s*$", local_prefix))
                or bool(re.search(r"^\s*(?:por ciento|%)", local_suffix))
            )
            if not linked_to_metric:
                continue
        distance = min(abs(absolute_start - metric_start), abs(absolute_start - metric_end))
        candidates.append((distance, value, has_business_unit))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], not item[2]))
    return candidates[0][1]


def claim_context(
    normalized_text: str,
    *,
    metric_start: int,
    segment_start: int,
    segment_end: int,
) -> str:
    segment = normalized_text[segment_start:segment_end]
    relative_metric_start = max(0, metric_start - segment_start)
    prefix = segment[:relative_metric_start]
    separators = list(re.finditer(r"(?:[,;.]|\bpero\b|\by\b)", prefix))
    if separators:
        segment = segment[separators[-1].end():]
    return segment


def phrase_matches(text: str, phrase: str) -> list[re.Match[str]]:
    if not phrase:
        return []
    pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
    return list(pattern.finditer(text))


def phrase_distance(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    if end_a < start_b:
        return start_b - end_a
    if end_b < start_a:
        return start_a - end_b
    return 0


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
        "tmo": "TMO",
        "occupancy": "Ocupación",
        "facturacion": "Facturación",
    }
    return labels.get(metric_key, metric_key)


def max_severity(current: str, candidate: str) -> str:
    rank = {"silent": 0, "info": 1, "warning": 2, "critical": 3}
    return candidate if rank[candidate] > rank[current] else current


def has_any(text: str, needles: list[str]) -> bool:
    normalized_text = normalize(text)
    return any(normalize(needle) in normalized_text for needle in needles)


def normalize(value: str) -> str:
    lowered = value.lower().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(character for character in decomposed if not unicodedata.combining(character))
