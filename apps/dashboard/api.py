"""Read-only and research REST API endpoints for the AurumIQ Dashboard (Phase 7)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List
import structlog
from django.core.paginator import Paginator
from django.http import HttpRequest
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.instruments.models import Instrument
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.live_monitor.serializers import (
    LiveRiskPlanRecordReadOnlySerializer,
    SignalRecordReadOnlySerializer,
)
from apps.live_monitor.services import XauUsdLiveProjectionService
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord

logger = structlog.get_logger(__name__)


class LiveProjectionAPIView(APIView):
    """
    Canonical live projection REST endpoint for XAUUSD (Amendment 9).
    Exposes identical semantic projection as Django views and WebSocket updates.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        if not state:
            state = XauUsdLiveProjectionService.reconstruct_xauusd_state()

        projection_dict = XauUsdLiveProjectionService.assemble_projection_dict(state)
        return Response(projection_dict, status=status.HTTP_200_OK)


class SignalHistoryAPIView(APIView):
    """Read-only paginated API for immutable XAUUSD SignalRecords."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        page_num = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        inst = Instrument.get_canonical_xauusd()
        qs = SignalRecord.objects.filter(instrument=inst).order_by("-timestamp") if inst else SignalRecord.objects.none()

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


class RiskProjectionAPIView(APIView):
    """Read-only API for current Phase 5 XAUUSD risk plan projection."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        if not state:
            state = XauUsdLiveProjectionService.reconstruct_xauusd_state()

        data = {
            "instrument": "XAUUSD",
            "risk_side": state.risk_side,
            "risk_candidate_status": state.risk_candidate_status,
            "is_valid_risk_plan": state.risk_plan_valid,
            "execution_eligible": state.execution_eligible,
            "candidate_effective_action": state.candidate_effective_action or state.effective_action,
            "publication_effective_action": state.publication_effective_action or "WAIT",
            "entry_min": str(state.entry_min) if state.entry_min else None,
            "entry_mid": str(state.entry_mid) if state.entry_mid else None,
            "entry_max": str(state.entry_max) if state.entry_max else None,
            "stop_structure": str(state.stop_structure) if state.stop_structure else None,
            "stop_atr": str(state.stop_atr) if state.stop_atr else None,
            "stop_final": str(state.stop_final) if state.stop_final else None,
            "stop_distance_atr": str(state.stop_distance_atr) if state.stop_distance_atr else None,
            "tp1": str(state.tp1) if state.tp1 else None,
            "tp2": str(state.tp2) if state.tp2 else None,
            "planned_rr_tp1": str(state.rr_tp1) if state.rr_tp1 else None,
            "planned_rr_tp2": str(state.rr_tp2) if state.rr_tp2 else None,
            "risk_plan_fingerprint": state.risk_plan_fingerprint,
            "source_phase4_fingerprint": state.source_phase4_fingerprint,
        }
        return Response(data, status=status.HTTP_200_OK)


