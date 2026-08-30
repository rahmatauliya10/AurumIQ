"""Deterministic fingerprinting and provenance generator for Phase 6 backtest runs."""
import hashlib
from engine.backtest.types import BacktestRunSpec


def compute_backtest_fingerprint(spec: BacktestRunSpec) -> str:
    """
    Generate a canonical SHA-256 fingerprint for a BacktestRunSpec.

    Strict Invariants (P6-31..P6-33, A36):
      1. Determinism: Same spec inputs always produce identical hash.
      2. Sensitivity: Any mutation in config, dataset hash, cost, or code revision changes hash.
    """
    h = hashlib.sha256()
    h.update(f"instrument:{spec.instrument}".encode("utf-8"))
    h.update(f"start:{spec.start_time.isoformat()}".encode("utf-8"))
    h.update(f"end:{spec.end_time.isoformat()}".encode("utf-8"))
    h.update(f"timeframes:{','.join(spec.timeframes)}".encode("utf-8"))
    h.update(f"dataset_hash:{spec.dataset_hash}".encode("utf-8"))
    h.update(f"cost_scenario:{spec.cost_scenario.value}".encode("utf-8"))
    h.update(f"entry_fee:{spec.cost_config.entry_fee_bps}".encode("utf-8"))
    h.update(f"exit_fee:{spec.cost_config.exit_fee_bps}".encode("utf-8"))
    h.update(f"spread:{spec.cost_config.synthetic_spread_bps}".encode("utf-8"))
    h.update(f"entry_slip:{spec.cost_config.entry_slippage_bps}".encode("utf-8"))
    h.update(f"exit_slip:{spec.cost_config.exit_slippage_bps}".encode("utf-8"))
    h.update(f"engine_ver:{spec.engine_version}".encode("utf-8"))
    h.update(f"config_ver:{spec.config_version}".encode("utf-8"))
    h.update(f"feature_ver:{spec.feature_version}".encode("utf-8"))
    h.update(f"cycle_ver:{spec.cycle_version}".encode("utf-8"))
    h.update(f"risk_ver:{spec.risk_version}".encode("utf-8"))
    h.update(f"exec_ver:{spec.execution_model_version}".encode("utf-8"))
    h.update(f"backtest_ver:{spec.backtest_version}".encode("utf-8"))
    h.update(f"ablation:{spec.ablation_type.value}".encode("utf-8"))
    h.update(f"code_rev:{spec.code_revision}".encode("utf-8"))
    return h.hexdigest()
