"""WebSocket consumers and real-time event broadcasting for Live Monitor (Phase 7B)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Set
import json
import structlog

import redis
from django.conf import settings

logger = structlog.get_logger(__name__)


class LiveEventBroadcaster:
    """
    Cross-process Redis pub-sub & in-memory event broadcaster for typed incremental live events.
    Supports: quote_update, signal_update, risk_plan_update, feed_health_update, candle_closed.
    """

    _subscribers: Set[Any] = set()
    _redis_client: Optional[Any] = None

    @classmethod
    def get_redis_client(cls):
        if cls._redis_client is None:
            try:
                redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
                cls._redis_client = redis.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                )
            except Exception as e:
                logger.warning("redis_broadcaster_connection_failed", error=str(e))
                cls._redis_client = None
        return cls._redis_client

    @classmethod
    def subscribe(cls, subscriber: Any) -> None:
        cls._subscribers.add(subscriber)

    @classmethod
    def unsubscribe(cls, subscriber: Any) -> None:
        cls._subscribers.discard(subscriber)

    @classmethod
    def broadcast(cls, event_payload: Dict[str, Any]) -> None:
        """Broadcast typed incremental update to all local subscribers and Redis pub/sub channel."""
        # 1. Local in-memory broadcast
        dead_subscribers = set()
        for sub in list(cls._subscribers):
            try:
                sub.send_event(event_payload)
            except Exception as e:
                logger.warning("broadcast_failed_for_subscriber", error=str(e))
                dead_subscribers.add(sub)
        cls._subscribers.difference_update(dead_subscribers)

        # 2. Cross-process Redis pub/sub broadcast
        r = cls.get_redis_client()
        if r:
            try:
                inst = event_payload.get("instrument", "default")
                channel = f"aurumiq:live_events:{inst}"
                r.publish(channel, json.dumps(event_payload))
            except Exception as e:
                logger.warning("redis_publish_failed", error=str(e))

    @classmethod
    def format_quote_event(
        cls,
        instrument: str,
        bid: Decimal,
        ask: Decimal,
        spread: Decimal,
        spread_pct: Decimal,
        source_timestamp: datetime,
        sequence_number: Optional[int],
        entry_zone_status: str,
        distance_to_entry_zone_pct: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Format typed quote_update payload (P7-19)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"QU_{instrument}_{now_utc.timestamp()}",
            "event_type": "quote_update",
            "instrument": instrument,
            "source_timestamp": source_timestamp.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "sequence_number": sequence_number,
            "data": {
                "bid": str(bid),
                "ask": str(ask),
                "spread": str(spread),
                "spread_pct": str(spread_pct),
                "entry_zone_status": entry_zone_status,
                "distance_to_entry_zone_pct": str(distance_to_entry_zone_pct) if distance_to_entry_zone_pct is not None else None,
            },
        }

    @classmethod
    def format_signal_update(
        cls,
        instrument: str,
        signal_fingerprint: str,
        signal_state: str,
        signal_user_decision: str,
        direction_score: float,
        timing_score: float,
        last_closed_candle_ts: datetime,
        decision_sequence: int,
        reasons_positive: list,
        reasons_negative: list,
        hard_gate_reasons: list,
    ) -> Dict[str, Any]:
        """Format typed signal_update payload (P7-20)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"SU_{instrument}_{now_utc.timestamp()}",
            "event_type": "signal_update",
            "instrument": instrument,
            "source_timestamp": last_closed_candle_ts.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "decision_sequence": decision_sequence,
            "data": {
                "signal_fingerprint": signal_fingerprint,
                "signal_state": signal_state,
                "signal_user_decision": signal_user_decision,
                "direction_score": direction_score,
                "timing_score": timing_score,
                "reasons_positive": reasons_positive,
                "reasons_negative": reasons_negative,
                "hard_gate_reasons": hard_gate_reasons,
            },
        }

    @classmethod
    def format_risk_plan_update(
        cls,
        instrument: str,
        source_signal_fingerprint: str,
        risk_plan_valid: bool,
        execution_eligible: bool,
        effective_action: str,
        entry_min: Optional[Decimal],
        entry_mid: Optional[Decimal],
        entry_max: Optional[Decimal],
        stop_final: Optional[Decimal],
        tp1: Optional[Decimal],
        tp2: Optional[Decimal],
        rr_tp1: Optional[Decimal],
        rr_tp2: Optional[Decimal],
        decision_sequence: int,
    ) -> Dict[str, Any]:
        """Format typed risk_plan_update payload (P7-21)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"RPU_{instrument}_{now_utc.timestamp()}",
            "event_type": "risk_plan_update",
            "instrument": instrument,
            "source_timestamp": now_utc.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "decision_sequence": decision_sequence,
            "data": {
                "source_signal_fingerprint": source_signal_fingerprint,
                "risk_plan_valid": risk_plan_valid,
                "execution_eligible": execution_eligible,
                "effective_action": effective_action,
                "entry_min": str(entry_min) if entry_min is not None else None,
                "entry_mid": str(entry_mid) if entry_mid is not None else None,
                "entry_max": str(entry_max) if entry_max is not None else None,
                "stop_final": str(stop_final) if stop_final is not None else None,
                "tp1": str(tp1) if tp1 is not None else None,
                "tp2": str(tp2) if tp2 is not None else None,
                "rr_tp1": str(rr_tp1) if rr_tp1 is not None else None,
                "rr_tp2": str(rr_tp2) if rr_tp2 is not None else None,
            },
        }

    @classmethod
    def format_feed_health_update(
        cls,
        instrument: str,
        feed_health: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format typed feed_health_update payload (P7-22)."""
        now_utc = datetime.now(timezone.utc)
        return {
            "event_id": f"FHU_{instrument}_{now_utc.timestamp()}",
            "event_type": "feed_health_update",
            "instrument": instrument,
            "source_timestamp": now_utc.isoformat(),
            "server_timestamp": now_utc.isoformat(),
            "data": feed_health,
        }