class SystemHealthAPIView(APIView):
    """Read-only API for system and feed health diagnostics."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        from apps.live_monitor.consumers import LiveEventBroadcaster
        r = LiveEventBroadcaster.get_redis_client()
        redis_status = "ONLINE" if r is not None else "OFFLINE"

        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        feed_health = state.feed_health_data if state else {}

        now_utc = datetime.now(timezone.utc)
        return Response(
            {
                "service": "aurumiq-dashboard",
                "timestamp": now_utc.isoformat(),
                "redis_status": redis_status,
                "feed_health": feed_health,
                "calibration_status": state.calibration_status if state else "CALIBRATION_REQUIRED",
            },
            status=status.HTTP_200_OK,
        )


class ChartDataAPIView(APIView):
    """
    REST endpoint returning internal candlestick data & structural overlays for Plotly.js.
    Strict Invariant: Internal data only. Zero external data scraping.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest, timeframe: str = "15m") -> Response:
        if timeframe not in ("15m", "1h", "4h", "1d"):
            timeframe = "15m"

        limit = int(request.query_params.get("limit", 100))
        limit = min(limit, 500)

        inst = Instrument.get_canonical_xauusd()
        if not inst:
            return Response({"error": "XAUUSD instrument not configured"}, status=status.HTTP_404_NOT_FOUND)

        candles_qs = (
            MarketCandle.objects.filter(
                instrument=inst,
                timeframe=timeframe,
                is_closed=True,
            )
            .order_by("-timestamp_close")[:limit]
        )
        candles = list(reversed(list(candles_qs)))

        timestamps = [c.timestamp_close.isoformat() for c in candles]
        opens = [float(c.open) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]

        # Structural overlays from latest state
        state = LiveMonitorState.objects.filter(instrument="XAUUSD").first()
        overlays: Dict[str, Any] = {}
        if state and state.risk_plan_valid:
            overlays = {
                "entry_min": float(state.entry_min) if state.entry_min else None,
                "entry_max": float(state.entry_max) if state.entry_max else None,
                "stop_final": float(state.stop_final) if state.stop_final else None,
                "tp1": float(state.tp1) if state.tp1 else None,
                "tp2": float(state.tp2) if state.tp2 else None,
                "side": state.risk_side,
            }

        chart_payload = {
            "instrument": "XAUUSD",
            "display_symbol": "XAU/USD",
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


class BacktestStatusAPIView(APIView):
    """Read-only API for querying Phase 6 BacktestRun status and metrics."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: HttpRequest) -> Response:
        run_id = request.query_params.get("run_id")
        from apps.backtests.models import BacktestRun
        if run_id:
            run = BacktestRun.objects.filter(id=run_id).first()
            if not run:
                return Response({"error": "Backtest run not found"}, status=status.HTTP_404_NOT_FOUND)
            return Response(
                {
                    "run_id": run.id,
                    "run_fingerprint": run.run_fingerprint,
                    "status": run.status,
                    "ablation_id": run.ablation_id,
                    "aggregate_metrics": run.aggregate_metrics,
                    "temporal_stability": run.temporal_stability,
                    "error_message": run.error_message,
                    "created_at": run.created_at.isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        runs = BacktestRun.objects.all().order_by("-created_at")[:20]
        results = [
            {
                "run_id": r.id,
                "run_fingerprint": r.run_fingerprint,
                "status": r.status,
                "ablation_id": r.ablation_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ]
        return Response({"results": results}, status=status.HTTP_200_OK)


class BacktestRunLaunchAPIView(APIView):
    """
    Research-only API for enqueuing Phase 6 backtest runs asynchronously (Amendment 7).
    Strict Invariants:
      - Only enqueues existing Celery run_xauusd_backtest_task.
      - Strictly forbidden from placing, modifying, or executing orders.
      - Rejects naive timestamps. Require end_date > start_date.
      - Requires explicit cost_scenario in (IDEALIZED, EMPIRICAL).
      - Requires all 5 friction fields for EMPIRICAL (no hidden defaults).
      - Requires valid EntryExecutionPolicy and IntrabarPolicy enums.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: HttpRequest) -> Response:
        from engine.core.types import EntryExecutionPolicy, IntrabarPolicy
        data = request.data

        start_date_str = data.get("start_date") or data.get("start_time_iso")
        end_date_str = data.get("end_date") or data.get("end_time_iso")

        if not start_date_str or not end_date_str:
            return Response(
                {"error": "start_date and end_date ISO strings are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            start_dt = datetime.fromisoformat(start_date_str)
            end_dt = datetime.fromisoformat(end_date_str)
            if start_dt.tzinfo is None or start_dt.tzinfo.utcoffset(start_dt) is None:
                return Response(
                    {"error": "start_date must include an explicit timezone offset (naive timestamps forbidden)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if end_dt.tzinfo is None or end_dt.tzinfo.utcoffset(end_dt) is None:
                return Response(
                    {"error": "end_date must include an explicit timezone offset (naive timestamps forbidden)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if end_dt <= start_dt:
                return Response(
                    {"error": "end_date must be strictly greater than start_date."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as e:
            return Response(
                {"error": f"Invalid date format: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cost_scenario = data.get("cost_scenario")
        if cost_scenario not in ("IDEALIZED", "EMPIRICAL"):
            return Response(
                {"error": "cost_scenario is required and must be either 'IDEALIZED' or 'EMPIRICAL'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate explicit friction for EMPIRICAL scenario (all 5 required)
        if cost_scenario == "EMPIRICAL":
            entry_fee = data.get("entry_fee_bps")
            exit_fee = data.get("exit_fee_bps")
            spread_bps = data.get("synthetic_spread_bps")
            entry_slip = data.get("entry_slippage_bps")
            exit_slip = data.get("exit_slippage_bps")
            if (
                entry_fee is None
                or exit_fee is None
                or spread_bps is None
                or entry_slip is None
                or exit_slip is None
            ):
                return Response(
                    {
                        "error": "cost_scenario EMPIRICAL strictly requires all 5 friction parameters: "
                        "entry_fee_bps, exit_fee_bps, synthetic_spread_bps, entry_slippage_bps, exit_slippage_bps."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        code_revision = data.get("code_revision")
        if not code_revision or not str(code_revision).strip():
            return Response(
                {"error": "code_revision is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dataset_hash = data.get("dataset_hash")
        if not dataset_hash or not str(dataset_hash).strip():
            return Response(
                {"error": "dataset_hash is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            holding_horizon_bars = int(data.get("holding_horizon_bars_15m", 0))
            if holding_horizon_bars <= 0:
                raise ValueError()
        except Exception:
            return Response(
                {"error": "holding_horizon_bars_15m is required and must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            max_fill_wait_bars = int(data.get("max_fill_wait_bars_15m", 0))
            if max_fill_wait_bars <= 0:
                raise ValueError()
        except Exception:
            return Response(
                {"error": "max_fill_wait_bars_15m is required and must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        execution_policy = data.get("execution_policy")
        valid_exec_policies = [e.value for e in EntryExecutionPolicy]
        if execution_policy not in valid_exec_policies:
            return Response(
                {"error": f"Invalid execution_policy '{execution_policy}'. Allowed: {valid_exec_policies}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        intrabar_policy = data.get("intrabar_policy")
        valid_intrabar_policies = [e.value for e in IntrabarPolicy]
        if intrabar_policy not in valid_intrabar_policies:
            return Response(
                {"error": f"Invalid intrabar_policy '{intrabar_policy}'. Allowed: {valid_intrabar_policies}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from engine.backtest.xauusd_types import XauUsdAblationType
        ablation_raw = data.get("ablation_id") or data.get("ablation_type", "BASELINE")
        valid_ablations = [a.value for a in XauUsdAblationType]
        if ablation_raw not in valid_ablations:
            return Response(
                {"error": f"Invalid ablation_type '{ablation_raw}'. Allowed: {valid_ablations}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ablation_id = ablation_raw

        # Enqueue Phase 6 task
        from apps.backtests.tasks import run_xauusd_backtest_task

        task_res = run_xauusd_backtest_task.delay(
            start_time_iso=start_dt.isoformat(),
            end_time_iso=end_dt.isoformat(),
            dataset_hash=dataset_hash,
            code_revision=code_revision,
            cost_scenario=cost_scenario,
            holding_horizon_bars_15m=holding_horizon_bars,
            max_fill_wait_bars_15m=max_fill_wait_bars,
            execution_policy=execution_policy,
            intrabar_policy=intrabar_policy,
            ablation_type=ablation_id,
            entry_fee_bps=str(data.get("entry_fee_bps")) if data.get("entry_fee_bps") is not None else None,
            exit_fee_bps=str(data.get("exit_fee_bps")) if data.get("exit_fee_bps") is not None else None,
            synthetic_spread_bps=str(data.get("synthetic_spread_bps")) if data.get("synthetic_spread_bps") is not None else None,
            entry_slippage_bps=str(data.get("entry_slippage_bps")) if data.get("entry_slippage_bps") is not None else None,
            exit_slippage_bps=str(data.get("exit_slippage_bps")) if data.get("exit_slippage_bps") is not None else None,
            signal_profile_dict=data.get("signal_profile_dict"),
            risk_profile_dict=data.get("risk_profile_dict"),
        )

        return Response(
            {
                "status": "ENQUEUED",
                "task_id": str(task_res.id),
                "ablation_id": ablation_id,
                "cost_scenario": cost_scenario,
                "instrument": "XAUUSD",
                "message": "Phase 6 research simulation enqueued.",
            },
            status=status.HTTP_202_ACCEPTED,
        )
