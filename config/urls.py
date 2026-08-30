"""URL configuration for XAUT Signal Intelligence."""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse


def health_check(request):
    """Basic health check endpoint for container probes."""
    return JsonResponse({"status": "ok", "service": "xaut-signal-intelligence"})


from apps.live_monitor.views import LivenessHealthView, ReadinessHealthView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("health/", health_check, name="health_check"),
    path("health/live/", LivenessHealthView.as_view(), name="health_live"),
    path("health/ready/", ReadinessHealthView.as_view(), name="health_ready"),
    path("live/", include("apps.live_monitor.urls", namespace="live_monitor")),
    path("", include("apps.live_monitor.urls", namespace="root_live")),
]
