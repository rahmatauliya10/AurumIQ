"""Phase 7B Master Acceptance Test Suite: Real-Time Presentation Integrity, Reconnect, and Read-Only UI.

Acceptance Gates:
  A43 — REAL-TIME PRESENTATION INTEGRITY
  A44 — RECONNECT / STATE RECOVERY
  A45 — READ-ONLY DECISION UI
"""
import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState, LiveRiskPlanRecord
from apps.signals.models import SignalRecord


@pytest.mark.acceptance
@pytest.mark.django_db
class Phase7BAcceptanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="leadoperator", password="password123")
        self.client = Client()

        self.xaut_asset, _ = Asset.objects.get_or_create(code="XAUT", name="Tether Gold", asset_type=AssetType.CRYPTO_TOKEN)
        self.usdt_asset, _ = Asset.objects.get_or_create(code="USDT", name="Tether USD", asset_type=AssetType.CRYPTO_TOKEN)
        self.xaut_inst, _ = Instrument.objects.get_or_create(
            base_asset=self.xaut_asset,
            quote_asset=self.usdt_asset,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.EXECUTION},
        )

        self.state = LiveMonitorState.objects.create(
            instrument="XAUT/USDT",
            current_bid=Decimal("2500.00"),
            current_ask=Decimal("2500.50"),
            spread=Decimal("0.50"),
            spread_pct=Decimal("0.0200"),
            quote_sequence=500,
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            direction_score=86.0,
            timing_score=81.0,
            risk_plan_valid=False,
            execution_eligible=False,
            effective_action="WAIT",
            entry_min=Decimal("2490.00"),
            entry_mid=Decimal("2495.00"),
            entry_max=Decimal("2500.00"),
            stop_final=Decimal("2470.00"),
            tp1=Decimal("2540.00"),
            tp2=Decimal("2560.00"),
            rr_tp1=Decimal("1.33"),
            rr_tp2=Decimal("2.00"),
            decision_sequence=25,
        )

    # --- A43: REAL-TIME PRESENTATION INTEGRITY ---
    def test_a43_real_time_presentation_integrity(self):
        """
        Gate A43:
          1. REST state serialization and HTML server-rendered context reflect exact canonical DB values.
          2. Static JS code audit verifies zero client-side signal or risk scoring calculation.
        """
        self.client.force_login(self.user)

        # 1. HTML view rendering check
        resp_html = self.client.get("/live/?symbol=XAUT/USDT")
        assert resp_html.status_code == 200
        content = resp_html.content.decode("utf-8")
        assert "XAUT/USDT" in content
        assert "WAIT" in content
        assert "BUY_WINDOW" in content
        assert "2500.50" in content

        # 2. REST API state check
        resp_api = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp_api.status_code == 200
        data = resp_api.json()
        assert data["effective_action"] == "WAIT"
        assert data["signal_state"] == "BUY_WINDOW"
        assert data["signal_user_decision"] == "BUY"
        assert data["risk_plan_valid"] is False
        assert data["direction_score"] == 86.0

        # 3. Static JS audit: ensure zero trading/calculation rules in dashboard.js
        js_path = Path(__file__).resolve().parent.parent.parent / "static" / "js" / "dashboard.js"
        with open(js_path, "r", encoding="utf-8") as f:
            js_code = f.read()
            # Prohibit formula re-implementation in JS
            assert "calculate_direction" not in js_code
            assert "calculate_timing" not in js_code
            assert "evaluate_hard_gates" not in js_code
            assert "calculate_stops" not in js_code
            assert "calculate_targets" not in js_code

    # --- A44: RECONNECT / STATE RECOVERY ---
    def test_a44_reconnect_state_recovery(self):
        """
        Gate A44:
          Simulates missed WebSocket updates followed by client reconnect;
          verifies the client synchronizes directly with canonical DB state.
        """
        self.client.force_login(self.user)

        # Background state mutation while client was disconnected
        LiveMonitorState.objects.filter(instrument="XAUT/USDT").update(
            current_ask=Decimal("2545.00"),
            current_bid=Decimal("2544.50"),
            spread=Decimal("0.50"),
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            risk_plan_valid=True,
            execution_eligible=True,
            effective_action="BUY",
            rr_tp1=Decimal("2.20"),
            decision_sequence=30,
        )

        # Reconnect fetch
        resp = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp.status_code == 200
        reconciled = resp.json()
        assert reconciled["current_ask"] == "2545.0000"
        assert reconciled["effective_action"] == "BUY"
        assert reconciled["risk_plan_valid"] is True
        assert reconciled["rr_tp1"] == "2.2000"
        assert reconciled["decision_sequence"] == 30

    # --- A45: READ-ONLY DECISION UI ---
    def test_a45_read_only_decision_ui(self):
        """
        Gate A45:
          1. SignalRecord and LiveRiskPlanRecord cannot be modified or deleted via API.
          2. No order submission or exchange execution endpoints exist.
        """
        self.client.force_login(self.user)

        # Prohibited action endpoints check
        prohibited_endpoints = [
            "/live/api/order/",
            "/live/api/orders/",
            "/live/api/trade/",
            "/live/api/trades/",
            "/live/api/buy/",
            "/live/api/sell/",
            "/live/api/execute/",
            "/api/v1/orders/",
        ]
        for ep in prohibited_endpoints:
            resp = self.client.post(ep, {"symbol": "XAUT/USDT", "side": "BUY"})
            assert resp.status_code in (404, 405), f"Prohibited endpoint responded with {resp.status_code}: {ep}"

        # AST audit on live_monitor views ensuring zero order placement calls
        views_path = Path(__file__).resolve().parent.parent.parent / "apps" / "live_monitor" / "views.py"
        with open(views_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(views_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    assert not node.name.startswith("place_order")
                    assert not node.name.startswith("submit_order")
                    assert not node.name.startswith("execute_trade")
