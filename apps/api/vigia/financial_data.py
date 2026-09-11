from __future__ import annotations

import calendar
import copy
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "bi_snapshot.json"
SUPPORTED_PROVIDERS = {"json", "http"}
FINANCIAL_DECIMAL_PLACES = 2
PERSONAL_FIELD_KEYS = {
    "dni", "fullname", "employeecode", "personkey", "agentname",
    "supervisorname", "supervisornames", "supervisor", "reviewedby",
    "calculatedby", "approvedby", "createdby", "updatedby",
}
DETAIL_SECTIONS = {
    "campaigns", "agents", "workforce", "billing", "budget", "kpis", "history",
    "executive", "ratios", "operations", "operational_metrics", "attendance",
    "data_quality", "ifc", "ifc_annual", "payroll_detail",
}
SIFO_REQUIRED_SECTIONS = {
    "dashboard", "history", "campaigns", "agents", "billing", "budget", "kpis",
    "executive", "ratios", "operations", "operational_metrics", "attendance",
    "data_quality", "ifc", "ifc_annual",
}
REMOTE_PAGE_LIMITS = {
    "campaigns": 100,
    "agents": 200,
    "workforce": 200,
    "operations": 1000,
    "operational_metrics": 500,
    "attendance": 500,
}


class FinancialDataError(RuntimeError):
    """Raised when the authoritative financial source cannot be read safely."""


