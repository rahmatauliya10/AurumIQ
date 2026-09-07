"""URL configuration for AurumIQ."""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse


def health_check(request):
    """Basic health check endpoint for container probes."""
    return JsonResponse({"status": "ok", "service": "aurumiq"})


from apps.live_monitor.views import LivenessHealthView, ReadinessHealthView
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("health/", health_check, name="health_check"),
    path("health/live/", LivenessHealthView.as_view(), name="health_live"),
    path("health/ready/", ReadinessHealthView.as_view(), name="health_ready"),
    path("dashboard/", include("apps.dashboard.urls", namespace="dashboard")),
    path("live/", include("apps.live_monitor.urls", namespace="live_monitor")),
    path("", RedirectView.as_view(pattern_name="dashboard:overview", permanent=False), name="root_overview"),
]
