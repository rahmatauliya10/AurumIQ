"""URL configuration for XAUT Signal Intelligence."""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse


def health_check(request):
    """Basic health check endpoint for container probes."""
    return JsonResponse({"status": "ok", "service": "xaut-signal-intelligence"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health_check"),
]