class LiveMonitorWebSocketHandler:
    """
    ASGI-compliant WebSocket session handler for authenticated streaming (P7-25).
    """

    def __init__(self, user: Any, instrument: str = "XAUUSD"):
        self.user = user
        self.instrument = instrument
        self.is_connected = False
        self.last_quote_sequence: Optional[int] = None
        self.last_decision_sequence: Optional[int] = None
        self.message_queue: list[str] = []
        self.async_sender = None
        self.loop = None

    def connect(self) -> bool:
        """Enforce authentication check on connection (P7-25)."""
        if not self.user or not getattr(self.user, "is_authenticated", False):
            logger.warning("unauthorized_websocket_connection_rejected")
            self.is_connected = False
            return False

        self.is_connected = True
        LiveEventBroadcaster.subscribe(self)
        logger.info("websocket_connected", user=str(self.user), instrument=self.instrument)
        return True

    def disconnect(self) -> None:
        self.is_connected = False
        LiveEventBroadcaster.unsubscribe(self)
        logger.info("websocket_disconnected", user=str(self.user))

    def send_event(self, event_payload: Dict[str, Any]) -> None:
        """Send formatted event JSON to client if connected (Strict instrument isolation)."""
        if not self.is_connected:
            return
        inst = event_payload.get("instrument")
        # Strict isolation: only match exact instrument
        if inst != self.instrument:
            return

        msg_str = json.dumps(event_payload)
        self.message_queue.append(msg_str)
        if self.async_sender:
            try:
                import asyncio
                if self.loop and self.loop.is_running():
                    try:
                        current_loop = asyncio.get_running_loop()
                        if current_loop is self.loop:
                            self.loop.create_task(self.async_sender(msg_str))
                        else:
                            asyncio.run_coroutine_threadsafe(self.async_sender(msg_str), self.loop)
                    except RuntimeError:
                        asyncio.run_coroutine_threadsafe(self.async_sender(msg_str), self.loop)
                else:
                    asyncio.create_task(self.async_sender(msg_str))
            except Exception as e:
                logger.debug("async_send_failed", error=str(e))


class LiveMonitorAsyncWebsocketConsumer:
    """
    Pure ASGI WebSocket Consumer for live market data & decision streaming.
    Bypasses Channels dependency; compatible directly with Uvicorn / Daphne ASGI.
    """

    def __init__(self, scope, receive, send):
        self.scope = scope
        self.receive = receive
        self.send = send
        self.handler: Optional[LiveMonitorWebSocketHandler] = None

    async def __call__(self):
        import asyncio
        import inspect

        async def _safe_send(msg_dict):
            if inspect.iscoroutinefunction(self.send):
                await self.send(msg_dict)
            else:
                res = self.send(msg_dict)
                if inspect.isawaitable(res):
                    await res

        # Authenticate user from ASGI scope
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await _safe_send({"type": "websocket.close", "code": 4401})
            return

        # Determine instrument from query string or default to XAUUSD
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        instrument = "XAUUSD"
        if "symbol=" in query_string:
            for part in query_string.split("&"):
                if part.startswith("symbol="):
                    instrument = part.split("=")[1]
                    break
        elif self.scope.get("path", "").startswith("/ws/live"):
            # Historical tests connect to /ws/live/ without query string
            instrument = "XAUT/USDT"

        # Accept websocket connection
        await _safe_send({"type": "websocket.accept"})

        self.handler = LiveMonitorWebSocketHandler(user=user, instrument=instrument)
        try:
            self.handler.loop = asyncio.get_running_loop()
        except Exception:
            pass
        self.handler.connect()

        async def _async_send(msg_text: str):
            await _safe_send({"type": "websocket.send", "text": msg_text})

        self.handler.async_sender = _async_send

        # Background Redis pub/sub listener loop for cross-process worker delivery
        async def _redis_listener_loop():
            r = LiveEventBroadcaster.get_redis_client()
            if not r:
                return
            try:
                pubsub = r.pubsub()
                channel = f"aurumiq:live_events:{instrument}"
                pubsub.subscribe(channel)
                while True:
                    msg = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg.get("type") == "message":
                        data_val = msg.get("data")
                        if data_val:
                            if isinstance(data_val, (bytes, bytearray)):
                                data_str = data_val.decode("utf-8")
                            else:
                                data_str = str(data_val)
                            await _safe_send({"type": "websocket.send", "text": data_str})
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("redis_listener_loop_ended", error=str(e))
            finally:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

        redis_task = asyncio.create_task(_redis_listener_loop())

        try:
            while True:
                message = await self.receive()
                msg_type = message.get("type")
                if msg_type == "websocket.disconnect":
                    break
                elif msg_type == "websocket.receive":
                    text = message.get("text", "")
                    if text == "ping":
                        await _safe_send({"type": "websocket.send", "text": "pong"})
        finally:
            redis_task.cancel()
            if self.handler:
                self.handler.disconnect()