class FinancialDataService:
    """Read and cache the canonical snapshot used to fact-check a meeting.

    The service deliberately exposes a small canonical contract to the rest of
    VigIA. An internal financial system can change independently as long as its
    API adapter returns this contract. Failed refreshes never replace the last
    valid snapshot with partial or malformed data.
    """

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        default_snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
    ) -> None:
        self.project_root = project_root.resolve()
        self.default_snapshot_path = default_snapshot_path.resolve()
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None
        self._loaded_monotonic: float | None = None
        self._last_refresh_at: str | None = None
        self._last_error: str | None = None
        self._provider_used: str | None = None

    @property
    def provider(self) -> str:
        configured = os.getenv("VIGIA_DATA_PROVIDER", "auto").strip().lower()
        if configured == "auto":
            return "http" if os.getenv("SEGUIMIENTO_FINANCIERO_URL", "").strip() else "json"
        if configured not in SUPPORTED_PROVIDERS:
            raise FinancialDataError(
                f"VIGIA_DATA_PROVIDER must be one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
            )
        return configured

    def get_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            refresh_seconds = env_int("VIGIA_DATA_REFRESH_SECONDS", 60)
            cache_valid = (
                self._snapshot is not None
                and self._loaded_monotonic is not None
                and (refresh_seconds <= 0 or time.monotonic() - self._loaded_monotonic < refresh_seconds)
            )
            if cache_valid and not force_refresh:
                return copy.deepcopy(self._snapshot)

            try:
                provider = self.provider
                raw_snapshot = self._load_from_provider(provider)
                snapshot = validate_financial_snapshot(raw_snapshot)
            except Exception as exc:
                self._last_error = safe_error(exc)
                if self._snapshot is not None:
                    return copy.deepcopy(self._snapshot)
                if isinstance(exc, FinancialDataError):
                    raise
                raise FinancialDataError(self._last_error) from exc

            self._snapshot = snapshot
            self._provider_used = provider
            self._loaded_monotonic = time.monotonic()
            self._last_refresh_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            return copy.deepcopy(snapshot)

    def status(self) -> dict[str, Any]:
        with self._lock:
            generated_at = None
            source = None
            period = None
            account_count = 0
            age_seconds = None
            if self._snapshot:
                generated_at = self._snapshot.get("generated_at")
                source = self._snapshot.get("source")
                period = self._snapshot.get("period")
                account_count = len(self._snapshot.get("campaigns", []))
                age_seconds = snapshot_age_seconds(str(generated_at))

            details = self._snapshot.get("details", {}) if self._snapshot else {}
            available_sections = sorted(details) if isinstance(details, dict) else []
            missing_sections = sorted(SIFO_REQUIRED_SECTIONS.difference(available_sections))
            coverage = copy.deepcopy(self._snapshot.get("coverage", {})) if self._snapshot else {}

            provider = self._provider_used
            try:
                provider = provider or self.provider
            except FinancialDataError:
                provider = os.getenv("VIGIA_DATA_PROVIDER", "invalid")

            max_age_seconds = env_int(
                "VIGIA_DATA_MAX_AGE_SECONDS",
                900 if provider == "http" else 0,
            )
            stale = bool(
                age_seconds is not None
                and max_age_seconds > 0
                and age_seconds > max_age_seconds
            )
            available = self._snapshot is not None

            return {
                "provider": provider,
                "available": available,
                "trusted": available and not stale,
                "stale": stale,
                "source": source,
                "period": period,
                "account_count": account_count,
                "complete": available and not missing_sections,
                "available_sections": available_sections,
                "missing_sections": missing_sections,
                "coverage": coverage,
                "generated_at": generated_at,
                "age_seconds": round(age_seconds) if age_seconds is not None else None,
                "max_age_seconds": max_age_seconds,
                "last_refresh_at": self._last_refresh_at,
                "last_error": self._last_error,
            }

    def reset_cache(self) -> None:
        """Clear process-local cache. Intended for tests and operator refreshes."""
        with self._lock:
            self._snapshot = None
            self._loaded_monotonic = None
            self._last_refresh_at = None
            self._last_error = None
            self._provider_used = None

    def get_snapshot_for_scope(
        self,
        *,
        accounts: list[str] | None = None,
        campaigns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build the canonical fact-check snapshot for one meeting scope.

        Account-only scopes can be restricted safely from the cached canonical
        snapshot. Campaign scopes are recalculated by SIFO so VigIA never uses
        the total account amount when the meeting selected only one campaign.
        """
        snapshot = self.get_snapshot()
        scope = normalize_financial_scope(
            snapshot,
            accounts=accounts or [],
            campaigns=campaigns or [],
        )
        if not scope["accounts"] and not scope["campaigns"]:
            return with_financial_scope(snapshot, scope)

        try:
            url, headers = self._http_connection()
            if is_seguimiento_external_api_url(url):
                return self._load_scoped_seguimiento_snapshot(snapshot, scope, headers)
        except FinancialDataError:
            if scope["campaigns"]:
                raise

        return restrict_snapshot_to_scope(snapshot, scope)

    def _load_scoped_seguimiento_snapshot(
        self,
        snapshot: dict[str, Any],
        scope: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        base_url = seguimiento_external_api_base(os.getenv("SEGUIMIENTO_FINANCIERO_URL", ""))
        year, month = parse_period(snapshot.get("period"))
        query: dict[str, Any] = {"year": year, "month": month, "currency": "PEN"}
        if scope["accounts"]:
            query["account"] = ",".join(scope["accounts"])
        if scope["campaigns"]:
            query["campaign"] = ",".join(scope["campaigns"])
        dashboard = self._request_json(url_with_query(f"{base_url}/dashboard", query), headers)
        if not isinstance(dashboard, dict) or not dashboard.get("by_account"):
            raise FinancialDataError("El alcance seleccionado no tiene datos financieros en el período vigente")

        details = snapshot.get("details", {}) if isinstance(snapshot.get("details"), dict) else {}
        budget_payload = filter_scope_payload(details.get("budget"), scope)
        campaign_payload = filter_scope_payload(details.get("campaigns"), scope)
        history_dashboards: list[tuple[str, dict[str, Any]]] = []
        if not scope["campaigns"]:
            for item in details.get("history", []):
                if not isinstance(item, dict):
                    continue
                period = str(item.get("period") or "")
                rows = [
                    row for row in item.get("by_account", [])
                    if isinstance(row, dict) and scope_includes_account(scope, row.get("account"))
                ]
                if period and rows:
                    history_dashboards.append((period, {"summary": item.get("summary", {}), "by_account": rows}))

        scoped = transform_seguimiento_dashboard(
            dashboard,
            period=f"{year:04d}-{month:02d}",
            budget_payload=budget_payload,
            history_dashboards=history_dashboards,
            campaign_payload=campaign_payload,
            fetched_at=str(dashboard.get("generated_at") or snapshot.get("generated_at") or ""),
        )
        add_campaign_scope_aliases(scoped, campaign_payload, scope)
        return with_financial_scope(validate_financial_snapshot(scoped), scope)

    def query_official_details(
        self,
        *,
        section: str,
        account: str = "",
        campaign: str = "",
        year: int | None = None,
        month: int | None = None,
        date_from: str = "",
        date_to: str = "",
        search: str = "",
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return bounded, read-only detail from every official API dataset.

        Large datasets are never placed wholesale in the model context. Personal
        payroll detail requires SIFO's explicit ``agents.read_sensitive`` API
        permission and is returned only as the requested, audited, bounded page.
        Cached financial datasets are filtered locally so most voice questions
        do not incur another network round trip.
        """
        section = str(section or "").strip().lower()
        if section not in DETAIL_SECTIONS:
            return {"ok": False, "error": "Sección financiera no reconocida."}

        snapshot = self.get_snapshot()
        period_year, period_month = parse_period(snapshot.get("period"))
        selected_year = valid_year(year, period_year)
        selected_month = valid_month(month, period_month)
        selected_page = max(1, int(page or 1))
        selected_limit = max(1, min(int(limit or 50), 100))

        if section == "billing" and (year is not None or month is not None):
            base_url, headers = self._external_api_connection()
            payload = self._request_json(
                url_with_query(
                    f"{base_url}/billing",
                    {"year": selected_year, "month": selected_month},
                ),
                headers,
            )
            return query_detail_payload(
                payload,
                section=section,
                account=account,
                campaign=campaign,
                page=selected_page,
                limit=selected_limit,
            )
        if section == "budget" and year is not None and selected_year != period_year:
            base_url, headers = self._external_api_connection()
            payload = self._request_json(
                url_with_query(f"{base_url}/budget", {"year": selected_year}), headers
            )
            return query_detail_payload(
                payload,
                section=section,
                account=account,
                campaign=campaign,
                page=selected_page,
                limit=selected_limit,
            )
        dynamic_query: dict[str, Any] | None = None
        dynamic_endpoint = ""
        if section == "payroll_detail":
            dynamic_endpoint = "agents-sensitive"
            dynamic_query = {
                "year": selected_year,
                "month": selected_month,
                "page": selected_page,
                "limit": selected_limit,
            }
            if account.strip():
                dynamic_query["account"] = account.strip()
            if campaign.strip():
                dynamic_query["campaign"] = resolve_campaign_code(snapshot, campaign, account) or campaign.strip()
            if search.strip():
                dynamic_query["search"] = " ".join(search.split())[:500]
        elif section == "executive" and any(
            [account.strip(), campaign.strip(), year is not None, month is not None, date_from.strip(), date_to.strip()]
        ):
            dynamic_endpoint = "executive-snapshot"
            dynamic_query = {
                "period_end": date_to.strip()[:7] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to.strip())
                else f"{selected_year:04d}-{selected_month:02d}",
            }
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from.strip()):
                dynamic_query["period_start"] = date_from.strip()[:7]
            if account.strip():
                dynamic_query["account"] = account.strip()
            if campaign.strip():
                dynamic_query["campaign"] = resolve_campaign_code(snapshot, campaign, account) or campaign.strip()
        elif section == "ratios" and any([account.strip(), campaign.strip(), year is not None, month is not None]):
            dynamic_endpoint = "ratios"
            dynamic_query = {"period": f"{selected_year:04d}-{selected_month:02d}"}
            if account.strip():
                dynamic_query["account"] = account.strip()
            if campaign.strip():
                dynamic_query["campaign"] = resolve_campaign_code(snapshot, campaign, account) or campaign.strip()
        elif section in {"operations", "operational_metrics", "attendance"}:
            dynamic_endpoint = {
                "operations": "operations",
                "operational_metrics": "operational-metrics",
                "attendance": "attendance",
            }[section]
            dynamic_query = {}
            if date_from.strip():
                dynamic_query["from"] = date_from.strip()
            if date_to.strip():
                dynamic_query["to"] = date_to.strip()
            if account.strip() and section != "operations":
                dynamic_query["account"] = account.strip()
            if campaign.strip():
                if section == "operations":
                    dynamic_query["queue"] = campaign.strip()
                elif section == "operational_metrics":
                    dynamic_query["campaign"] = resolve_campaign_code(snapshot, campaign, account) or campaign.strip()
        elif section == "ifc" and (year is not None or month is not None):
            dynamic_endpoint = "ifc"
            dynamic_query = {"period": f"{selected_year:04d}-{selected_month:02d}", "currency": "PEN"}
        elif section == "ifc_annual" and (year is not None or month is not None):
            dynamic_endpoint = "ifc-annual"
            dynamic_query = {"year": selected_year, "through_month": selected_month, "currency": "PEN", "scope": "GROUP"}

        if dynamic_query is not None:
            if section == "payroll_detail":
                base_url, headers = self._sensitive_api_connection()
            else:
                base_url, headers = self._external_api_connection()
            if section in {"operations", "operational_metrics", "attendance"}:
                dynamic_query.update({"page": selected_page, "limit": selected_limit})
            payload = self._request_json(
                url_with_query(f"{base_url}/{dynamic_endpoint}", dynamic_query), headers
            )
            if not isinstance(payload, dict):
                raise FinancialDataError(f"seguimiento_financiero {dynamic_endpoint} returned an invalid response")
            if section == "payroll_detail":
                return query_sensitive_data_page(
                    payload,
                    section=section,
                    page=selected_page,
                    limit=selected_limit,
                )
            if section in {"operations", "operational_metrics", "attendance"}:
                return query_remote_data_page(
                    payload,
                    section=section,
                    account=account,
                    campaign=campaign,
                    page=selected_page,
                    limit=selected_limit,
                )
            return query_comprehensive_detail(
                {"details": {section: coerce_external_decimal_strings(payload)}},
                section=section,
                account=account,
                campaign=campaign,
                page=selected_page,
                limit=selected_limit,
            )
        if section in {"campaigns", "billing", "budget", "history"}:
            return query_cached_details(
                snapshot,
                section=section,
                account=account,
                campaign=campaign,
                year=year if section == "history" else selected_year,
                month=month if section == "history" else selected_month,
                page=selected_page,
                limit=selected_limit,
            )

        if section in {
            "executive", "ratios", "operations", "operational_metrics", "attendance",
            "data_quality", "ifc", "ifc_annual",
        }:
            return query_comprehensive_detail(
                snapshot,
                section=section,
                account=account,
                campaign=campaign,
                page=selected_page,
                limit=selected_limit,
            )

        base_url, headers = self._external_api_connection()
        campaign_code = resolve_campaign_code(snapshot, campaign, account)
        if section in {"agents", "workforce"}:
            query: dict[str, Any] = {
                "year": selected_year,
                "month": selected_month,
                "page": selected_page,
                "limit": selected_limit,
            }
            if account.strip():
                query["account"] = account.strip()
            if campaign_code:
                query["campaign"] = campaign_code
            elif campaign.strip():
                query["campaign"] = campaign.strip()
            try:
                payload = self._request_json(url_with_query(f"{base_url}/workforce", query), headers)
            except FinancialDataError:
                # /agents is the compatibility alias retained by both SIFO
                # backends. It also keeps VigIA compatible during rollout.
                payload = self._request_json(url_with_query(f"{base_url}/agents", query), headers)
            if not isinstance(payload, dict):
                raise FinancialDataError("seguimiento_financiero workforce returned an invalid response")
            return query_remote_data_page(
                payload,
                section="agents",
                account=account,
                campaign=campaign,
                page=selected_page,
                limit=selected_limit,
                total_key="total_groups",
            )

        if not campaign_code:
            return {
                "ok": False,
                "section": section,
                "error": "Para consultar KPI indica una campaña específica.",
            }
        last_day = calendar.monthrange(selected_year, selected_month)[1]
        query = {
            "campaign": campaign_code,
            "from": date_from.strip() or "2000-01-01",
            "to": date_to.strip() or f"{selected_year:04d}-{selected_month:02d}-{last_day:02d}",
        }
        payload = self._request_json(url_with_query(f"{base_url}/kpis", query), headers)
        if not isinstance(payload, dict):
            raise FinancialDataError("seguimiento_financiero kpis returned an invalid response")
        return round_financial_values({"ok": True, "section": section, **payload})

    def _load_from_provider(self, provider: str) -> dict[str, Any]:
        if provider == "json":
            return self._load_json()
        if provider == "http":
            return self._load_http()
        raise FinancialDataError(f"Unsupported financial data provider: {provider}")

    def _load_json(self) -> dict[str, Any]:
        configured = os.getenv("VIGIA_BI_SNAPSHOT_PATH", "").strip()
        path = Path(configured).expanduser() if configured else self.default_snapshot_path
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve()
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise FinancialDataError(f"Financial snapshot not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise FinancialDataError(f"Financial snapshot contains invalid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise FinancialDataError("Financial snapshot root must be a JSON object")
        return payload

    def _load_http(self) -> dict[str, Any]:
        url, headers = self._http_connection()
        if is_seguimiento_external_api_url(url):
            return self._load_seguimiento_external_api(url, headers)

        payload = self._request_json(url, headers)
        payload = unwrap_payload(payload)
        if not isinstance(payload, dict):
            raise FinancialDataError("seguimiento_financiero response must contain a snapshot object")
        return payload

    def _http_connection(self) -> tuple[str, dict[str, str]]:
        url = os.getenv("SEGUIMIENTO_FINANCIERO_URL", "").strip()
        if not url:
            raise FinancialDataError(
                "SEGUIMIENTO_FINANCIERO_URL is required when VIGIA_DATA_PROVIDER=http"
            )
        parsed_url = urlparse(url)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        allow_insecure = os.getenv("VIGIA_ALLOW_INSECURE_DATA_URL", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise FinancialDataError("SEGUIMIENTO_FINANCIERO_URL must be a valid HTTP(S) URL")
        if parsed_url.scheme != "https" and parsed_url.hostname not in local_hosts and not allow_insecure:
            raise FinancialDataError(
                "seguimiento_financiero must use HTTPS outside localhost"
            )

        headers = {
            "Accept": "application/json",
            "User-Agent": "vigia-a365/2.1",
        }
        token = os.getenv("SEGUIMIENTO_FINANCIERO_TOKEN", "").strip()
        api_key = os.getenv("SEGUIMIENTO_FINANCIERO_API_KEY", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if api_key:
            headers["X-API-Key"] = api_key
        return url, headers

    def _external_api_connection(self) -> tuple[str, dict[str, str]]:
        url, headers = self._http_connection()
        if not is_seguimiento_external_api_url(url):
            raise FinancialDataError(
                "El detalle completo requiere la API oficial /api/external de seguimiento_financiero"
            )
        return seguimiento_external_api_base(url), headers

    def _sensitive_api_connection(self) -> tuple[str, dict[str, str]]:
        """Use an optional private SIFO URL only for permission-gated payroll."""
        primary_base, headers = self._external_api_connection()
        configured = os.getenv("SEGUIMIENTO_FINANCIERO_SENSITIVE_URL", "").strip()
        if not configured:
            return primary_base, headers
        parsed_url = urlparse(configured)
        allow_insecure = os.getenv(
            "VIGIA_ALLOW_INSECURE_SENSITIVE_DATA_URL", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise FinancialDataError(
                "SEGUIMIENTO_FINANCIERO_SENSITIVE_URL must be a valid HTTP(S) URL"
            )
        if parsed_url.scheme != "https" and parsed_url.hostname not in {
            "127.0.0.1", "localhost", "::1"
        } and not allow_insecure:
            raise FinancialDataError(
                "Sensitive SIFO data must use HTTPS outside localhost"
            )
        if not is_seguimiento_external_api_url(configured):
            raise FinancialDataError(
                "El detalle de planilla requiere una API oficial /api/external de SIFO"
            )
        return seguimiento_external_api_base(configured), headers

    def _request_json(self, url: str, headers: dict[str, str]) -> Any:
        request = Request(url, headers=headers, method="GET")
        timeout = env_float("VIGIA_DATA_TIMEOUT_SECONDS", 5.0)
        try:
            with urlopen(request, timeout=max(1.0, timeout)) as response:
                body = response.read()
        except HTTPError as exc:
            raise FinancialDataError(f"seguimiento_financiero returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise FinancialDataError("seguimiento_financiero is unreachable") from exc
        except TimeoutError as exc:
            raise FinancialDataError("seguimiento_financiero timed out") from exc

        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinancialDataError("seguimiento_financiero returned invalid JSON") from exc

    def _load_seguimiento_external_api(
        self,
        configured_url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        base_url = seguimiento_external_api_base(configured_url)
        configured_period = os.getenv("SEGUIMIENTO_FINANCIERO_PERIOD", "").strip()
        if configured_period and not re.fullmatch(r"\d{4}-\d{2}", configured_period):
            raise FinancialDataError("SEGUIMIENTO_FINANCIERO_PERIOD must use YYYY-MM")

        if configured_period:
            year, month = (int(part) for part in configured_period.split("-"))
            candidates = [(year, month)]
        else:
            now = datetime.now(timezone.utc)
            lookback = max(1, min(env_int("SEGUIMIENTO_FINANCIERO_LOOKBACK_MONTHS", 24), 60))
            candidates = [shift_month(now.year, now.month, -offset) for offset in range(lookback)]

        selected_dashboard: dict[str, Any] | None = None
        selected_period: tuple[int, int] | None = None
        for year, month in candidates:
            payload = self._request_json(
                url_with_query(f"{base_url}/dashboard", {"year": year, "month": month}),
                headers,
            )
            if not isinstance(payload, dict):
                raise FinancialDataError("seguimiento_financiero dashboard returned an invalid response")
            if payload.get("by_account"):
                selected_dashboard = payload
                selected_period = (year, month)
                break

        if selected_dashboard is None or selected_period is None:
            raise FinancialDataError("seguimiento_financiero has no financial data in the configured period")

        year, month = selected_period
        history_dashboards = cached_history_dashboards(self._snapshot, f"{year:04d}-{month:02d}")
        if not history_dashboards:
            history_months = max(0, min(env_int("VIGIA_FINANCIAL_HISTORY_MONTHS", 36), 60))
            for offset in range(history_months, 0, -1):
                history_year, history_month = shift_month(year, month, -offset)
                try:
                    payload = self._request_json(
                        url_with_query(
                            f"{base_url}/dashboard",
                            {"year": history_year, "month": history_month},
                        ),
                        headers,
                    )
                except FinancialDataError:
                    continue
                if isinstance(payload, dict) and payload.get("by_account"):
                    history_dashboards.append(
                        (f"{history_year:04d}-{history_month:02d}", payload)
                    )

        budget_payload: dict[str, Any] | None = None
        try:
            candidate_budget = self._request_json(
                url_with_query(f"{base_url}/budget", {"year": year}),
                headers,
            )
            if isinstance(candidate_budget, dict):
                budget_payload = candidate_budget
        except FinancialDataError:
            # Dashboard data remains useful and authoritative even when the
            # optional budget endpoint is temporarily unavailable.
            budget_payload = None

        campaign_payload: dict[str, Any] | None = None
        billing_payload: dict[str, Any] | None = None
        agents_payload: dict[str, Any] | None = None
        try:
            campaign_payload = self._request_all_pages(
                f"{base_url}/campaigns", headers, limit=100
            )
        except FinancialDataError:
            campaign_payload = cached_detail_payload(self._snapshot, "campaigns")
        try:
            candidate_billing = self._request_json(
                url_with_query(f"{base_url}/billing", {"year": year, "month": month}),
                headers,
            )
            billing_payload = candidate_billing if isinstance(candidate_billing, dict) else None
        except FinancialDataError:
            billing_payload = cached_detail_payload(self._snapshot, "billing")
        try:
            workforce_query = {"year": year, "month": month, "page": 1, "limit": 1}
            try:
                candidate_agents = self._request_json(
                    url_with_query(f"{base_url}/workforce", workforce_query), headers
                )
            except FinancialDataError:
                candidate_agents = self._request_json(
                    url_with_query(f"{base_url}/agents", workforce_query), headers
                )
            if isinstance(candidate_agents, dict):
                agents_payload = {
                    **{key: value for key, value in candidate_agents.items() if key != "data"},
                    "total": candidate_agents.get("total", 0),
                    "total_groups": candidate_agents.get("total_groups", 0),
                    "data": [],
                    "access": "on_demand_paginated",
                }
        except FinancialDataError:
            agents_payload = cached_detail_payload(self._snapshot, "agents")

        comprehensive_payloads: dict[str, dict[str, Any]] = {}
        comprehensive_requests = {
            "executive": url_with_query(
                f"{base_url}/executive-snapshot",
                {"period_end": f"{year:04d}-{month:02d}", "currency": "PEN"},
            ),
            "ratios": url_with_query(
                f"{base_url}/ratios", {"period": f"{year:04d}-{month:02d}"}
            ),
            "operations": url_with_query(
                f"{base_url}/operations", {"page": 1, "limit": 1}
            ),
            "operational_metrics": url_with_query(
                f"{base_url}/operational-metrics", {"page": 1, "limit": 1}
            ),
            "attendance": url_with_query(
                f"{base_url}/attendance", {"page": 1, "limit": 1}
            ),
            "data_quality": f"{base_url}/data-quality",
            "ifc": url_with_query(
                f"{base_url}/ifc", {"period": f"{year:04d}-{month:02d}", "currency": "PEN"}
            ),
            "ifc_annual": url_with_query(
                f"{base_url}/ifc-annual",
                {"year": year, "through_month": month, "currency": "PEN", "scope": "GROUP"},
            ),
        }
        with ThreadPoolExecutor(max_workers=len(comprehensive_requests)) as executor:
            pending = {
                executor.submit(self._request_json, endpoint, headers): section
                for section, endpoint in comprehensive_requests.items()
            }
            for future in as_completed(pending):
                section = pending[future]
                try:
                    payload = future.result()
                    if isinstance(payload, dict):
                        if section in {"operations", "operational_metrics", "attendance"}:
                            payload = {
                                **{key: value for key, value in payload.items() if key != "data"},
                                "data": [],
                                "access": "on_demand_paginated",
                            }
                        comprehensive_payloads[section] = coerce_external_decimal_strings(payload)
                except FinancialDataError:
                    cached = cached_detail_payload(self._snapshot, section)
                    if cached is not None:
                        comprehensive_payloads[section] = cached

        return transform_seguimiento_dashboard(
            selected_dashboard,
            period=f"{year:04d}-{month:02d}",
            budget_payload=budget_payload,
            history_dashboards=history_dashboards,
            campaign_payload=campaign_payload,
            billing_payload=billing_payload,
            agents_payload=agents_payload,
            comprehensive_payloads=comprehensive_payloads,
            fetched_at=str(selected_dashboard.get("generated_at") or "") or None,
        )

    def _request_all_pages(
        self,
        url: str,
        headers: dict[str, str],
        *,
        limit: int,
        total_key: str = "total",
    ) -> dict[str, Any]:
        first = self._request_json(url_with_query(url, {"page": 1, "limit": limit}), headers)
        if not isinstance(first, dict):
            raise FinancialDataError("seguimiento_financiero returned an invalid paginated response")
        rows = [row for row in first.get("data", []) if isinstance(row, dict)]
        total = max(len(rows), int(number(first.get(total_key))))
        effective_limit = int(number(first.get("limit"))) or limit
        page_count = (total + effective_limit - 1) // effective_limit
        for page in range(2, page_count + 1):
            if len(rows) >= total:
                break
            payload = self._request_json(
                url_with_query(url, {"page": page, "limit": effective_limit}), headers
            )
            if not isinstance(payload, dict):
                raise FinancialDataError("seguimiento_financiero returned an invalid paginated response")
            page_rows = [row for row in payload.get("data", []) if isinstance(row, dict)]
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(rows) >= total:
                break
        if len(rows) < total:
            raise FinancialDataError(
                f"seguimiento_financiero returned {len(rows)} of {total} expected rows"
            )
        result = copy.deepcopy(first)
        result.update({"page": 1, "limit": effective_limit, total_key: total, "data": rows})
        return result


def unwrap_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    for key in ("snapshot", "data", "result"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and "campaigns" in candidate:
            return candidate
    return payload


def is_seguimiento_external_api_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path.endswith("/api/external") or path.endswith("/api/external/dashboard")


def seguimiento_external_api_base(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path.endswith("/dashboard"):
        path = path[: -len("/dashboard")]
    return parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")


def url_with_query(url: str, values: dict[str, Any]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in values.items()})
    return parsed._replace(query=urlencode(query)).geturl()


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute_month = year * 12 + (month - 1) + offset
    shifted_year, shifted_month = divmod(absolute_month, 12)
    return shifted_year, shifted_month + 1


def parse_period(value: Any) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(value or "").strip())
    if not match:
        now = datetime.now(timezone.utc)
        return now.year, now.month
    return int(match.group(1)), int(match.group(2))


def valid_year(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 2000 <= parsed <= 2200 else default


def valid_month(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= 12 else default


def round_financial_values(value: Any, digits: int = FINANCIAL_DECIMAL_PLACES) -> Any:
    """Round every finite decimal recursively while preserving ids and integers."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return round(value, digits) if value == value and abs(value) != float("inf") else 0.0
    if isinstance(value, dict):
        return {key: round_financial_values(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [round_financial_values(item, digits) for item in value]
    if isinstance(value, tuple):
        return tuple(round_financial_values(item, digits) for item in value)
    return value


def coerce_external_decimal_strings(value: Any) -> Any:
    """Convert PostgreSQL numeric JSON strings before enforcing two decimals."""
    if isinstance(value, str) and re.fullmatch(r"-?\d+\.\d+", value.strip()):
        return number(value)
    if isinstance(value, dict):
        return {key: coerce_external_decimal_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [coerce_external_decimal_strings(item) for item in value]
    return value


def cached_detail_payload(
    snapshot: dict[str, Any] | None,
    section: str,
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    payload = snapshot.get("details", {}).get(section)
    return copy.deepcopy(payload) if isinstance(payload, dict) else None


def cached_history_dashboards(
    snapshot: dict[str, Any] | None,
    current_period: str,
) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(snapshot, dict) or snapshot.get("period") != current_period:
        return []
    entries = snapshot.get("details", {}).get("history", [])
    result: list[tuple[str, dict[str, Any]]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        period = str(entry.get("period", ""))
        if period and isinstance(entry.get("by_account"), list):
            result.append((period, {
                "summary": entry.get("summary", {}),
                "by_account": entry.get("by_account", []),
            }))
    return result


def resolve_campaign_code(snapshot: dict[str, Any], campaign: str, account: str) -> str:
    catalog = snapshot.get("details", {}).get("campaigns", {}).get("data", [])
    requested_campaign = normalize_search(campaign)
    requested_account = normalize_search(account)
    best_code = ""
    for row in catalog if isinstance(catalog, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("campaign_bi") or "").strip()
        campaign_name = normalize_search(row.get("campaign"))
        account_name = normalize_search(row.get("account"))
        if requested_campaign and requested_campaign in {normalize_search(code), campaign_name}:
            return code
        if requested_campaign and (
            requested_campaign in campaign_name or campaign_name in requested_campaign
        ):
            best_code = best_code or code
        elif requested_account and requested_account == account_name:
            best_code = best_code or code
    return best_code


def normalize_search(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").lower())
    return " ".join(
        re.sub(
            r"[^a-z0-9]+",
            " ",
            "".join(char for char in decomposed if not unicodedata.combining(char)),
        ).split()
    )


def query_cached_details(
    snapshot: dict[str, Any],
    *,
    section: str,
    account: str,
    campaign: str,
    year: int | None,
    month: int | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    details = snapshot.get("details", {})
    payload = details.get(section)
    if section == "history":
        rows = payload if isinstance(payload, list) else []
        filtered: list[dict[str, Any]] = []
        account_filter = normalize_search(account)
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            entry_year, entry_month = parse_period(entry.get("period"))
            if year is not None and entry_year != valid_year(year, entry_year):
                continue
            if month is not None and entry_month != valid_month(month, entry_month):
                continue
            copied = copy.deepcopy(entry)
            if account_filter:
                copied["by_account"] = [
                    row for row in copied.get("by_account", [])
                    if account_filter in normalize_search(row.get("account"))
                ]
            filtered.append(copied)
        return paginated_detail_response(section, filtered, page, limit)
    return query_detail_payload(
        payload,
        section=section,
        account=account,
        campaign=campaign,
        page=page,
        limit=limit,
    )


def query_comprehensive_detail(
    snapshot: dict[str, Any],
    *,
    section: str,
    account: str,
    campaign: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    """Return compact decision-grade sections without personal information."""
    payload = snapshot.get("details", {}).get(section)
    if not isinstance(payload, dict):
        return {"ok": False, "section": section, "error": "Detalle no disponible."}
    payload = strip_personal_fields(copy.deepcopy(payload))

    if section == "executive":
        source = payload.get("snapshot", {})
        if not isinstance(source, dict):
            return {"ok": False, "section": section, "error": "Snapshot ejecutivo no disponible."}
        account_filter = normalize_search(account)
        campaign_filter = normalize_search(campaign)

        def matches_scope(row: dict[str, Any]) -> bool:
            row_account = normalize_search(row.get("account_name") or row.get("account"))
            row_campaign = " ".join(
                item for item in [
                    normalize_search(row.get("campaign_bi")),
                    normalize_search(row.get("campaign_name")),
                    normalize_search(row.get("unified_campaign")),
                    normalize_search(row.get("item_name")),
                ] if item
            )
            return (
                (not account_filter or account_filter in row_account)
                and (not campaign_filter or campaign_filter in row_campaign)
            )

        def page_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
            filtered = [row for row in rows if isinstance(row, dict) and matches_scope(row)]
            start = (page - 1) * limit
            return {
                "total": len(filtered),
                "page": page,
                "limit": limit,
                "results": filtered[start:start + limit],
            }

        performance = source.get("performanceTable", {})
        rows = performance.get("rows", []) if isinstance(performance, dict) else []
        campaign_breakdown = source.get("campaignBreakdown", [])
        campaign_breakdown = campaign_breakdown if isinstance(campaign_breakdown, list) else []
        campaign_rows: list[dict[str, Any]] = []
        for group in source.get("campaignsByAccount", []):
            if not isinstance(group, dict):
                continue
            for campaign_row in group.get("campaigns", []):
                if not isinstance(campaign_row, dict):
                    continue
                campaign_rows.append({
                    "account_name": group.get("account_name"),
                    "operation_type": campaign_row.get("operation_type") or group.get("operation_type"),
                    **campaign_row,
                })
        compact = {
            "ok": True,
            "section": section,
            "source": payload.get("source"),
            "generated_at": payload.get("generated_at"),
            "coverage": payload.get("coverage"),
            "filters": source.get("filters"),
            "calculation_rules": source.get("calculationRules"),
            "scope_presentation": source.get("scopePresentation"),
            "report_detail": source.get("reportDetail"),
            "summary": source.get("calculatedSummary"),
            "monthly_comparison": source.get("monthlyComparison"),
            "financial_history": source.get("financialMonthly", []),
            "performance": {
                "level": performance.get("level") if isinstance(performance, dict) else None,
                **page_rows(rows),
            },
            "campaign_breakdown": page_rows(campaign_breakdown),
            "campaigns": page_rows(campaign_rows),
            "operations_by_type": source.get("operationsByType", []),
            "staff_history": source.get("staffMonthly", []),
            "role_distribution": source.get("roleDistribution", []),
            "sales_history": source.get("salesMonthly", []),
            "kpis": source.get("kpis", []),
            "queue_history": source.get("operationsMonthly", []),
            "supervision": source.get("supervisorAlerts", {}),
            "data_quality": source.get("dataQuality", {}),
        }
        return round_financial_values(compact)

    if section == "ratios":
        rows = payload.get("rows", [])
        metadata = {key: value for key, value in payload.items() if key != "rows"}
    elif section in {"operations", "operational_metrics", "attendance"}:
        rows = payload.get("data", [])
        metadata = {key: value for key, value in payload.items() if key != "data"}
    else:
        return round_financial_values({"ok": True, "section": section, **payload})

    account_filter = normalize_search(account)
    campaign_filter = normalize_search(campaign)
    filtered = [
        row for row in rows if isinstance(row, dict)
        and (not account_filter or account_filter in normalize_search(row.get("account")))
        and (
            not campaign_filter
            or campaign_filter in normalize_search(row.get("campaign"))
            or campaign_filter in normalize_search(row.get("queue_name"))
        )
    ]
    return paginated_detail_response(
        section, filtered, page, limit, metadata=metadata
    )


def strip_personal_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_personal_fields(item)
            for key, item in value.items()
            if str(key).replace("_", "").lower() not in PERSONAL_FIELD_KEYS
        }
    if isinstance(value, list):
        return [strip_personal_fields(item) for item in value]
    return value


def query_detail_payload(
    payload: Any,
    *,
    section: str,
    account: str,
    campaign: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "section": section, "error": "Detalle no disponible."}
    account_filter = normalize_search(account)
    campaign_filter = normalize_search(campaign)
    filtered: list[dict[str, Any]] = []
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        row_account = normalize_search(row.get("account"))
        row_campaign = " ".join(
            part for part in [
                normalize_search(row.get("campaign")),
                normalize_search(row.get("campaign_bi")),
            ] if part
        )
        if account_filter and account_filter not in row_account:
            continue
        if campaign_filter and campaign_filter not in row_campaign:
            continue
        filtered.append(copy.deepcopy(row))
    return paginated_detail_response(
        section,
        filtered,
        page,
        limit,
        metadata={key: value for key, value in payload.items() if key != "data"},
    )


def query_remote_data_page(
    payload: dict[str, Any],
    *,
    section: str,
    account: str,
    campaign: str,
    page: int,
    limit: int,
    total_key: str = "total",
) -> dict[str, Any]:
    """Normalize both paginated PHP responses and unpaged Node responses.

    SIFO's public PHP backend applies ``page`` and ``limit`` while older Node
    deployments return the complete aggregate. VigIA supports both contracts
    without treating a people count as a row count for workforce data.
    """
    cleaned = strip_personal_fields(coerce_external_decimal_strings(copy.deepcopy(payload)))
    rows = [row for row in cleaned.get("data", []) if isinstance(row, dict)]
    remote_page = int(number(cleaned.get("page")))
    remote_limit = int(number(cleaned.get("limit")))

    if remote_page > 0 and remote_limit > 0:
        metadata = {key: value for key, value in cleaned.items() if key != "data"}
        row_total = int(number(cleaned.get(total_key)))
        if total_key != "total":
            metadata["people_total"] = int(number(cleaned.get("total")))
        return round_financial_values({
            **metadata,
            "ok": True,
            "section": section,
            "page": remote_page,
            "limit": remote_limit,
            "total": max(row_total, len(rows)),
            "results": rows,
        })

    if section in {"operations", "operational_metrics", "attendance"}:
        return query_comprehensive_detail(
            {"details": {section: cleaned}},
            section=section,
            account=account,
            campaign=campaign,
            page=page,
            limit=limit,
        )

    if total_key != "total":
        cleaned["people_total"] = int(number(cleaned.get("total")))
        cleaned["total"] = int(number(cleaned.get(total_key))) or len(rows)
    return query_detail_payload(
        cleaned,
        section=section,
        account=account,
        campaign=campaign,
        page=page,
        limit=limit,
    )


def query_sensitive_data_page(
    payload: dict[str, Any],
    *,
    section: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    """Normalize one permission-gated page without removing payroll fields."""
    cleaned = coerce_external_decimal_strings(copy.deepcopy(payload))
    rows = [row for row in cleaned.get("data", []) if isinstance(row, dict)]
    remote_page = int(number(cleaned.get("page"))) or page
    remote_limit = int(number(cleaned.get("limit"))) or limit
    metadata = {key: value for key, value in cleaned.items() if key != "data"}
    return round_financial_values({
        **metadata,
        "ok": True,
        "section": section,
        "page": remote_page,
        "limit": remote_limit,
        "total": max(int(number(cleaned.get("total"))), len(rows)),
        "results": rows,
        "sensitive": True,
    })


def paginated_detail_response(
    section: str,
    rows: list[dict[str, Any]],
    page: int,
    limit: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start = (page - 1) * limit
    selected = rows[start:start + limit]
    return round_financial_values({
        **(metadata or {}),
        "ok": True,
        "section": section,
        "page": page,
        "limit": limit,
        "total": len(rows),
        "summary": summarize_detail_rows(section, rows),
        "results": selected,
    })


def summarize_detail_rows(section: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if section == "campaigns":
        return {
            "active": sum(1 for row in rows if str(row.get("status", "")).upper() == "ACTIVA"),
            "inactive": sum(1 for row in rows if str(row.get("status", "")).upper() == "INACTIVA"),
            "latest_income": sum(number(row.get("latest_income")) for row in rows),
            "latest_agents": sum(number(row.get("latest_agents")) for row in rows),
        }
    if section == "billing":
        keys = ["base_amount", "bonus_amount", "penalty_amount", "subtotal", "igv", "total"]
        return {key: sum(number(row.get(key)) for row in rows) for key in keys}
    if section == "budget":
        budget = sum(number(row.get("budget")) for row in rows)
        actual = sum(number(row.get("actual")) for row in rows)
        return {
            "budget": budget,
            "actual": actual,
            "variance": actual - budget,
            "compliance": (actual / budget * 100.0) if budget else 0.0,
        }
    if section == "history":
        return {"periods": len(rows)}
    return {}


def number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def account_slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "cuenta"


def row_period(value: Any) -> str:
    text = str(value or "")
    match = re.match(r"(\d{4})-(\d{2})", text)
    return f"{match.group(1)}-{match.group(2)}" if match else ""


def transform_seguimiento_dashboard(
    dashboard: dict[str, Any],
    *,
    period: str,
    budget_payload: dict[str, Any] | None = None,
    history_dashboards: list[tuple[str, dict[str, Any]]] | None = None,
    campaign_payload: dict[str, Any] | None = None,
    billing_payload: dict[str, Any] | None = None,
    agents_payload: dict[str, Any] | None = None,
    comprehensive_payloads: dict[str, dict[str, Any]] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Map Seguimiento Financiero's public API into VigIA's fact-check contract."""
    rows = dashboard.get("by_account")
    if not isinstance(rows, list) or not rows:
        raise FinancialDataError("seguimiento_financiero dashboard has no account data")

    budget_by_account: dict[str, float] = {}
    for row in (budget_payload or {}).get("data", []):
        if not isinstance(row, dict) or row_period(row.get("month")) != period:
            continue
        account = str(row.get("account") or "").strip()
        if account:
            budget_value = row.get("budget_pen")
            if budget_value is None:
                budget_value = row.get("budget")
            budget_by_account[account] = budget_by_account.get(account, 0.0) + number(budget_value)

    history_by_period: list[tuple[str, dict[str, dict[str, Any]]]] = []
    for history_period, payload in history_dashboards or []:
        by_name: dict[str, dict[str, Any]] = {}
        for row in payload.get("by_account", []):
            if isinstance(row, dict):
                account = str(row.get("account") or "").strip()
                if account:
                    by_name[account] = row
        history_by_period.append((history_period, by_name))

    margin_target = env_float("VIGIA_GROSS_MARGIN_TARGET", 20.0)
    used_ids: set[str] = set()
    campaigns: list[dict[str, Any]] = []

    def history_for(account: str, extractor: Any) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for history_period, rows_by_name in history_by_period:
            row = rows_by_name.get(account)
            if row is not None:
                history.append({"period": history_period, "actual": round(extractor(row), 2)})
        return history

    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        account = str(raw_row.get("account") or "").strip()
        if not account:
            continue

        campaign_id = account_slug(account)
        suffix = 2
        while campaign_id in used_ids:
            campaign_id = f"{account_slug(account)}-{suffix}"
            suffix += 1
        used_ids.add(campaign_id)

        gross_income = number(raw_row.get("gross_income"))
        net_income = number(raw_row.get("net_income"))
        payroll = number(raw_row.get("payroll"))
        structure_payroll = number(raw_row.get("structure_payroll"))
        billed = number(raw_row.get("billed"))
        penalties = number(raw_row.get("penalties"))
        agents = number(raw_row.get("agents"))
        supervisors = number(raw_row.get("supervisors"))
        campaign_count = number(raw_row.get("campaigns"))
        budget = budget_by_account.get(account, 0.0)
        gross_margin = ((net_income - payroll) / net_income * 100.0) if net_income else 0.0
        billing_target = net_income if net_income > 0 else billed
        income_target = budget if budget > 0 else net_income
        payroll_target = max(0.0, net_income * (1.0 - margin_target / 100.0))

        metrics: dict[str, dict[str, Any]] = {
            "gross_income": {
                "label": "Ingreso bruto",
                "aliases": ["ingreso bruto", "venta bruta"],
                "actual": round(gross_income, 2),
                "target": round(gross_income, 2),
                "direction": "min",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("gross_income"))),
            },
            "gross_margin": {
                "label": "Margen bruto",
                "aliases": ["margen", "rentabilidad", "margen de planilla"],
                "actual": round(gross_margin, 2),
                "target": margin_target,
                "direction": "min",
                "unit": "%",
                "tolerance": 0.5,
                "history": history_for(
                    account,
                    lambda row: (
                        (number(row.get("net_income")) - number(row.get("payroll")))
                        / number(row.get("net_income"))
                        * 100.0
                    ) if number(row.get("net_income")) else 0.0,
                ),
            },
            "facturacion": {
                "label": "Facturación",
                "aliases": ["facturado", "monto facturado", "facturación real"],
                "actual": round(billed, 2),
                "target": round(billing_target, 2),
                "direction": "min",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("billed"))),
            },
            "net_income": {
                "label": "Ingreso neto",
                "aliases": ["ingreso neto", "provisión", "provision", "venta neta"],
                "actual": round(net_income, 2),
                "target": round(income_target, 2),
                "direction": "min",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("net_income"))),
            },
            "payroll": {
                "label": "Planilla directa",
                "aliases": ["planilla", "costo de planilla", "nómina", "nomina"],
                "actual": round(payroll, 2),
                "target": round(payroll_target, 2),
                "direction": "max",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("payroll"))),
            },
            "structure_payroll": {
                "label": "Planilla de estructura",
                "aliases": ["estructura", "costo de estructura", "planilla estructura"],
                "actual": round(structure_payroll, 2),
                "target": round(structure_payroll, 2),
                "direction": "max",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("structure_payroll"))),
            },
            "penalties": {
                "label": "Penalidades",
                "aliases": ["penalidad", "penalidades", "descuentos por penalidad"],
                "actual": round(penalties, 2),
                "target": 0.0,
                "direction": "max",
                "unit": "S/",
                "tolerance": 1.0,
                "history": history_for(account, lambda row: number(row.get("penalties"))),
            },
            "agents": {
                "label": "Agentes efectivos",
                "aliases": ["agentes", "asesores", "dotación", "dotacion"],
                "actual": round(agents, 2),
                "target": round(agents, 2),
                "direction": "min",
                "unit": "personas",
                "tolerance": 0.0,
                "history": history_for(account, lambda row: number(row.get("agents"))),
            },
            "supervisors": {
                "label": "Supervisores activos",
                "aliases": ["supervisores", "supervisión", "supervision"],
                "actual": round(supervisors, 2),
                "target": round(supervisors, 2),
                "direction": "min",
                "unit": "personas",
                "tolerance": 0.0,
                "history": history_for(account, lambda row: number(row.get("supervisors"))),
            },
            "campaign_count": {
                "label": "Campañas con datos",
                "aliases": ["campañas con datos", "campanas activas", "número de campañas"],
                "actual": round(campaign_count, 2),
                "target": round(campaign_count, 2),
                "direction": "min",
                "unit": "campañas",
                "tolerance": 0.0,
                "history": history_for(account, lambda row: number(row.get("campaigns"))),
            },
        }
        if budget > 0:
            metrics["budget_compliance"] = {
                "label": "Cumplimiento de presupuesto",
                "aliases": ["cumplimiento de presupuesto", "presupuesto", "cumplimiento presupuestal"],
                "actual": round(net_income / budget * 100.0, 2),
                "target": 100.0,
                "direction": "min",
                "unit": "%",
                "tolerance": 0.5,
            }

        campaigns.append(
            {
                "id": campaign_id,
                "name": account,
                "aliases": [account],
                "owner": "Seguimiento Financiero A365",
                "metrics": metrics,
            }
        )

    if not campaigns:
        raise FinancialDataError("seguimiento_financiero dashboard has no valid accounts")

    history_details = [
        {
            "period": history_period,
            "summary": payload.get("summary", {}),
            "by_account": payload.get("by_account", []),
        }
        for history_period, payload in history_dashboards or []
    ]
    comprehensive = comprehensive_payloads or {}
    details = {
        "dashboard": dashboard,
        "history": history_details,
        "campaigns": campaign_payload or {"page": 1, "limit": 0, "total": 0, "data": []},
        "billing": billing_payload or {
            "period": {"year": parse_period(period)[0], "month": parse_period(period)[1]},
            "data": [],
        },
        "budget": budget_payload or {"year": parse_period(period)[0], "data": []},
        "agents": {
            **(agents_payload or {
                "period": {"year": parse_period(period)[0], "month": parse_period(period)[1]},
                "total": 0,
            }),
            "access": "on_demand_paginated",
        },
        "kpis": {"access": "on_demand", "requires": "campaign"},
        **comprehensive,
    }
    coverage = {
        "accounts": len(campaigns),
        "historical_periods": len(history_details) + 1,
        "campaigns": int(number(details["campaigns"].get("total"))),
        "catalog_campaigns": int(number(details["campaigns"].get("total"))),
        "period_campaigns": int(number(dashboard.get("summary", {}).get("total_campaigns"))),
        "billing_records": len(details["billing"].get("data", [])),
        "budget_records": len(details["budget"].get("data", [])),
        "agents": int(number(details["agents"].get("total"))),
        "workforce_groups": int(number(details["agents"].get("total_groups"))),
        "agents_mode": "on_demand_paginated_without_personal_data",
        "payroll_detail_mode": "on_demand_paginated_permission_gated",
        "payroll_detail_permission": "agents.read_sensitive",
        "kpis_mode": "on_demand_by_campaign",
        "executive_snapshot": "executive" in comprehensive,
        "ratio_records": len(comprehensive.get("ratios", {}).get("rows", [])),
        "operations_records": int(number(comprehensive.get("operations", {}).get("total"))),
        "operational_metric_records": int(number(comprehensive.get("operational_metrics", {}).get("total"))),
        "attendance_records": int(number(comprehensive.get("attendance", {}).get("total"))),
        "operations_mode": "on_demand_paginated",
        "operational_metrics_mode": "on_demand_paginated",
        "attendance_mode": "on_demand_paginated",
        "ifc_available": "ifc" in comprehensive,
        "ifc_annual_available": "ifc_annual" in comprehensive,
        "data_quality_available": "data_quality" in comprehensive,
        "privacy": "Datos personales fuera del contexto ejecutivo; planilla individual disponible bajo demanda con permiso sensible",
    }

    return round_financial_values({
        "generated_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "source": f"{str(dashboard.get('source') or 'SIFO API').strip()} · período {period}",
        "period": period,
        "campaigns": campaigns,
        "summary": dashboard.get("summary", {}),
        "details": details,
        "coverage": coverage,
    })


