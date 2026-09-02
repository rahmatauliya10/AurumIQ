"""Transport integrations for informational alerts (Webhook / Telegram).
Default: NOT_CONFIGURED / DISABLED.
"""
from typing import Any, Dict, Optional, Tuple
import json
import structlog
from django.conf import settings

from apps.alerts.models import AlertEvent, AlertStatus
from apps.alerts.types import AlertPayload

logger = structlog.get_logger(__name__)


class AlertTransportDispatcher:
    """
    Dispatcher managing external transports (Webhook, Telegram).
    Strict Invariants:
      1. Default is NOT_CONFIGURED / DISABLED.
      2. Never performs external requests if transport is unconfigured.
      3. Tests mock transport functions.
    """

    @classmethod
    def is_webhook_configured(cls) -> bool:
        url = getattr(settings, "ALERT_WEBHOOK_URL", None)
        return bool(url and str(url).strip() and not str(url).startswith("NOT_CONFIGURED"))

    @classmethod
    def is_telegram_configured(cls) -> bool:
        token = getattr(settings, "ALERT_TELEGRAM_BOT_TOKEN", None)
        chat_id = getattr(settings, "ALERT_TELEGRAM_CHAT_ID", None)
        return bool(
            token
            and chat_id
            and str(token).strip()
            and str(chat_id).strip()
            and not str(token).startswith("NOT_CONFIGURED")
        )

    @classmethod
    def dispatch_alert(cls, alert_record: AlertEvent) -> Tuple[str, Optional[str]]:
        """
        Dispatch an AlertEvent record to configured channels.
        Returns (status_result, error_message).
        """
        webhook_ok = cls.is_webhook_configured()
        tg_ok = cls.is_telegram_configured()

        if not webhook_ok and not tg_ok:
            logger.debug(
                "alert_transport_disabled",
                event_id=alert_record.event_id,
                event_type=alert_record.event_type,
            )
            alert_record.status = AlertStatus.DISABLED
            alert_record.save(update_fields=["status"])
            return AlertStatus.DISABLED, None

        errors = []
        dispatched_any = False

        if webhook_ok:
            success, err = cls._send_webhook(alert_record.payload)
            if success:
                dispatched_any = True
            else:
                errors.append(f"Webhook error: {err}")

        if tg_ok:
            success, err = cls._send_telegram(alert_record.payload)
            if success:
                dispatched_any = True
            else:
                errors.append(f"Telegram error: {err}")

        if dispatched_any:
            alert_record.status = AlertStatus.DISPATCHED
            alert_record.dispatch_error = "; ".join(errors) if errors else ""
            alert_record.save(update_fields=["status", "dispatch_error"])
            return AlertStatus.DISPATCHED, None
        else:
            alert_record.status = AlertStatus.FAILED
            alert_record.dispatch_error = "; ".join(errors)
            alert_record.save(update_fields=["status", "dispatch_error"])
            return AlertStatus.FAILED, alert_record.dispatch_error

    @classmethod
    def _send_webhook(cls, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Send JSON payload via HTTP webhook (pure transport)."""
        import urllib.request
        import urllib.error
        url = getattr(settings, "ALERT_WEBHOOK_URL", None)
        if not url:
            return False, "ALERT_WEBHOOK_URL not configured"

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "AurumIQ-Alerts/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if 200 <= resp.status < 300:
                    return True, None
                return False, f"HTTP {resp.status}"
        except Exception as e:
            return False, str(e)

    @classmethod
    def _send_telegram(cls, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Send formatted alert message via Telegram Bot API."""
        import urllib.request
        import urllib.parse
        token = getattr(settings, "ALERT_TELEGRAM_BOT_TOKEN", None)
        chat_id = getattr(settings, "ALERT_TELEGRAM_CHAT_ID", None)
        if not token or not chat_id:
            return False, "Telegram credentials not configured"

        text = (
            f"🔔 *AURUMIQ ALERT: {payload.get('event_type')}*\n"
            f"Symbol: {payload.get('display_symbol')}\n"
            f"Candidate: {payload.get('candidate_user_decision')} ({payload.get('candidate_state')})\n"
            f"Published: {payload.get('published_user_decision')}\n"
            f"Side: {payload.get('side', 'N/A')}\n"
            f"Entry: [{payload.get('entry_min')}, {payload.get('entry_max')}]\n"
            f"Stop: {payload.get('stop_final')} | TP1: {payload.get('tp1')}\n"
            f"⚠️ _{payload.get('disclaimer')}_"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if 200 <= resp.status < 300:
                    return True, None
                return False, f"HTTP {resp.status}"
        except Exception as e:
            return False, str(e)
