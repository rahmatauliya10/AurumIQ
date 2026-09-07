"""Focused routing tests verifying root redirects to XAUUSD dashboard while /live/ remains historical compatibility path."""
import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse


@pytest.mark.unit
@pytest.mark.django_db
class TestRoutingRemediation(TestCase):
    """Verify root URL routing seal and legacy live monitor preservation."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="operator_routing_test",
            password="StrongPassword123!",
            email="routing_op@aurumiq.internal",
        )
        self.client = Client()

    def test_root_redirects_to_xauusd_dashboard_overview(self):
        """Root '/' must redirect (HTTP 302) to dashboard:overview ('/dashboard/')."""
        response = self.client.get("/")
        assert response.status_code == 302
        assert response.url == reverse("dashboard:overview")
        assert response.url == "/dashboard/"

    def test_authenticated_root_redirect_follow_reaches_dashboard(self):
        """Authenticated request to '/' following redirect lands on active XAUUSD dashboard overview."""
        self.client.force_login(self.user)
        response = self.client.get("/", follow=True)
        assert response.status_code == 200
        assert response.redirect_chain == [("/dashboard/", 302)]
        content = response.content.decode("utf-8")
        assert "Overview" in content
        assert "/static/dashboard/js/dashboard.js" in content

    def test_live_compatibility_surface_accessible(self):
        """Historical '/live/' compatibility path remains explicitly available."""
        self.client.force_login(self.user)
        response = self.client.get("/live/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "LIVE INTELLIGENCE" in content
        assert "/static/js/dashboard.js" in content

    def test_live_history_compatibility_accessible(self):
        """Historical '/live/history/' compatibility path remains accessible."""
        self.client.force_login(self.user)
        response = self.client.get("/live/history/")
        assert response.status_code == 200