def financial_scope_options(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the non-sensitive account and current-period campaign catalog."""
    accounts = sorted({
        str(item.get("name") or "").strip()
        for item in snapshot.get("campaigns", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    })
    account_set = set(accounts)
    period = str(snapshot.get("period") or "")
    details = snapshot.get("details", {}) if isinstance(snapshot.get("details"), dict) else {}
    rows = details.get("campaigns", {}).get("data", []) if isinstance(details.get("campaigns"), dict) else []
    campaigns: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or str(row.get("latest_period") or "") != period:
            continue
        value = str(row.get("campaign_bi") or "").strip()
        account = str(row.get("account") or "").strip()
        if not value or value in seen or account not in account_set:
            continue
        seen.add(value)
        campaigns.append({
            "value": value,
            "label": value,
            "account": account,
            "operation_type": str(row.get("operation_type") or "").strip(),
        })
    campaigns.sort(key=lambda item: (item["account"], item["label"]))
    return {"accounts": accounts, "campaigns": campaigns}


def normalize_financial_scope(
    snapshot: dict[str, Any],
    *,
    accounts: list[str],
    campaigns: list[str],
) -> dict[str, Any]:
    options = financial_scope_options(snapshot)
    account_lookup = {value.casefold(): value for value in options["accounts"]}
    campaign_lookup = {item["value"].casefold(): item for item in options["campaigns"]}

    requested_accounts = unique_scope_values(accounts)
    unknown_accounts = [value for value in requested_accounts if value.casefold() not in account_lookup]
    if unknown_accounts:
        raise FinancialDataError(f"Cuenta fuera del alcance disponible: {unknown_accounts[0]}")
    selected_accounts = [account_lookup[value.casefold()] for value in requested_accounts]
    if len(selected_accounts) == len(options["accounts"]):
        selected_accounts = []

    effective_accounts = set(selected_accounts or options["accounts"])
    compatible_campaigns = {
        key: item for key, item in campaign_lookup.items()
        if item["account"] in effective_accounts
    }
    requested_campaigns = unique_scope_values(campaigns)
    unknown_campaigns = [value for value in requested_campaigns if value.casefold() not in compatible_campaigns]
    if unknown_campaigns:
        raise FinancialDataError(f"Campaña fuera del alcance disponible: {unknown_campaigns[0]}")
    selected_campaigns = [compatible_campaigns[value.casefold()]["value"] for value in requested_campaigns]
    if len(selected_campaigns) == len(compatible_campaigns):
        selected_campaigns = []

    return {
        "accounts": selected_accounts,
        "campaigns": selected_campaigns,
        "all_accounts": not selected_accounts,
        "all_campaigns": not selected_campaigns,
        "label": financial_scope_label(selected_accounts, selected_campaigns),
    }


def unique_scope_values(values: list[str], limit: int = 250) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in values[:limit]:
        value = " ".join(str(raw or "").split())[:240]
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def financial_scope_label(accounts: list[str], campaigns: list[str]) -> str:
    if campaigns:
        return campaigns[0] if len(campaigns) == 1 else f"{len(campaigns)} campañas"
    if accounts:
        return accounts[0] if len(accounts) == 1 else f"{len(accounts)} cuentas"
    return "Todo SIFO"


def with_financial_scope(snapshot: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    result["financial_scope"] = copy.deepcopy(scope)
    return result


def scope_includes_account(scope: dict[str, Any], value: Any) -> bool:
    accounts = scope.get("accounts") or []
    return not accounts or str(value or "").strip() in accounts


def scope_includes_campaign(scope: dict[str, Any], value: Any) -> bool:
    campaigns = scope.get("campaigns") or []
    return not campaigns or str(value or "").strip() in campaigns


def filter_scope_payload(payload: Any, scope: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    result = copy.deepcopy(payload)
    rows = payload.get("data")
    if not isinstance(rows, list):
        return result
    result["data"] = [
        copy.deepcopy(row) for row in rows
        if isinstance(row, dict)
        and scope_includes_account(scope, row.get("account") or row.get("account_name"))
        and scope_includes_campaign(scope, row.get("campaign_bi") or row.get("campaign"))
    ]
    result["total"] = len(result["data"])
    return result


def add_campaign_scope_aliases(
    snapshot: dict[str, Any],
    campaign_payload: dict[str, Any] | None,
    scope: dict[str, Any],
) -> None:
    if not scope.get("campaigns") or not isinstance(campaign_payload, dict):
        return
    by_account: dict[str, list[str]] = {}
    for row in campaign_payload.get("data", []):
        if not isinstance(row, dict):
            continue
        account = str(row.get("account") or "").strip()
        campaign = str(row.get("campaign_bi") or "").strip()
        if account and campaign:
            by_account.setdefault(account, []).append(campaign)
    for item in snapshot.get("campaigns", []):
        aliases = by_account.get(str(item.get("name") or ""), [])
        if len(aliases) == 1:
            item["aliases"] = list(dict.fromkeys([*item.get("aliases", []), aliases[0]]))


def restrict_snapshot_to_scope(snapshot: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    """Safe fallback for local JSON snapshots and account-only scopes."""
    result = copy.deepcopy(snapshot)
    allowed_accounts = set(scope.get("accounts") or [])
    if scope.get("campaigns"):
        catalog = financial_scope_options(snapshot)["campaigns"]
        allowed_campaigns = set(scope["campaigns"])
        campaign_accounts = {item["account"] for item in catalog if item["value"] in allowed_campaigns}
        allowed_accounts = allowed_accounts.intersection(campaign_accounts) if allowed_accounts else campaign_accounts
    if allowed_accounts:
        result["campaigns"] = [
            item for item in result.get("campaigns", [])
            if str(item.get("name") or "") in allowed_accounts
        ]
    if not result.get("campaigns"):
        raise FinancialDataError("El alcance seleccionado no contiene cuentas con datos")

    summary_keys = {
        "total_gross_income": "gross_income",
        "total_net_income": "net_income",
        "total_payroll": "payroll",
        "total_structure_payroll": "structure_payroll",
        "total_penalties": "penalties",
        "total_billed": "facturacion",
        "total_campaigns": "campaign_count",
        "total_agents": "agents",
        "total_supervisors": "supervisors",
    }
    result["summary"] = {
        summary_key: sum(
            number(item.get("metrics", {}).get(metric_key, {}).get("actual"))
            for item in result["campaigns"]
        )
        for summary_key, metric_key in summary_keys.items()
    }
    # Full-detail aggregates cannot be proven to match a local JSON scope.
    # They remain available on demand from SIFO and are not put in the prompt.
    result["details"] = {}
    result["coverage"] = {**result.get("coverage", {}), "accounts": len(result["campaigns"])}
    return with_financial_scope(result, scope)


def snapshot_for_session(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = session_state or {}
    scoped = state.get("_financial_snapshot")
    if isinstance(scoped, dict) and scoped.get("campaigns"):
        return copy.deepcopy(scoped)
    return financial_data_service.get_snapshot()


def validate_financial_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = str(payload.get("generated_at", "")).strip()
    if not generated_at:
        raise FinancialDataError("Financial snapshot requires generated_at")
    parse_iso_datetime(generated_at)

    source = str(payload.get("source", "")).strip()
    if not source:
        raise FinancialDataError("Financial snapshot requires source")

    campaigns = payload.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        raise FinancialDataError("Financial snapshot requires at least one campaign")

    normalized_campaigns: list[dict[str, Any]] = []
    seen_campaign_ids: set[str] = set()
    for campaign_index, raw_campaign in enumerate(campaigns):
        if not isinstance(raw_campaign, dict):
            raise FinancialDataError(f"Campaign {campaign_index} must be an object")
        campaign_id = str(raw_campaign.get("id", "")).strip().lower()
        campaign_name = str(raw_campaign.get("name", "")).strip()
        if not campaign_id or not campaign_name:
            raise FinancialDataError(f"Campaign {campaign_index} requires id and name")
        if campaign_id in seen_campaign_ids:
            raise FinancialDataError(f"Duplicate campaign id: {campaign_id}")
        seen_campaign_ids.add(campaign_id)

        metrics = raw_campaign.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise FinancialDataError(f"Campaign {campaign_name} requires metrics")

        normalized_metrics: dict[str, dict[str, Any]] = {}
        for metric_key, raw_metric in metrics.items():
            if not isinstance(raw_metric, dict):
                raise FinancialDataError(f"Metric {campaign_name}.{metric_key} must be an object")
            label = str(raw_metric.get("label", metric_key)).strip()
            unit = str(raw_metric.get("unit", "")).strip()
            direction = str(raw_metric.get("direction", "")).strip().lower()
            if direction not in {"min", "max"}:
                raise FinancialDataError(
                    f"Metric {campaign_name}.{metric_key} direction must be min or max"
                )
            try:
                actual = float(raw_metric["actual"])
                target = float(raw_metric["target"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FinancialDataError(
                    f"Metric {campaign_name}.{metric_key} requires numeric actual and target"
                ) from exc

            metric = {
                **raw_metric,
                "label": label,
                "actual": actual,
                "target": target,
                "direction": direction,
                "unit": unit,
            }
            metric_aliases = raw_metric.get("aliases") or []
            if not isinstance(metric_aliases, list):
                raise FinancialDataError(
                    f"Metric {campaign_name}.{metric_key} aliases must be a list"
                )
            metric["aliases"] = [
                str(alias).strip() for alias in metric_aliases if str(alias).strip()
            ]
            if raw_metric.get("tolerance") is not None:
                try:
                    metric["tolerance"] = max(0.0, float(raw_metric["tolerance"]))
                except (TypeError, ValueError) as exc:
                    raise FinancialDataError(
                        f"Metric {campaign_name}.{metric_key} tolerance must be numeric"
                    ) from exc
            normalized_metrics[str(metric_key)] = metric

        aliases = raw_campaign.get("aliases") or []
        if not isinstance(aliases, list):
            raise FinancialDataError(f"Campaign {campaign_name} aliases must be a list")
        normalized_campaigns.append(
            {
                **raw_campaign,
                "id": campaign_id,
                "name": campaign_name,
                "owner": str(raw_campaign.get("owner", "")).strip(),
                "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
                "metrics": normalized_metrics,
            }
        )

    return round_financial_values({
        **payload,
        "generated_at": generated_at,
        "source": source,
        "campaigns": normalized_campaigns,
    })


def snapshot_age_seconds(generated_at: str) -> float | None:
    try:
        generated = parse_iso_datetime(generated_at)
    except FinancialDataError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - generated).total_seconds())


def parse_iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinancialDataError("generated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:240]


financial_data_service = FinancialDataService()
