"""Unit tests for Celery application configuration and 5 named queues."""
import pytest
from django.conf import settings
from config.celery import app as celery_app, debug_task


@pytest.mark.unit
def test_celery_queues_configured():
    """Verify all 5 required Celery queues are defined."""
    queues = settings.CELERY_TASK_QUEUES
    assert "market_data" in queues
    assert "analysis" in queues
    assert "backtest" in queues
    assert "machine_learning" in queues
    assert "maintenance" in queues


@pytest.mark.unit
def test_celery_app_instance():
    """Verify Celery app name and task autodiscovery."""
    assert celery_app.main == "xaut_intelligence"
    assert "config.celery.debug_task" in celery_app.tasks


@pytest.mark.unit
def test_debug_task_execution():
    """Verify smoke debug_task runs without exception."""
    result = debug_task.apply()
    assert result.successful()
