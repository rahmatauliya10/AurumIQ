"""Integration tests for Phase 7 Authentication & Login Flow (P7-AUTH-01 .. P7-AUTH-07)."""
import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.instruments.models import Asset, AssetType, Instrument, InstrumentRole, InstrumentType
from apps.live_monitor.models import LiveMonitorState


@pytest.mark.django_db
class Phase7AuthFlowIntegrationTests(TestCase):
    def setUp(self):
        self.username = "operator1"
        self.password = "Secur3P@ssw0rd!"
        self.user = User.objects.create_user(
            username=self.username,
            password=self.password,
            email="operator@aurumiq.internal",
        )
        self.client = Client()

        # Create basic instrument & state for dashboard rendering
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
            signal_state="BUY_WINDOW",
            signal_user_decision="BUY",
            effective_action="BUY",
        )

    # --- P7-AUTH-01: Anonymous request to / redirects to /accounts/login/?next=/ ---
    def test_p7_auth_01_anonymous_redirects_to_login(self):
        response = self.client.get("/")
        assert response.status_code == 302
        assert response.url == "/accounts/login/?next=/"

    # --- P7-AUTH-02: GET /accounts/login/ returns HTTP 200 ---
    def test_p7_auth_02_login_page_returns_200(self):
        response = self.client.get("/accounts/login/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "AURUM" in content
        assert "Operator Authentication" in content
        assert "Sign In" in content
        assert "csrfmiddlewaretoken" in content

    # --- P7-AUTH-03: Valid login redirects through ?next=/ and dashboard loads ---
    def test_p7_auth_03_valid_login_redirects_to_dashboard(self):
        response = self.client.post(
            "/accounts/login/?next=/",
            {"username": self.username, "password": self.password},
            follow=True,
        )
        assert response.status_code == 200
        # Check that user is authenticated and dashboard loaded
        assert response.context["user"].is_authenticated
        content = response.content.decode("utf-8")
        assert "XAUT/USDT" in content
        assert "LIVE INTELLIGENCE" in content

    # --- P7-AUTH-04: Invalid credentials do not authenticate ---
    def test_p7_auth_04_invalid_credentials_rejected(self):
        response = self.client.post(
            "/accounts/login/?next=/",
            {"username": self.username, "password": "WrongPassword123"},
            follow=True,
        )
        assert response.status_code == 200
        # Still on login page with error
        assert not response.context["user"].is_authenticated
        content = response.content.decode("utf-8")
        assert "Invalid operator username or password" in content

    # --- P7-AUTH-05: Logout invalidates session ---
    def test_p7_auth_05_logout_invalidates_session(self):
        # Login first
        self.client.login(username=self.username, password=self.password)
        
        # Access dashboard while authenticated
        resp_auth = self.client.get("/")
        assert resp_auth.status_code == 200

        # Logout
        resp_logout = self.client.post("/accounts/logout/", follow=True)
        assert resp_logout.status_code == 200

        # Verify session is invalidated: accessing / redirects to login
        resp_after = self.client.get("/")
        assert resp_after.status_code == 302
        assert resp_after.url == "/accounts/login/?next=/"

    # --- P7-AUTH-06: Protected routes remain inaccessible to anonymous users ---
    def test_p7_auth_06_protected_routes_inaccessible_to_anonymous(self):
        protected_urls = [
            "/",
            "/live/",
            "/history/",
            "/live/history/",
            "/live/api/state/?symbol=XAUT/USDT",
            "/live/api/chart/?symbol=XAUT/USDT",
            "/live/api/history/signals/?symbol=XAUT/USDT",
            "/live/api/history/risk/?symbol=XAUT/USDT",
        ]
        for url in protected_urls:
            resp = self.client.get(url)
            # Either 302 redirect to login (for HTML views) or 401/403 (for DRF APIs)
            assert resp.status_code in (302, 401, 403), f"URL {url} responded with unexpected status: {resp.status_code}"

    # --- P7-AUTH-07: Authenticated users can access dashboard and read-only live APIs ---
    def test_p7_auth_07_authenticated_user_can_access_dashboard_and_apis(self):
        self.client.force_login(self.user)
        
        resp_dash = self.client.get("/")
        assert resp_dash.status_code == 200

        resp_hist = self.client.get("/history/")
        assert resp_hist.status_code == 200

        resp_state = self.client.get("/live/api/state/?symbol=XAUT/USDT")
        assert resp_state.status_code == 200

        resp_chart = self.client.get("/live/api/chart/?symbol=XAUT/USDT")
        assert resp_chart.status_code == 200
