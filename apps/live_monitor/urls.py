"""URL mappings for Live Monitor dashboard, REST APIs, and health probes."""
from django.urls import path

from apps.live_monitor.views import (
    ChartDataAPIView,
    DashboardView,
    HistoryPageView,
    LiveStateAPIView,
    LivenessHealthView,
    ReadinessHealthView,
    RiskPlanHistoryAPIView,
    SignalHistoryAPIView,
)

app_name = "live_monitor"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("history/", HistoryPageView.as_view(), name="history"),
    path("api/state/", LiveStateAPIView.as_view(), name="api_state"),
    path("api/chart/", ChartDataAPIView.as_view(), name="api_chart"),
    path("api/history/signals/", SignalHistoryAPIView.as_view(), name="api_history_signals"),
    path("api/history/risk/", RiskPlanHistoryAPIView.as_view(), name="api_history_risk"),
    path("health/live/", LivenessHealthView.as_view(), name="health_live"),
    path("health/ready/", ReadinessHealthView.as_view(), name="health_ready"),
]
