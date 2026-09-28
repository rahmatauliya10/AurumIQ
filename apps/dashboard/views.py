"""Server-rendered Django views for the 8 Dashboard navigation pages (Phase 7)."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict
import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views import View

from apps.alerts.models import AlertEvent
from apps.backtests.models import BacktestRun
from apps.instruments.models import Instrument, ProviderHealthSnapshot
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.services import XauUsdLiveProjectionService
from apps.market_data.models import DataQualitySnapshot, MarketCandle
from apps.signals.models import SignalRecord

logger = structlog.get_logger(__name__)


class OverviewView(LoginRequiredMixin, View):
    """
    1. OVERVIEW PAGE
    Displays live XAU/USD state, dual-layer decision projection, risk geometry, and health.
    Strict Invariant: Published WAIT is visually unmistakable. Candidate BUY/SELL is NOT an order.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        if not state:
            state = XauUsdLiveProjectionService.reconstruct_xauusd_state()

        projection_state = XauUsdLiveProjectionService.assemble_projection(state)
        projection_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)

        context = {
            "page_title": "Overview",
            "active_tab": "overview",
            "projection": projection_state,
            "projection_json": json.dumps(projection_dict),
            "user": request.user,
        }
        return render(request, "dashboard/overview.html", context)


class LiveAnalysisView(LoginRequiredMixin, View):
    """
    2. LIVE ANALYSIS PAGE
    Multi-timeframe candlestick chart (15m, 1H, 4H, 1D) with swing, BOS, and S/R zone overlays.
    Strict Invariant: Rendered via Plotly.js using pure internal persisted XAUUSD data.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        timeframe = request.GET.get("tf", "15m")
        if timeframe not in ("15m", "1h", "4h", "1d"):
            timeframe = "15m"

        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        if not state:
            state = XauUsdLiveProjectionService.reconstruct_xauusd_state()

        projection_state = XauUsdLiveProjectionService.assemble_projection(state)

        context = {
            "page_title": "Live Analysis",
            "active_tab": "live_analysis",
            "selected_timeframe": timeframe,
            "projection": projection_state,
            "user": request.user,
        }
        return render(request, "dashboard/analysis.html", context)


def _parse_int_param(request: HttpRequest, key: str, default: int, allowed: Any = None) -> int:
    try:
        val = int(request.GET.get(key, default))
        if allowed and val not in allowed:
            return default
        return val if val > 0 else default
    except (ValueError, TypeError):
        return default


class TimeCycleLabView(LoginRequiredMixin, View):
    """
    3. TIME CYCLE LAB PAGE
    Presents Phase 3A cycle parameters and Phase 3B research state (weight 0.0).
    Server-side pagination for Phase 3A cycle snapshots (default 50 rows/page).
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        from apps.analysis.models import CycleSnapshotRecord, ExperimentalCycleSnapshotRecord
        inst = Instrument.get_canonical_xauusd()
        page_num = _parse_int_param(request, "page", 1)
        page_size = _parse_int_param(request, "page_size", 50, allowed=[25, 50, 100])

        qs_3a = (
            CycleSnapshotRecord.objects.filter(instrument=inst).order_by("-timestamp", "-id")
            if inst
            else CycleSnapshotRecord.objects.none()
        )
        paginator_3a = Paginator(qs_3a, page_size)
        page_obj_3a = paginator_3a.get_page(page_num)

        latest_cycles_3b = []
        if inst:
            latest_cycles_3b = ExperimentalCycleSnapshotRecord.objects.filter(instrument=inst).order_by("-timestamp", "-id")[:20]

        context = {
            "page_title": "Time Cycle Lab",
            "active_tab": "time_cycle",
            "page_obj_3a": page_obj_3a,
            "latest_cycles_3a": page_obj_3a.object_list,
            "page_size": page_size,
            "latest_cycles_3b": latest_cycles_3b,
            "phase3b_status": "RESEARCH ONLY — PRODUCTION WEIGHT 0.0",
            "user": request.user,
        }
        return render(request, "dashboard/cycles.html", context)


