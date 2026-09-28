import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAMPION_PATH = (
    ROOT
    / "artifacts"
    / "calibration"
    / "xauusd_calibrated_profile_champion.json"
)


def _load_champion():
    with CHAMPION_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["signal_profile"]


def _runtime_max_direction(direction_policy):
    """
    Current runtime contract:
    - XAUUSD Twelve Data volume evidence is UNAVAILABLE.
    - Therefore Volume Confirmation contributes 0.
    """
    unavailable = {"weight_volume"}

    return sum(
        float(value)
        for key, value in direction_policy.items()
        if key not in unavailable
    )


def _runtime_max_timing(timing_policy):
    """
    Current runtime contract before remediation:
    - Volume Response is unavailable from Twelve Data XAUUSD.
    - Phase 3A snapshot may exist, but cycle_3a_profile is not passed
      into XauUsdSignalEngine, therefore Phase 3A scoring contributes 0.
    """
    unavailable = {
        "weight_phase3a",
        "weight_volume_response",
    }

    return sum(
        float(value)
        for key, value in timing_policy.items()
        if key not in unavailable
    )


def test_champion_buy_window_is_reachable_under_current_runtime_contract():
    profile = _load_champion()

    max_direction = _runtime_max_direction(profile["long_direction"])
    max_timing = _runtime_max_timing(profile["long_timing"])

    required_direction = float(
        profile["long_gate"]["threshold_window_direction"]
    )
    required_timing = float(
        profile["long_gate"]["threshold_window_timing"]
    )

    assert max_direction >= required_direction, (
        "BUY_WINDOW direction is unreachable: "
        f"runtime maximum={max_direction:.2f}, "
        f"required={required_direction:.2f}"
    )

    assert max_timing >= required_timing, (
        "BUY_WINDOW timing is unreachable: "
        f"runtime maximum={max_timing:.2f}, "
        f"required={required_timing:.2f}"
    )


def test_champion_sell_window_is_reachable_under_current_runtime_contract():
    profile = _load_champion()

    max_direction = _runtime_max_direction(profile["short_direction"])
    max_timing = _runtime_max_timing(profile["short_timing"])

    required_direction = float(
        profile["short_gate"]["threshold_window_direction"]
    )
    required_timing = float(
        profile["short_gate"]["threshold_window_timing"]
    )

    assert max_direction >= required_direction, (
        "SELL_WINDOW direction is unreachable: "
        f"runtime maximum={max_direction:.2f}, "
        f"required={required_direction:.2f}"
    )

    assert max_timing >= required_timing, (
        "SELL_WINDOW timing is unreachable: "
        f"runtime maximum={max_timing:.2f}, "
        f"required={required_timing:.2f}"
    )


def test_champion_short_ready_is_reachable_under_current_runtime_contract():
    profile = _load_champion()

    max_direction = _runtime_max_direction(profile["short_direction"])
    required_direction = float(
        profile["short_gate"]["threshold_ready_direction"]
    )

    assert max_direction >= required_direction, (
        "READY_SHORT direction is unreachable: "
        f"runtime maximum={max_direction:.2f}, "
        f"required={required_direction:.2f}"
    )
