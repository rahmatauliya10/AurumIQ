/**
 * AurumIQ Real-Time Dashboard Client (Phase 7)
 * Connects to native WebSocket stream and applies typed live updates.
 */
document.addEventListener("DOMContentLoaded", function() {
    let ws = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 10;

    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/live/ws/?symbol=XAUUSD`;

        try {
            ws = new WebSocket(wsUrl);

            ws.onopen = function() {
                reconnectAttempts = 0;
                const pill = document.getElementById("freshness-pill");
                if (pill) {
                    pill.className = "freshness-indicator fresh";
                    document.getElementById("freshness-text").innerText = "LIVE STREAMING";
                }
            };

            ws.onmessage = function(event) {
                try {
                    const payload = JSON.parse(event.data);
                    handleLiveEvent(payload);
                } catch (e) {
                    console.debug("Ignored non-json ws message:", event.data);
                }
            };

            ws.onclose = function() {
                const pill = document.getElementById("freshness-pill");
                if (pill) {
                    pill.className = "freshness-indicator stale";
                    document.getElementById("freshness-text").innerText = "DISCONNECTED / RETRYING";
                }
                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    setTimeout(connectWebSocket, Math.min(1000 * reconnectAttempts, 5000));
                }
            };

            ws.onerror = function() {
                ws.close();
            };
        } catch (e) {
            console.debug("WebSocket connection error:", e);
        }
    }

    function handleLiveEvent(payload) {
        if (!payload || !payload.event_type) return;

        if (payload.event_type === "quote_update" && payload.data) {
            const d = payload.data;
            if (d.bid) setElementText("live-bid", d.bid);
            if (d.ask) setElementText("live-ask", d.ask);
            if (d.spread) setElementText("live-spread", "$" + d.spread);
            if (d.entry_zone_status) setElementText("live-zone-status", d.entry_zone_status);
        } else if (payload.event_type === "signal_update" && payload.data) {
            const d = payload.data;
            if (d.candidate_user_decision) {
                const badge = document.getElementById("candidate-decision");
                if (badge) {
                    badge.innerText = d.candidate_user_decision;
                    badge.parentElement.className = `decision-badge candidate-badge decision-${d.candidate_user_decision.toLowerCase()}`;
                }
            }
            if (d.candidate_state) setElementText("candidate-state", d.candidate_state);
            if (d.candidate_resolution_reason) setElementText("candidate-reason", d.candidate_resolution_reason);
            if (d.long_direction_score !== undefined) setElementText("long-dir-score", Number(d.long_direction_score).toFixed(1));
            if (d.short_direction_score !== undefined) setElementText("short-dir-score", Number(d.short_direction_score).toFixed(1));
            if (d.long_timing_score !== undefined) setElementText("long-tim-score", Number(d.long_timing_score).toFixed(1));
            if (d.short_timing_score !== undefined) setElementText("short-tim-score", Number(d.short_timing_score).toFixed(1));
        } else if (payload.event_type === "risk_plan_update" && payload.data) {
            const d = payload.data;
            if (d.entry_min && d.entry_max) {
                setElementText("geo-entry", `[${d.entry_min} — ${d.entry_max}]`);
            } else {
                setElementText("geo-entry", "—");
            }
            setElementText("geo-stop", d.stop_final || "—");
            setElementText("geo-tp1", d.tp1 || "—");
            setElementText("geo-tp2", d.tp2 || "—");
            setElementText("geo-rr", d.rr_tp1 ? `${d.rr_tp1}R` : "—");
        }
    }

    function setElementText(id, text) {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    }

    // Initialize websocket connection if on overview page
    if (document.getElementById("live-bid")) {
        connectWebSocket();
    }
});