class SignalsHistoryView(LoginRequiredMixin, View):
    """
    4. SIGNALS HISTORY PAGE
    Paginated, filterable immutable SignalRecords with dual-side scores and Layer A/B decisions.
    Server-side pagination default 50 rows/page.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        page_num = _parse_int_param(request, "page", 1)
        page_size = _parse_int_param(request, "page_size", 50, allowed=[25, 50, 100])

        inst = Instrument.get_canonical_xauusd()
        qs = (
            SignalRecord.objects.filter(instrument=inst).order_by("-timestamp", "-id")
            if inst
            else SignalRecord.objects.none()
        )

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page_num)

        context = {
            "page_title": "Signals History",
            "active_tab": "signals_history",
            "page_obj": page_obj,
            "signals": page_obj.object_list,
            "page_size": page_size,
            "user": request.user,
        }
        return render(request, "dashboard/signals.html", context)


class BacktestLabView(LoginRequiredMixin, View):
    """
    5. BACKTEST LAB PAGE
    Phase 6 backtest governance surface. Launch asynchronous jobs and review normalized R outcomes.
    Strict Invariant: Zero order execution or broker connectivity.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        page_num = int(request.GET.get("page", 1))
        runs = BacktestRun.objects.all().order_by("-created_at")
        paginator = Paginator(runs, 20)
        page_obj = paginator.get_page(page_num)

        from engine.backtest.xauusd_governance import resolve_backtest_governance_spec
        gov_spec = resolve_backtest_governance_spec()
        launch_enabled = gov_spec.is_approved_configured and gov_spec.approved_window is not None
        if launch_enabled and gov_spec.approved_window:
            approved_window_display = (
                f"{gov_spec.approved_window.start.strftime('%Y-%m-%d %H:%M')} → "
                f"{gov_spec.approved_window.end.strftime('%Y-%m-%d %H:%M')} UTC"
            )
            default_start_str = "2023-01-01T00:00"
            default_end_str = "2024-01-01T00:00"
            min_date_str = gov_spec.approved_window.start.strftime("%Y-%m-%dT%H:%M")
            max_date_str = gov_spec.approved_window.end.strftime("%Y-%m-%dT%H:%M")
        else:
            approved_window_display = "NO_APPROVED_RESEARCH_WINDOW_CONFIGURED"
            default_start_str = ""
            default_end_str = ""
            min_date_str = ""
            max_date_str = ""

        context = {
            "page_title": "Backtest Lab",
            "active_tab": "backtest_lab",
            "page_obj": page_obj,
            "runs": page_obj.object_list,
            "launch_enabled": launch_enabled,
            "approved_window_display": approved_window_display,
            "default_start_str": default_start_str,
            "default_end_str": default_end_str,
            "min_date_str": min_date_str,
            "max_date_str": max_date_str,
            "user": request.user,
        }
        return render(request, "dashboard/backtest.html", context)


class DataIntegrityView(LoginRequiredMixin, View):
    """
    6. DATA INTEGRITY PAGE
    Provider health, freshness, last successful closed candle, and data quality check records.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        inst = Instrument.get_canonical_xauusd()
        snapshots = []
        dq_records = []
        if inst:
            snapshots = ProviderHealthSnapshot.objects.filter(listing__instrument=inst).order_by("-checked_at")[:20]
            dq_records = DataQualitySnapshot.objects.filter(instrument=inst).order_by("-timestamp")[:20]

        context = {
            "page_title": "Data Integrity",
            "active_tab": "data_integrity",
            "snapshots": snapshots,
            "dq_records": dq_records,
            "user": request.user,
        }
        return render(request, "dashboard/data_integrity.html", context)


class SystemHealthView(LoginRequiredMixin, View):
    """
    7. SYSTEM HEALTH PAGE
    Redis cache status, Celery worker status, and provider transitions.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        from apps.live_monitor.consumers import LiveEventBroadcaster
        r = LiveEventBroadcaster.get_redis_client()
        redis_status = "ONLINE" if r is not None else "OFFLINE"

        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        feed_health = state.feed_health_data if state else {}

        context = {
            "page_title": "System Health",
            "active_tab": "system_health",
            "redis_status": redis_status,
            "feed_health": feed_health,
            "user": request.user,
        }
        return render(request, "dashboard/system_health.html", context)


class AuditLogView(LoginRequiredMixin, View):
    """
    8. AUDIT LOG PAGE
    Immutable audit records: SignalRecords, LiveRiskPlanRecords, and AlertEvents.
    Server-side pagination:
      - AlertEvents: default 50 rows/page
      - Risk Plan History: default 25 rows/page
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        alerts_page_num = _parse_int_param(request, "alerts_page", 1)
        alerts_page_size = _parse_int_param(request, "alerts_page_size", 50, allowed=[25, 50, 100])

        risk_page_num = _parse_int_param(request, "risk_page", 1)
        risk_page_size = _parse_int_param(request, "risk_page_size", 25, allowed=[25, 50, 100])

        alerts_qs = AlertEvent.objects.all().order_by("-created_at", "-id")
        alerts_paginator = Paginator(alerts_qs, alerts_page_size)
        page_obj_alerts = alerts_paginator.get_page(alerts_page_num)

        active_risk_plan = (
            LiveRiskPlanRecord.objects.filter(
                instrument="XAUUSD",
                is_valid_risk_plan=True,
                execution_eligible=True,
            )
            .order_by("-signal_timestamp", "-id")
            .first()
        )
        risk_qs = LiveRiskPlanRecord.objects.filter(instrument="XAUUSD").order_by("-signal_timestamp", "-id")
        risk_paginator = Paginator(risk_qs, risk_page_size)
        page_obj_risk = risk_paginator.get_page(risk_page_num)

        context = {
            "page_title": "Audit Log",
            "active_tab": "audit_log",
            "alerts": page_obj_alerts.object_list,
            "page_obj_alerts": page_obj_alerts,
            "alerts_page_size": alerts_page_size,
            "active_risk_plan": active_risk_plan,
            "risk_plans_history": page_obj_risk.object_list,
            "risk_plans": page_obj_risk.object_list,
            "page_obj_risk": page_obj_risk,
            "risk_page_size": risk_page_size,
            "user": request.user,
        }
        return render(request, "dashboard/audit.html", context)
