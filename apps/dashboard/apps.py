"""App configuration for dashboard."""
from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "Product Presentation Dashboard"

    def ready(self) -> None:
        try:
            from engine.backtest.xauusd_governance import register_dynamic_observation_resolver

            def _resolve_obs_window():
                from apps.live_monitor.models import Phase8OperationalState
                state = Phase8OperationalState.objects.filter(id=1).first()
                if state and state.observation_window_start:
                    return state.observation_window_start
                return None

            register_dynamic_observation_resolver(_resolve_obs_window)
        except Exception:
            pass
