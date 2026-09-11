"""Celery asynchronous tasks for historical backtesting and validation."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from celery import shared_task
from django.conf import settings

from apps.backtests.models import BacktestRun
from apps.backtests.services import persist_backtest_run, persist_xauusd_backtest_run
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    AblationType,
    BacktestCostConfig,
    BacktestRunSpec,
    CostScenario,
)
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdAblationType,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
)
from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
)
from engine.risk.xauusd_policy import XauUsdRiskProfile
from engine.signals.profile import Phase4SignalProfile


@shared_task(queue="backtest", bind=True, max_retries=1)
def run_backtest_task(
    self,
    start_time_iso: str,
    end_time_iso: str,
    dataset_hash: str,
    code_revision: str,
    instrument: str = "XAUTUSDT",
    cost_scenario: str = "ZERO_FRICTION",
    entry_fee_bps: Optional[str] = None,
    exit_fee_bps: Optional[str] = None,
    slippage_bps: Optional[str] = None,
    spread_usd: Optional[str] = None,
    ablation_type: str = "BASELINE",
    engine_version: str = "2.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "1.0.0",
    execution_model_version: str = "1.0.0",
    backtest_version: str = "1.0.0",
) -> dict:
    """
    Asynchronous Celery task for running historical point-in-time backtest for XAUT.
    """
    if not code_revision or not code_revision.strip():
        raise ValueError("Explicit code_revision is strictly required for backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if start_dt.tzinfo is None or start_dt.tzinfo.utcoffset(start_dt) is None:
        raise ValueError("start_time_iso must include an explicit timezone offset (naive timestamps forbidden).")
    if end_dt.tzinfo is None or end_dt.tzinfo.utcoffset(end_dt) is None:
        raise ValueError("end_time_iso must include an explicit timezone offset (naive timestamps forbidden).")

    if cost_scenario == "REALISTIC":
        cost_cfg = BacktestCostConfig.realistic(
            entry_fee_bps=Decimal(entry_fee_bps or "4.0"),
            exit_fee_bps=Decimal(exit_fee_bps or "4.0"),
            synthetic_spread_bps=Decimal(spread_usd or "5.0"),
            entry_slippage_bps=Decimal(slippage_bps or "2.0"),
            exit_slippage_bps=Decimal(slippage_bps or "2.0"),
        )
    elif cost_scenario == "IDEALIZED" or cost_scenario == "ZERO_FRICTION":
        cost_cfg = BacktestCostConfig.idealized()
        cost_scenario = "IDEALIZED"
    else:
        raise ValueError(f"Unknown cost_scenario: {cost_scenario}")

    spec = BacktestRunSpec(
        instrument=instrument,
        start_time=start_dt,
        end_time=end_dt,
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=cost_cfg,
        cost_scenario=CostScenario(cost_scenario),
        dataset_hash=dataset_hash,
        engine_version=engine_version,
        config_version=config_version,
        feature_version=feature_version,
        cycle_version=cycle_version,
        risk_version=risk_version,
        execution_model_version=execution_model_version,
        backtest_version=backtest_version,
        code_revision=code_revision.strip(),
        ablation_type=AblationType(ablation_type),
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle

        for tf in ("15m", "1h", "4h", "1d"):
            candles_qs = MarketCandle.objects.filter(
                timeframe=tf,
                timestamp_close__gte=start_dt,
                timestamp_close__lte=end_dt,
                is_closed=True,
            ).order_by("timestamp_close")

            for c in candles_qs:
                dataset.add_candle(
                    tf,
                    CandleData(
                        timestamp_open=c.timestamp_open,
                        timestamp_close=c.timestamp_close,
                        open=c.open,
                        high=c.high,
                        low=c.low,
                        close=c.close,
                        volume=c.volume,
                        is_closed=c.is_closed,
                        source_id=c.source,
                    ),
                )

        runner = BacktestRunner()
        result = runner.run(dataset=dataset, spec=spec)
        run_obj, created = persist_backtest_run(run_result=result, dataset_identity=dataset_hash)

        return {
            "status": "COMPLETED",
            "run_fingerprint": run_obj.run_fingerprint,
            "created": created,
            "net_expectancy_r": result.metrics.net_expectancy_r,
            "trade_count": result.metrics.trade_count,
        }
    except Exception as exc:
        BacktestRun.objects.update_or_create(
            run_fingerprint=f"failed-{dataset_hash[:16]}-{code_revision[:8]}",
            defaults={
                "instrument": "XAUTUSDT",
                "dataset_identity": dataset_hash,
                "historical_start": start_dt,
                "historical_end": end_dt,
                "code_revision": code_revision,
                "status": "FAILED",
                "error_message": str(exc),
            },
        )
        raise exc


def compute_calibration_artifact_fingerprint(data: Dict[str, Any]) -> str:
    """
    Compute deterministic SHA-256 fingerprint over artifact content excluding fingerprint fields.
    Uses sorted keys and compact separators to guarantee key-order invariance.
    """
    canonical_payload = {
        k: v for k, v in data.items()
        if k not in ("artifact_fingerprint", "fingerprint")
    }
    serialized_bytes = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized_bytes).hexdigest()


def resolve_xauusd_research_profiles(
    signal_profile_id: Optional[str] = None,
    risk_profile_id: Optional[str] = None,
    calibration_artifact_id: Optional[str] = None,
    signal_profile_dict: Optional[Dict[str, Any]] = None,
    risk_profile_dict: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Phase4SignalProfile], Optional[XauUsdRiskProfile]]:
    """
    Resolve immutable research profiles server-side from identifier or validated JSON-safe dictionary.
    Enforces strict fail-closed validation, path confinement, schema, status allowlist, and fingerprint checks.
    """
    from engine.signals.profile import (
        Phase4CalibrationStatus,
        Phase4FeedPolicy,
        Phase4SignalProfile,
        SideDirectionPolicy,
        SideGatePolicy,
        SideTimingPolicy,
    )
    from engine.risk.xauusd_policy import (
        SideRiskPolicy,
        XauUsdExecutionPolicy,
        XauUsdRiskProfile,
    )
    from engine.core.types import Phase5CalibrationStatus

    sig_prof: Optional[Phase4SignalProfile] = None
    risk_prof: Optional[XauUsdRiskProfile] = None

    # Server-side ID resolution requires a real persisted empirical calibration artifact.
    # When no empirical artifact exists, unknown / default IDs return (None, None), failing closed.
    if calibration_artifact_id is not None:
        if not isinstance(calibration_artifact_id, str):
            return None, None

        raw_id = calibration_artifact_id.strip()
        if not raw_id:
            return None, None

        # 1. Path Security: Strictly reject absolute paths, traversal operators, and directory separators
        if (
            os.path.isabs(raw_id)
            or ".." in raw_id
            or "/" in raw_id
            or "\\" in raw_id
            or ":" in raw_id
            or "\x00" in raw_id
        ):
            return None, None

        clean_name = raw_id if raw_id.endswith(".json") else f"{raw_id}.json"

        # 2. Canonical allowlisted calibration artifact directory
        base_dir = getattr(settings, "BASE_DIR", Path("."))
        canonical_dir = (Path(base_dir) / "artifacts" / "calibration").resolve()
        target_path = (canonical_dir / clean_name).resolve()

        # Confinement check: target_path must be directly within canonical_dir
        if target_path.parent != canonical_dir or not target_path.is_file():
            return None, None

        # 3. Parse JSON fail-closed
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                artifact_data = json.load(f)
        except Exception:
            return None, None

        if not isinstance(artifact_data, dict):
            return None, None

        # 4. Schema Validation
        schema = artifact_data.get("schema")
        if not schema or not isinstance(schema, str) or not schema.startswith("aurumiq."):
            return None, None

        # 5. Target Instrument Validation
        instrument = artifact_data.get("instrument") or artifact_data.get("target_instrument")
        if not instrument or not isinstance(instrument, str) or instrument.strip().upper() not in ("XAUUSD", "XAU/USD"):
            return None, None

        # 6. Profile Status Validation & Allowlist
        sig_dict = artifact_data.get("signal_profile")
        if not isinstance(sig_dict, dict):
            return None, None

        raw_sig_status = sig_dict.get("calibration_status") or artifact_data.get("calibration_status")
        if not raw_sig_status or not isinstance(raw_sig_status, str):
            return None, None

        try:
            sig_status_enum = Phase4CalibrationStatus(raw_sig_status.strip())
        except (ValueError, TypeError):
            return None, None

        # Explicit allowlist for active Phase 8 calibrated signals:
        # PENDING_DATA, UNCALIBRATED, PENDING_PHASE6, LEGACY_REFERENCE must NEVER resolve to an active profile.
        allowed_sig_statuses = {
            Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
            Phase4CalibrationStatus.REVALIDATED_RESEARCH,
        }
        if sig_status_enum not in allowed_sig_statuses:
            return None, None

        # Validate Risk Profile Status if present
        risk_dict = artifact_data.get("risk_profile")
        if isinstance(risk_dict, dict):
            raw_risk_status = risk_dict.get("calibration_status") or artifact_data.get("calibration_status")
            if raw_risk_status:
                try:
                    risk_status_enum = Phase5CalibrationStatus(str(raw_risk_status).strip())
                    allowed_risk_statuses = {
                        Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN,
                        Phase5CalibrationStatus.REVALIDATED_RESEARCH,
                    }
                    if risk_status_enum not in allowed_risk_statuses:
                        return None, None
                except (ValueError, TypeError):
                    return None, None

        # 7. Deterministic Artifact Fingerprint Validation (if present)
        declared_fp = artifact_data.get("artifact_fingerprint") or artifact_data.get("fingerprint")
        if declared_fp:
            if not isinstance(declared_fp, str):
                return None, None
            computed_fp = compute_calibration_artifact_fingerprint(artifact_data)
            if declared_fp.strip().lower() != computed_fp.lower():
                return None, None

        signal_profile_dict = sig_dict
        risk_profile_dict = risk_dict

    # Reconstruct Signal Profile
    if signal_profile_dict and isinstance(signal_profile_dict, dict):
        try:
            raw_cal_status = signal_profile_dict.get("calibration_status", "CANDIDATE_NOT_FROZEN")
            cal_status = Phase4CalibrationStatus(str(raw_cal_status).strip())
            allowed_sig_statuses = {
                Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
                Phase4CalibrationStatus.REVALIDATED_RESEARCH,
            }
            if cal_status not in allowed_sig_statuses:
                return None, None

            ld_dict = signal_profile_dict.get("long_direction", {})
            sd_dict = signal_profile_dict.get("short_direction", {})
            lt_dict = signal_profile_dict.get("long_timing", {})
            st_dict = signal_profile_dict.get("short_timing", {})
            lg_dict = signal_profile_dict.get("long_gate", {})
            sg_dict = signal_profile_dict.get("short_gate", {})
            fp_dict = signal_profile_dict.get("feed_policy", {})

            ld = SideDirectionPolicy(**ld_dict) if isinstance(ld_dict, dict) else SideDirectionPolicy()
            sd = SideDirectionPolicy(**sd_dict) if isinstance(sd_dict, dict) else SideDirectionPolicy()
            lt = SideTimingPolicy(**lt_dict) if isinstance(lt_dict, dict) else SideTimingPolicy()
            st = SideTimingPolicy(**st_dict) if isinstance(st_dict, dict) else SideTimingPolicy()
            lg = SideGatePolicy(**lg_dict) if isinstance(lg_dict, dict) else SideGatePolicy()
            sg = SideGatePolicy(**sg_dict) if isinstance(sg_dict, dict) else SideGatePolicy()
            fp = Phase4FeedPolicy(**fp_dict) if isinstance(fp_dict, dict) else Phase4FeedPolicy()

            # Completeness and mathematical integrity check
            if not (
                ld.is_configured and sd.is_configured
                and lt.is_configured and st.is_configured
                and lg.is_configured and sg.is_configured
            ):
                return None, None

            sig_prof = Phase4SignalProfile(
                name=str(signal_profile_dict.get("name", "XAUUSD_RESEARCH")),
                long_direction=ld,
                short_direction=sd,
                long_timing=lt,
                short_timing=st,
                long_gate=lg,
                short_gate=sg,
                feed_policy=fp,
                calibration_status=cal_status,
                details=signal_profile_dict.get("details", {}),
            )
        except Exception:
            return None, None

    # Reconstruct Risk Profile
    if risk_profile_dict and isinstance(risk_profile_dict, dict):
        try:
            def _to_dec(val: Any) -> Optional[Decimal]:
                if val is None:
                    return None
                if isinstance(val, Decimal):
                    return val
                try:
                    return Decimal(str(val))
                except Exception:
                    return None

            raw_lr = risk_profile_dict.get("long_risk_policy", {})
            raw_sr = risk_profile_dict.get("short_risk_policy", {})
            raw_le = risk_profile_dict.get("long_execution_policy", {})
            raw_se = risk_profile_dict.get("short_execution_policy", {})

            lr = SideRiskPolicy(**{k: _to_dec(v) for k, v in raw_lr.items()}) if isinstance(raw_lr, dict) else SideRiskPolicy()
            sr = SideRiskPolicy(**{k: _to_dec(v) for k, v in raw_sr.items()}) if isinstance(raw_sr, dict) else SideRiskPolicy()

            le_kwargs = {}
            if isinstance(raw_le, dict):
                for k, v in raw_le.items():
                    if k == "latency_seconds":
                        le_kwargs[k] = float(v) if v is not None else None
                    else:
                        le_kwargs[k] = _to_dec(v)
            le = XauUsdExecutionPolicy(**le_kwargs)

            se_kwargs = {}
            if isinstance(raw_se, dict):
                for k, v in raw_se.items():
                    if k == "latency_seconds":
                        se_kwargs[k] = float(v) if v is not None else None
                    else:
                        se_kwargs[k] = _to_dec(v)
            se = XauUsdExecutionPolicy(**se_kwargs)

            # Completeness check
            if not (lr.is_configured and sr.is_configured):
                return None, None

            raw_cal_risk = risk_profile_dict.get("calibration_status", "CANDIDATE_NOT_FROZEN")
            cal_risk_status = Phase5CalibrationStatus(str(raw_cal_risk).strip())

            risk_prof = XauUsdRiskProfile(
                name=str(risk_profile_dict.get("name", "XAUUSD_RESEARCH")),
                calibration_status=cal_risk_status,
                long_risk_policy=lr,
                short_risk_policy=sr,
                long_execution_policy=le,
                short_execution_policy=se,
            )
        except Exception:
            return None, None

    return sig_prof, risk_prof


@shared_task(queue="backtest", bind=True, max_retries=1)
def run_xauusd_backtest_task(
    self,
    start_time_iso: str,
    end_time_iso: str,
    dataset_hash: str,
    code_revision: str,
    cost_scenario: str,  # REQUIRED: No silent default
    holding_horizon_bars_15m: int,  # REQUIRED: Explicit horizon
    max_fill_wait_bars_15m: int,  # REQUIRED: Explicit fill-search horizon
    signal_profile_id: Optional[str] = None,
    risk_profile_id: Optional[str] = None,
    calibration_artifact_id: Optional[str] = None,
    signal_profile_dict: Optional[Dict[str, Any]] = None,
    risk_profile_dict: Optional[Dict[str, Any]] = None,
    entry_fee_bps: Optional[str] = None,
    exit_fee_bps: Optional[str] = None,
    synthetic_spread_bps: Optional[str] = None,
    entry_slippage_bps: Optional[str] = None,
    exit_slippage_bps: Optional[str] = None,
    ablation_type: str = "BASELINE",
    holding_horizon_seconds: Optional[float] = None,
    max_fill_wait_seconds: Optional[float] = None,
    execution_policy: str = "NEXT_BAR_OPEN",
    intrabar_policy: str = "LOWER_TIMEFRAME_REPLAY",
    engine_version: str = "4.0.0-xauusd",
    config_version: str = "cfg-xauusd-2026-v1",
    feature_version: str = "feat-xauusd-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "5.0.0-xauusd",
    execution_model_version: str = "5.0.0-exec-v1",
    backtest_version: str = "6.0.0-xauusd",
) -> dict:
    """
    Asynchronous Celery task for running a point-in-time backtest for canonical XAUUSD.
    Payload arguments must be native JSON-serializable primitives (strings, ints, dicts).
    """
    if not code_revision or not code_revision.strip():
        raise ValueError("Explicit code_revision is strictly required for XAUUSD backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if start_dt.tzinfo is None or start_dt.tzinfo.utcoffset(start_dt) is None:
        raise ValueError("start_time_iso must include an explicit timezone offset (naive timestamps forbidden).")
    if end_dt.tzinfo is None or end_dt.tzinfo.utcoffset(end_dt) is None:
        raise ValueError("end_time_iso must include an explicit timezone offset (naive timestamps forbidden).")

    # Resolve research profiles server-side
    signal_profile, risk_profile = resolve_xauusd_research_profiles(
        signal_profile_id=signal_profile_id,
        risk_profile_id=risk_profile_id,
        calibration_artifact_id=calibration_artifact_id,
        signal_profile_dict=signal_profile_dict,
        risk_profile_dict=risk_profile_dict,
    )

    # If caller has not supplied configured profiles, return CALIBRATION_REQUIRED without fake completion
    if signal_profile is None or risk_profile is None:
        return {
            "status": "CALIBRATION_REQUIRED",
            "message": "Explicit calibrated signal_profile and risk_profile are required before executing empirical backtest.",
        }

    exec_policy_enum = EntryExecutionPolicy(execution_policy)
    intrabar_policy_enum = IntrabarPolicy(intrabar_policy)

    if cost_scenario == "EMPIRICAL":
        if (
            entry_fee_bps is None
            or exit_fee_bps is None
            or synthetic_spread_bps is None
            or entry_slippage_bps is None
            or exit_slippage_bps is None
        ):
            raise ValueError(
                "cost_scenario EMPIRICAL strictly requires all 5 friction parameters: "
                "entry_fee_bps, exit_fee_bps, synthetic_spread_bps, entry_slippage_bps, exit_slippage_bps."
            )
        cost_cfg = XauUsdCostConfig.empirical(
            entry_fee_bps=Decimal(str(entry_fee_bps)),
            exit_fee_bps=Decimal(str(exit_fee_bps)),
            synthetic_spread_bps=Decimal(str(synthetic_spread_bps)),
            entry_slippage_bps=Decimal(str(entry_slippage_bps)),
            exit_slippage_bps=Decimal(str(exit_slippage_bps)),
        )
    elif cost_scenario == "IDEALIZED":
        cost_cfg = XauUsdCostConfig.idealized()
    else:
        raise ValueError(f"Unknown cost_scenario: {cost_scenario}")

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_dt,
        end_time=end_dt,
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=cost_cfg,
        cost_scenario=XauUsdCostScenario(cost_scenario),
        dataset_hash=dataset_hash,
        holding_horizon_bars_15m=holding_horizon_bars_15m,
        holding_horizon_seconds=holding_horizon_seconds,
        max_fill_wait_bars_15m=max_fill_wait_bars_15m,
        max_fill_wait_seconds=max_fill_wait_seconds,
        execution_policy=exec_policy_enum,
        intrabar_policy=intrabar_policy_enum,
        engine_version=engine_version,
        config_version=config_version,
        feature_version=feature_version,
        cycle_version=cycle_version,
        risk_version=risk_version,
        execution_model_version=execution_model_version,
        backtest_version=backtest_version,
        code_revision=code_revision.strip(),
        ablation_type=XauUsdAblationType(ablation_type),
        signal_profile=signal_profile,
        risk_profile=risk_profile,
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle
        from apps.instruments.models import ListingRole, ListingStatus, MarketListing
        from django.db import models

        # Resolve active PRIMARY_XAUUSD_SPOT listing before loading candles (fail-closed if missing)
        prim_listing = MarketListing.objects.filter(
            instrument__base_asset__code="XAU",
            instrument__quote_asset__code="USD",
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        ).first()
        prim_prov = (
            str(prim_listing.provider).strip().lower()
            if (prim_listing and prim_listing.provider)
            else None
        )
        if not prim_listing or not prim_prov:
            return {
                "status": "EVIDENCE_NOT_CONFIGURED",
                "message": "PRIMARY_XAUUSD_SPOT listing is required to run XAUUSD backtest.",
            }

        # Load XAUUSD candles strictly for the active primary source
        for tf in ("15m", "1h", "4h", "1d", "1m", "5m"):
            candles_qs = (
                MarketCandle.objects.filter(
                    timeframe=tf,
                    timestamp_close__gte=start_dt,
                    timestamp_close__lt=end_dt,
                    is_closed=True,
                    instrument__base_asset__code="XAU",
                    instrument__quote_asset__code="USD",
                )
                .filter(
                    models.Q(source__iexact=prim_prov) | models.Q(source=prim_listing.provider)
                )
                .order_by("timestamp_close", "id")
            )

            for c in candles_qs:
                dataset.add_candle(
                    tf,
                    CandleData(
                        timestamp_open=c.timestamp_open,
                        timestamp_close=c.timestamp_close,
                        open=c.open,
                        high=c.high,
                        low=c.low,
                        close=c.close,
                        volume=c.volume,
                        is_closed=c.is_closed,
                        source_id=c.source,
                    ),
                )

        from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity_from_dataset
        computed_dataset_hash = compute_xauusd_dataset_identity_from_dataset(dataset, start_dt, end_dt)
        if dataset_hash != computed_dataset_hash:
            raise ValueError(f"dataset_hash mismatch: expected '{dataset_hash}', computed '{computed_dataset_hash}'")

        if exec_policy_enum == EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
            quotes = dataset.get_quotes(start_dt, end_dt)
            if not quotes:
                return {
                    "status": "EVIDENCE_NOT_CONFIGURED",
                    "message": "Quotes evidence required for MARKET_AFTER_SIGNAL execution policy is not configured or persisted.",
                }

        runner = XauUsdBacktestRunner(
            execution_policy=exec_policy_enum,
            intrabar_policy=intrabar_policy_enum,
        )
        metrics, trades, signals, run_fp = runner.run_point_in_time(dataset=dataset, spec=spec)
        run_obj, created = persist_xauusd_backtest_run(
            spec=spec,
            metrics=metrics,
            trades=trades,
            run_fingerprint=run_fp,
            dataset_identity=computed_dataset_hash,
        )

        return {
            "status": "COMPLETED",
            "run_fingerprint": run_obj.run_fingerprint,
            "created": created,
            "net_expectancy_r": metrics.net_expectancy_r,
            "trade_count": metrics.trade_count,
            "long_trade_count": metrics.long_trade_count,
            "short_trade_count": metrics.short_trade_count,
        }
    except Exception as exc:
        BacktestRun.objects.update_or_create(
            run_fingerprint=f"failed-xauusd-{dataset_hash[:16]}-{code_revision[:8]}",
            defaults={
                "instrument": "XAUUSD",
                "dataset_identity": dataset_hash,
                "historical_start": start_dt,
                "historical_end": end_dt,
                "code_revision": code_revision,
                "status": "FAILED",
                "error_message": str(exc),
            },
        )
        raise exc
