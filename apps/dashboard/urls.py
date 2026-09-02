"""URL configuration for the AurumIQ Dashboard and REST APIs (Phase 7)."""
from django.urls import path

from apps.dashboard.api import (
    BacktestRunLaunchAPIView,
    BacktestStatusAPIView,
    ChartDataAPIView,
    LiveProjectionAPIView,
    RiskProjectionAPIView,
    SignalHistoryAPIView,
    SystemHealthAPIView,
)
from apps.dashboard.views import (
    AuditLogView,
    BacktestLabView,
    DataIntegrityView,
    LiveAnalysisView,
    OverviewView,
    SignalsHistoryView,
    SystemHealthView,
    TimeCycleLabView,
)

app_name = "dashboard"

urlpatterns = [
    # 8 Server-rendered navigation pages
    path("", OverviewView.as_view(), name="overview"),
    path("analysis/", LiveAnalysisView.as_view(), name="analysis"),
    path("cycles/", TimeCycleLabView.as_view(), name="cycles"),
    path("signals/", SignalsHistoryView.as_view(), name="signals"),
    path("backtest/", BacktestLabView.as_view(), name="backtest"),
    path("data/", DataIntegrityView.as_view(), name="data_integrity"),
    path("health/", SystemHealthView.as_view(), name="system_health"),
    path("audit/", AuditLogView.as_view(), name="audit"),

    # REST APIs
    path("api/projection/", LiveProjectionAPIView.as_view(), name="api_projection"),
    path("api/signals/", SignalHistoryAPIView.as_view(), name="api_signals"),
    path("api/risk/", RiskProjectionAPIView.as_view(), name="api_risk"),
    path("api/health/", SystemHealthAPIView.as_view(), name="api_health"),
    path("api/chart/<str:timeframe>/", ChartDataAPIView.as_view(), name="api_chart"),
    path("api/backtest/status/", BacktestStatusAPIView.as_view(), name="api_backtest_status"),
    path("api/backtest/run/", BacktestRunLaunchAPIView.as_view(), name="api_backtest_run"),
]
