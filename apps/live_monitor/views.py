"""Views and REST API endpoints for Live Monitor dashboard and read-only history."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict
import json
import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.instruments.models import Instrument
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.serializers import (
    LiveMonitorStateSerializer,
    LiveRiskPlanRecordReadOnlySerializer,
    SignalRecordReadOnlySerializer,
)
from apps.live_monitor.services import StateRecoveryService
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord

logger = structlog.get_logger(__name__)


class DashboardView(LoginRequiredMixin, View):
    """
    Server-rendered Live Intelligence Dashboard.
    Requires authenticated session (P7-25).
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        symbol = request.GET.get("symbol", "XAUT/USDT")
        
        # Query canonical live state
        state = LiveMonitorState.objects.filter(instrument=symbol).first()
        if not state:
            state = StateRecoveryService.reconstruct_state(symbol)

        serializer = LiveMonitorStateSerializer(state)

        context = {
            "symbol": symbol,
            "state": state,
            "state_json": json.dumps(serializer.data),
            "user": request.user,
        }
        return render(request, "live_monitor/dashboard.html", context)


class LiveStateAPIView(APIView):
    """
    Canonical Live State REST endpoint (P7-24).
    Used for initial load and reconnect reconciliation.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        symbol = request.query_params.get("symbol", "XAUT/USDT")
        state = LiveMonitorState.objects.filter(instrument=symbol).first()
        if not state:
            state = StateRecoveryService.reconstruct_state(symbol)

        serializer = LiveMonitorStateSerializer(state)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChartDataAPIView(APIView):
    """
    Plotly Market Candlesticks & Structural Overlays REST endpoint.
    Strictly presentation-only: chart does NOT compute signals or risk parameters.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        symbol = request.query_params.get("symbol", "XAUT/USDT")
        timeframe = request.query_params.get("timeframe", "15m")
        limit = int(request.query_params.get("limit", "100"))

        parts = symbol.split("/")
        if len(parts) != 2:
            return Response({"error": "Invalid symbol format"}, status=status.HTTP_400_BAD_REQUEST)

        instrument = Instrument.objects.filter(
            base_asset__code=parts[0], quote_asset__code=parts[1]
        ).first()

        if not instrument:
            return Response({"error": "Instrument not found"}, status=status.HTTP_404_NOT_FOUND)

        candles_qs = (
            MarketCandle.objects.filter(
                instrument=instrument,
                timeframe=timeframe,
                is_closed=True,
            )
            .order_by("-timestamp_close")[:limit]
        )
        candles = list(reversed(list(candles_qs)))

        # Format candlestick data for Plotly
        timestamps = [c.timestamp_close.isoformat() for c in candles]
        opens = [float(c.open) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]

        # Fetch latest canonical state for overlay boundaries
        state = LiveMonitorState.objects.filter(instrument=symbol).first()
        overlays: Dict[str, Any] = {}
        if state and state.risk_plan_valid and state.execution_eligible:
            overlays = {
                "entry_min": float(state.entry_min) if state.entry_min else None,
                "entry_mid": float(state.entry_mid) if state.entry_mid else None,
                "entry_max": float(state.entry_max) if state.entry_max else None,
                "stop_final": float(state.stop_final) if state.stop_final else None,
                "tp1": float(state.tp1) if state.tp1 else None,
                "tp2": float(state.tp2) if state.tp2 else None,
            }

        chart_payload = {
            "instrument": symbol,
            "timeframe": timeframe,
            "timestamps": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "overlays": overlays,
        }
        return Response(chart_payload, status=status.HTTP_200_OK)


class SignalHistoryAPIView(APIView):
    """Read-only API for paginated historical SignalRecords (P7-26, P7-27)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        symbol = request.query_params.get("symbol", "XAUT/USDT")
        page_num = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        qs = (
            SignalRecord.objects.filter(instrument__base_asset__code="XAUT")
            .order_by("-timestamp")
        )
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page_num)

        serializer = SignalRecordReadOnlySerializer(page_obj.object_list, many=True)
        return Response(
            {
                "count": paginator.count,
                "num_pages": paginator.num_pages,
                "current_page": page_num,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class RiskPlanHistoryAPIView(APIView):
    """Read-only API for paginated historical LiveRiskPlanRecords (P7-26, P7-27)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        symbol = request.query_params.get("symbol", "XAUT/USDT")
        page_num = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        qs = LiveRiskPlanRecord.objects.filter(instrument=symbol).order_by("-signal_timestamp")
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page_num)

        serializer = LiveRiskPlanRecordReadOnlySerializer(page_obj.object_list, many=True)
        return Response(
            {
                "count": paginator.count,
                "num_pages": paginator.num_pages,
                "current_page": page_num,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class HistoryPageView(LoginRequiredMixin, View):
    """Server-rendered HTML page for viewing immutable signal and risk plan audit history."""

    def get(self, request: HttpRequest) -> HttpResponse:
        symbol = request.GET.get("symbol", "XAUT/USDT")
        signals = (
            SignalRecord.objects.filter(instrument__base_asset__code="XAUT")
            .order_by("-timestamp")[:50]
        )
        risk_plans = (
            LiveRiskPlanRecord.objects.filter(instrument=symbol)
            .order_by("-signal_timestamp")[:50]
        )

        context = {
            "symbol": symbol,
            "signals": signals,
            "risk_plans": risk_plans,
        }
        return render(request, "live_monitor/history.html", context)


class LivenessHealthView(View):
    """
    Liveness probe: verifies process is running (P7-42).
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        now_utc = datetime.now(timezone.utc)
        return JsonResponse(
            {
                "status": "ok",
                "service": "aurumiq-live-monitor",
                "timestamp": now_utc.isoformat(),
            },
            status=200,
        )


class ReadinessHealthView(View):
    """
    Readiness probe: verifies DB connectivity and core infrastructure (P7-43).
    Strictly does not recompute signals or business logic.
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        from django.db import connection
        now_utc = datetime.now(timezone.utc)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            
            instrument_ready = Instrument.objects.filter(base_asset__code="XAUT").exists()

            return JsonResponse(
                {
                    "status": "ready" if instrument_ready else "degraded",
                    "database": "connected",
                    "instrument_configured": instrument_ready,
                    "service": "aurumiq-live-monitor",
                    "timestamp": now_utc.isoformat(),
                },
                status=200 if instrument_ready else 503,
            )
        except Exception as e:
            logger.error("readiness_check_failed", error=str(e))
            return JsonResponse(
                {
                    "status": "unavailable",
                    "database": "disconnected",
                    "error": str(e),
                    "timestamp": now_utc.isoformat(),
                },
                status=503,
            )

