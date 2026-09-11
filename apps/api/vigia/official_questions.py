from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from vigia.core import normalize
from vigia.financial_data import financial_scope_options, number, round_financial_values


SECTION_LABELS = {
    "coverage": "cobertura de SIFO",
    "campaigns": "campañas",
    "history": "histórico financiero",
    "agents": "dotación",
    "payroll_detail": "planilla detallada",
    "billing": "facturación",
    "budget": "presupuesto",
    "kpis": "KPI",
    "executive": "snapshot ejecutivo",
    "ratios": "ratios de supervisión",
    "operations": "operación y SLA",
    "operational_metrics": "métricas operativas",
    "attendance": "asistencia SIOP",
    "data_quality": "calidad de datos",
    "ifc": "IFC mensual",
    "ifc_annual": "IFC anual",
}

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def detect_official_section(question: str) -> str | None:
    """Map a natural-language question to one authoritative SIFO dataset."""
    text = normalize(question)

    if re.search(r"\b(?:planilla|nomina)\s+de\s+(?!estructura\b)", text):
        return "payroll_detail"

    # Specific intents must precede broad words such as "operación" or "datos".
    ordered_markers = [
        ("payroll_detail", [
            "detalle de planilla", "planilla detallada", "detalle de nomina",
            "nomina detallada",
            "planilla por trabajador", "planilla por empleado",
            "planilla por colaborador", "planilla por persona", "planilla por asesor",
            "remuneracion individual", "sueldo de", "salario de", "costo por trabajador",
            "costo por empleado", "vacaciones de", "bonos de",
        ]),
        ("ifc_annual", ["ifc anual", "ifc del ano", "ifc acumulado"]),
        ("ifc", ["ifc", "estado financiero consolidado"]),
        ("data_quality", ["calidad de datos", "cobertura de datos", "mapeos siop", "datos pendientes de validar"]),
        ("attendance", ["asistencia", "ausentismo", "absentismo", "tardanza", "tardanzas", "siop"]),
        ("operational_metrics", ["metricas operativas", "metrica operativa", "productividad operativa"]),
        ("operations", ["colas", "cola de atencion", "nivel de servicio", "service level", "sla", "abandono", "tmo", "aht", "asa"]),
        ("ratios", ["ratio de supervision", "ratio supervisor", "ratios de supervision", "span de control"]),
        ("executive", ["snapshot ejecutivo", "resumen ejecutivo", "plan de accion", "plan de mejora", "que acciones", "que recomiendas"]),
        ("kpis", ["kpi", "kpis", "indicadores diarios"]),
        ("budget", ["presupuesto", "presupuestal", "budget"]),
        ("billing", ["facturacion", "facturas", "facturado", "provision", "igv"]),
        ("agents", ["dotacion", "fuerza laboral", "agentes", "asesores", "personas activas"]),
        ("history", ["historico", "historia financiera", "tendencia", "evolucion", "mes anterior", "meses anteriores"]),
    ]
    for section, markers in ordered_markers:
        if any(phrase_in_text(text, marker) for marker in markers):
            return section

    campaign_words = phrase_in_text(text, "campana") or phrase_in_text(text, "campanas")
    campaign_request_words = [
        "cuales", "cuantas", "lista", "listar", "catalogo", "todas", "activas",
        "inactivas", "hay", "tenemos", "registradas", "informacion de la campana",
    ]
    if campaign_words and (
        len(text.split()) <= 3
        or any(phrase_in_text(text, marker) for marker in campaign_request_words)
    ):
        return "campaigns"

    coverage_markers = [
        "que informacion tienes", "que datos tienes", "toda la informacion",
        "todo sifo", "cobertura de sifo", "fuentes de sifo", "que puedes consultar",
    ]
    if any(phrase_in_text(text, marker) for marker in coverage_markers):
        return "coverage"
    return None


def phrase_in_text(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(normalize(phrase))}(?![a-z0-9])", text) is not None


