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


class TimeCycleLabView(LoginRequiredMixin, View):
    """
    3. TIME CYCLE LAB PAGE
    Presents Phase 3A cycle parameters and Phase 3B research state (weight 0.0).
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        from apps.analysis.models import CycleSnapshotRecord
        inst = Instrument.get_canonical_xauusd()
        latest_cycles = []
        if inst:
            latest_cycles = CycleSnapshotRecord.objects.filter(instrument=inst).order_by("-timestamp")[:20]

        context = {
            "page_title": "Time Cycle Lab",
            "active_tab": "time_cycle",
            "latest_cycles": latest_cycles,
            "phase3b_status": "RESEARCH ONLY — PRODUCTION WEIGHT 0.0",
            "user": request.user,
        }
        return render(request, "dashboard/cycles.html", context)


class SignalsHistoryView(LoginRequiredMixin, View):
    """
    4. SIGNALS HISTORY PAGE
    Paginated, filterable immutable SignalRecords with dual-side scores and Layer A/B decisions.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        page_num = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 25))

        inst = Instrument.get_canonical_xauusd()
        qs = SignalRecord.objects.filter(instrument=inst).order_by("-timestamp") if inst else SignalRecord.objects.none()

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page_num)

        context = {
            "page_title": "Signals History",
            "active_tab": "signals_history",
            "page_obj": page_obj,
            "signals": page_obj.object_list,
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

        context = {
            "page_title": "Backtest Lab",
            "active_tab": "backtest_lab",
            "page_obj": page_obj,
            "runs": page_obj.object_list,
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
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        alerts = AlertEvent.objects.all().order_by("-created_at")[:50]
        risk_plans = LiveRiskPlanRecord.objects.filter(instrument="XAUUSD").order_by("-signal_timestamp")[:50]

        context = {
            "page_title": "Audit Log",
            "active_tab": "audit_log",
            "alerts": alerts,
            "risk_plans": risk_plans,
            "user": request.user,
        }
        return render(request, "dashboard/audit.html", context)
