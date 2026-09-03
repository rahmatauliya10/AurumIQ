"""Configuration for the local read-only MT5 market data bridge."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Mt5BridgeConfig:
    """Immutable configuration for MT5 Bridge microservice."""
    host: str = "127.0.0.1"  # STRICT: never bind 0.0.0.0
    port: int = 8001
    canonical_instrument: str = "XAUUSD"
    provider_id: str = "mt5_exness_demo"
    broker_name: str = "Exness"
    terminal_path: str = ""
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "Mt5BridgeConfig":
        """Load settings from environment with safe local defaults."""
        host = os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1").strip()
        if host == "0.0.0.0":
            raise ValueError("Binding to 0.0.0.0 is strictly forbidden by AurumIQ security policy.")

        try:
            port = int(os.environ.get("MT5_BRIDGE_PORT", "8001"))
        except ValueError:
            port = 8001

        return cls(
            host=host,
            port=port,
            canonical_instrument=os.environ.get("MT5_BRIDGE_CANONICAL_SYMBOL", "XAUUSD").strip(),
            provider_id=os.environ.get("MT5_BRIDGE_PROVIDER_ID", "mt5_exness_demo").strip(),
            broker_name=os.environ.get("MT5_BRIDGE_BROKER_NAME", "Exness").strip(),
            terminal_path=os.environ.get("MT5_TERMINAL_PATH", "").strip(),
            timeout_seconds=float(os.environ.get("MT5_BRIDGE_TIMEOUT_SECONDS", "15.0")),
        )