def extract_official_filters(question: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract non-sensitive account, campaign and period filters from a question."""
    text = normalize(question)
    account = longest_catalog_match(
        text,
        [str(item.get("name") or "") for item in snapshot.get("campaigns", [])],
    )

    details = snapshot.get("details", {}) if isinstance(snapshot.get("details"), dict) else {}
    campaign_payload = details.get("campaigns", {}) if isinstance(details.get("campaigns"), dict) else {}
    campaign_values: list[str] = []
    for row in campaign_payload.get("data", []):
        if not isinstance(row, dict):
            continue
        if account and normalize(str(row.get("account") or "")) != normalize(account):
            continue
        campaign_values.extend([
            str(row.get("campaign_bi") or ""),
            str(row.get("campaign") or ""),
        ])
    campaign = longest_catalog_match(text, campaign_values)

    default_year, default_month = parse_snapshot_period(snapshot.get("period"))
    year: int | None = None
    month: int | None = None
    period_match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])\b", text)
    if period_match:
        year = int(period_match.group(1))
        month = int(period_match.group(2))
    else:
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            year = int(year_match.group(1))
        for month_name, month_number in MONTHS.items():
            if phrase_in_text(text, month_name):
                month = month_number
                break
        if month is not None and year is None:
            year = default_year

    dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", question)
    page_match = re.search(r"\bpagina\s+(\d{1,4})\b", text)
    limit_match = re.search(r"\b(?:limite|primeros?|primeras?)\s+(\d{1,3})\b", text)
    search = extract_payroll_search(text, account=account, campaign=campaign)
    return {
        "account": account,
        "campaign": campaign,
        "year": year,
        "month": month,
        "date_from": dates[0] if dates else "",
        "date_to": dates[-1] if len(dates) > 1 else "",
        "search": search,
        "page": max(1, int(page_match.group(1))) if page_match else 1,
        "limit": max(1, min(int(limit_match.group(1)), 100)) if limit_match else 20,
        "default_year": default_year,
        "default_month": default_month,
    }


def extract_payroll_search(text: str, *, account: str, campaign: str) -> str:
    """Extract an optional employee/DNI/code lookup from a payroll question."""
    quoted = re.search(r"[\"']([^\"']{2,120})[\"']", text)
    if quoted:
        return quoted.group(1).strip()
    dni = re.search(r"\bdni\s*(?:numero|nro|n)?\s*[:#-]?\s*(\d{6,12})\b", text)
    if dni:
        return dni.group(1)
    code = re.search(r"\bcodigo\s*(?:de\s+empleado)?\s*[:#-]?\s*([a-z0-9-]{3,40})\b", text)
    if code:
        return code.group(1)
    if account or campaign:
        return ""
    named = re.search(
        r"\b(?:trabajador|empleado|colaborador|persona|asesor)\s+"
        r"(?:llamado|llamada|de|es)?\s*([a-z][a-z .'-]{2,100})$",
        text,
    )
    if named:
        candidate = trim_period_suffix(named.group(1))
        if candidate not in {"por trabajador", "por empleado", "por colaborador", "por persona", "por asesor"}:
            return candidate
    month_words = "|".join(MONTHS)
    payroll_name = re.search(
        rf"\b(?:planilla|nomina|sueldo|salario|vacaciones|bonos?)\s+de\s+"
        rf"([a-z][a-z .'-]{{1,100}}?)"
        rf"(?=\s+(?:en|para|del?)\s+(?:{month_words}|20\d{{2}})\b|"
        rf",\s*(?:pagina|limite|primeros?|primeras?)\b|$)",
        text,
    )
    if payroll_name:
        candidate = trim_period_suffix(payroll_name.group(1))
        if candidate and candidate not in MONTHS:
            return candidate
    return ""


def trim_period_suffix(value: str) -> str:
    cleaned = re.split(
        r"\s+(?:en|para|del?)\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|20\d{2})\b",
        value,
        maxsplit=1,
    )[0]
    return " ".join(cleaned.split()).strip(" .,'\"")


def longest_catalog_match(text: str, values: list[str]) -> str:
    unique = {" ".join(value.split()) for value in values if str(value).strip()}
    matches = [value for value in unique if phrase_in_text(text, value)]
    return max(matches, key=lambda value: len(normalize(value)), default="")


def parse_snapshot_period(value: Any) -> tuple[int, int]:
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", str(value or ""))
    if match:
        return int(match.group(1)), int(match.group(2))
    now = datetime.now()
    return now.year, now.month


def campaign_catalog_detail(
    snapshot: dict[str, Any],
    *,
    account: str = "",
    campaign: str = "",
) -> dict[str, Any]:
    """Return every current-period campaign available in the meeting scope."""
    period = str(snapshot.get("period") or "")
    details = snapshot.get("details", {}) if isinstance(snapshot.get("details"), dict) else {}
    payload = details.get("campaigns", {}) if isinstance(details.get("campaigns"), dict) else {}
    account_filter = normalize(str(account or ""))
    campaign_filter = normalize(str(campaign or ""))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    canonical_fallback = False
    for row in payload.get("data", []):
        if not isinstance(row, dict) or str(row.get("latest_period") or "") != period:
            continue
        row_account = str(row.get("account") or "").strip()
        code = str(row.get("campaign_bi") or "").strip()
        name = str(row.get("campaign") or "").strip()
        if account_filter and normalize(row_account) != account_filter:
            continue
        if campaign_filter and campaign_filter not in {normalize(code), normalize(name)}:
            continue
        key = normalize(code or f"{row_account}:{name}")
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append({
            "campaign_bi": code,
            "campaign": name,
            "account": row_account,
            "status": str(row.get("status") or ""),
            "operation_type": str(row.get("operation_type") or ""),
            "latest_period": str(row.get("latest_period") or ""),
            "latest_income": row.get("latest_income"),
            "latest_agents": row.get("latest_agents"),
        })

    # Canonical JSON snapshots may expose scope options without the full SIFO
    # catalog payload. Keep those campaign names queryable too.
    if not rows:
        for item in financial_scope_options(snapshot).get("campaigns", []):
            row_account = str(item.get("account") or "")
            code = str(item.get("value") or "")
            if account_filter and normalize(row_account) != account_filter:
                continue
            if campaign_filter and normalize(code) != campaign_filter:
                continue
            rows.append({
                "campaign_bi": code,
                "campaign": str(item.get("label") or code),
                "account": row_account,
                "status": "",
                "operation_type": str(item.get("operation_type") or ""),
                "latest_period": period,
            })

    # The alternate canonical contract may contain campaign-level records only
    # and no SIFO 1.1 catalog detail. In that case the top-level records are the
    # authoritative campaign list, not an empty result.
    if not rows and not payload.get("data"):
        canonical_fallback = True
        for item in snapshot.get("campaigns", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            identifier = str(item.get("id") or name).strip()
            if account_filter and account_filter not in {normalize(name), normalize(identifier)}:
                continue
            if campaign_filter and campaign_filter not in {normalize(name), normalize(identifier)}:
                continue
            if not name:
                continue
            rows.append({
                "campaign_bi": identifier,
                "campaign": name,
                "account": "Catálogo canónico",
                "status": "",
                "operation_type": "",
                "latest_period": period,
            })

    rows.sort(key=lambda row: (
        normalize(str(row.get("account") or "")),
        normalize(str(row.get("campaign_bi") or "")),
    ))
    by_account: dict[str, int] = {}
    for row in rows:
        row_account = str(row.get("account") or "Sin cuenta")
        by_account[row_account] = by_account.get(row_account, 0) + 1
    coverage = snapshot.get("coverage", {}) if isinstance(snapshot.get("coverage"), dict) else {}
    return round_financial_values({
        "ok": True,
        "section": "campaigns",
        "source": snapshot.get("source"),
        "period": period,
        "total_current": len(rows),
        "total_catalog": int(number(coverage.get("catalog_campaigns"))) or len(rows),
        "canonical_fallback": canonical_fallback,
        "by_account": by_account,
        "results": rows,
    })


def coverage_detail(snapshot: dict[str, Any]) -> dict[str, Any]:
    details = snapshot.get("details", {}) if isinstance(snapshot.get("details"), dict) else {}
    coverage = snapshot.get("coverage", {}) if isinstance(snapshot.get("coverage"), dict) else {}
    available_sections = sorted(details)
    if coverage.get("payroll_detail_mode") and "payroll_detail" not in available_sections:
        available_sections.append("payroll_detail")
    return {
        "ok": True,
        "section": "coverage",
        "source": snapshot.get("source"),
        "period": snapshot.get("period"),
        "coverage": coverage,
        "available_sections": sorted(available_sections),
    }


def format_official_answer(section: str, detail: dict[str, Any]) -> str:
    """Produce a useful, grounded answer even when the LLM is unavailable."""
    if not detail.get("ok", True):
        return str(detail.get("error") or f"El detalle de {SECTION_LABELS.get(section, section)} no está disponible.")
    if section == "campaigns":
        return format_campaign_answer(detail)
    if section == "coverage":
        return format_coverage_answer(detail)
    if section == "payroll_detail":
        return format_payroll_detail_answer(detail)

    label = SECTION_LABELS.get(section, section)
    period = detail.get("period")
    if isinstance(period, dict):
        period = f"{period.get('year', '')}-{int(number(period.get('month'))):02d}"
    prefix = f"SIFO, {period}: " if period else "SIFO: "
    total = detail.get("people_total") if section == "agents" else detail.get("total")
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}

    if section == "history":
        return f"{prefix}hay {int(number(total))} períodos disponibles en el histórico financiero."
    if section == "agents":
        groups = int(number(detail.get("total_groups") or detail.get("total")))
        return (
            f"{prefix}la dotación registrada es de {format_value(total)} personas "
            f"en {groups} grupos de cuenta, campaña, rol y estado."
        )
    if section == "executive" and isinstance(detail.get("summary"), dict):
        executive = detail["summary"]
        return (
            f"{prefix}ingreso {format_value(executive.get('income'))}, margen bruto "
            f"{format_value(executive.get('gross_margin_pct'))}%, dotación efectiva "
            f"{format_value(executive.get('effective_agents'))} y ejecución presupuestal "
            f"{format_value(executive.get('budget_execution_pct'))}%."
        )
    if section == "ifc":
        close = detail.get("close") or "pre-cierre/no informado"
        return (
            f"IFC {detail.get('period', '')}: cobertura {format_value(detail.get('coveragePct'))}%, "
            f"{format_value(detail.get('coveredUnits'))} de {format_value(detail.get('totalUnits'))} unidades; "
            f"estado {close}."
        )
    if section == "ifc_annual":
        return (
            f"IFC anual {detail.get('year', '')} hasta el mes {detail.get('through_month', detail.get('throughMonth', ''))}: "
            f"{len(detail.get('units', detail.get('lines', [])))} líneas/unidades consolidadas disponibles."
        )
    if summary:
        rendered = ", ".join(
            f"{humanize_key(key)} {format_value(value)}"
            for key, value in list(summary.items())[:6]
            if isinstance(value, (int, float))
        )
        if rendered:
            return f"{prefix}{label}: {rendered}."
    if total is not None:
        return f"{prefix}{label}: {format_value(total)} registros oficiales disponibles."
    return f"{prefix}el detalle oficial de {label} está disponible y fue consultado correctamente."


def format_campaign_answer(detail: dict[str, Any]) -> str:
    rows = detail.get("results", [])
    total_current = int(number(detail.get("total_current")))
    total_catalog = int(number(detail.get("total_catalog")))
    period = str(detail.get("period") or "el corte actual")
    if not rows:
        return f"SIFO no registra campañas con datos en {period} para ese filtro."
    if detail.get("canonical_fallback"):
        names = [str(row.get("campaign") or row.get("campaign_bi") or "") for row in rows]
        return f"SIFO registra {len(rows)} campañas en {period}: {', '.join(names)}."
    accounts = detail.get("by_account", {})
    if len(accounts) == 1:
        account = next(iter(accounts))
        names = [str(row.get("campaign_bi") or row.get("campaign") or "") for row in rows]
        return f"{account} tiene {len(rows)} campañas con datos en {period}: {', '.join(names)}."
    distribution = "; ".join(f"{account}: {count}" for account, count in accounts.items())
    catalog_note = (
        f" El catálogo completo contiene {total_catalog}."
        if total_catalog and total_catalog != total_current
        else ""
    )
    return (
        f"SIFO registra {total_current} campañas con datos en {period}.{catalog_note} "
        f"Por cuenta: {distribution}."
    )


def format_payroll_detail_answer(detail: dict[str, Any]) -> str:
    rows = detail.get("results", []) if isinstance(detail.get("results"), list) else []
    total = int(number(detail.get("total")))
    page = int(number(detail.get("page"))) or 1
    period = detail.get("period", {})
    if isinstance(period, dict):
        period_label = f"{int(number(period.get('year'))):04d}-{int(number(period.get('month'))):02d}"
    else:
        period_label = str(period or "el corte actual")
    if not rows:
        return f"SIFO no encontró trabajadores en la planilla de {period_label} para ese filtro."
    if total == 1:
        row = rows[0]
        name = row.get("full_name") or row.get("name") or "Trabajador"
        return (
            f"Planilla {period_label} de {name}: DNI {row.get('dni') or 'no informado'}, "
            f"código {row.get('employee_code') or 'no informado'}, cargo {row.get('job_title') or row.get('position') or 'no informado'}, "
            f"campaña {row.get('campaign_bi') or row.get('campaign') or 'no informada'}, remuneración S/ {format_value(row.get('remuneration', row.get('salary')))}, "
            f"sueldo total S/ {format_value(row.get('total_salary'))} y costo total empleador S/ {format_value(row.get('total_employer_cost', row.get('total_cost')))}."
        )
    preview = "; ".join(
        f"{row.get('full_name') or row.get('name') or 'Sin nombre'} "
        f"({row.get('campaign_bi') or row.get('campaign') or 'sin campaña'}: S/ "
        f"{format_value(row.get('total_employer_cost', row.get('total_cost')))})"
        for row in rows[:5]
    )
    return (
        f"SIFO tiene {total} registros individuales de planilla en {period_label}. "
        f"Página {page}: {preview}. Puedes pedir un nombre, DNI, código, campaña o página específica."
    )


def format_coverage_answer(detail: dict[str, Any]) -> str:
    coverage = detail.get("coverage", {}) if isinstance(detail.get("coverage"), dict) else {}
    sections = detail.get("available_sections", [])
    return (
        f"Tengo acceso de lectura a {len(sections)} secciones de SIFO para {detail.get('period', 'el corte actual')}: "
        f"{format_value(coverage.get('accounts'))} cuentas, {format_value(coverage.get('catalog_campaigns'))} campañas de catálogo, "
        f"{format_value(coverage.get('agents'))} personas, {format_value(coverage.get('historical_periods'))} períodos históricos, "
        f"además de planilla individual autorizada, facturación, presupuesto, KPI, ratios, operación, asistencia, "
        f"calidad de datos e IFC mensual y anual."
    )


def humanize_key(value: str) -> str:
    return value.replace("_", " ")


def format_value(value: Any) -> str:
    if value is None:
        return "no informado"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, (int, float)):
        return f"{float(value):,.2f}".rstrip("0").rstrip(".").replace(",", " ")
    return str(value)


def detail_for_prompt(detail: dict[str, Any], max_chars: int = 24_000) -> str:
    """Serialize the bounded, privacy-filtered official tool response."""
    rendered = json.dumps(detail, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= max_chars:
        return rendered
    compact = dict(detail)
    results = compact.get("results")
    if isinstance(results, list):
        compact["results"] = results[:10]
        compact["results_truncated_for_prompt"] = len(results) - len(compact["results"])
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)[:max_chars]
