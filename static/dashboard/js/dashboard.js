/**
 * AurumIQ Real-Time Dashboard Client (Phase 7 / Phase 8)
 * Connects to native WebSocket stream and manages canonical UI connection states:
 *   - MARKET_CLOSED: Governed XAUUSD closure (badge: MARKET CLOSED, quotes: -)
 *   - LIVE: Market open, WS connected, valid bid/ask quotes
 *   - RECONNECTING: Market open, WS disconnected, reconnect in progress
 *   - FEED_ERROR: Market open, WS connected or reachable, but quote feed unhealthy/unavailable
 *   - CONNECTING: Initial WS handshake
 */
document.addEventListener("DOMContentLoaded", function() {
    // 0. Sidebar Collapse / Expand Toggle & Local Persistence
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebar-toggle");
    if (sidebar && sidebarToggle) {
        const isInitiallyCollapsed = (
            localStorage.getItem("aurumiq_sidebar_collapsed") === "true" ||
            document.documentElement.classList.contains("sidebar-is-collapsed")
        );
        if (isInitiallyCollapsed) {
            sidebar.classList.add("collapsed");
            document.documentElement.classList.add("sidebar-is-collapsed");
            sidebarToggle.setAttribute("aria-expanded", "false");
            sidebarToggle.setAttribute("title", "Expand sidebar");
            const icon = sidebarToggle.querySelector(".toggle-icon");
            if (icon) icon.innerText = "▶";
        } else {
            sidebar.classList.remove("collapsed");
            document.documentElement.classList.remove("sidebar-is-collapsed");
            sidebarToggle.setAttribute("aria-expanded", "true");
            sidebarToggle.setAttribute("title", "Collapse sidebar");
            const icon = sidebarToggle.querySelector(".toggle-icon");
            if (icon) icon.innerText = "◀";
        }

        sidebarToggle.addEventListener("click", function() {
            const currentlyCollapsed = sidebar.classList.toggle("collapsed");
            if (currentlyCollapsed) {
                document.documentElement.classList.add("sidebar-is-collapsed");
                sidebarToggle.setAttribute("aria-expanded", "false");
                sidebarToggle.setAttribute("title", "Expand sidebar");
                const icon = sidebarToggle.querySelector(".toggle-icon");
                if (icon) icon.innerText = "▶";
                try {
                    localStorage.setItem("aurumiq_sidebar_collapsed", "true");
                } catch (e) {}
            } else {
                document.documentElement.classList.remove("sidebar-is-collapsed");
                sidebarToggle.setAttribute("aria-expanded", "true");
                sidebarToggle.setAttribute("title", "Collapse sidebar");
                const icon = sidebarToggle.querySelector(".toggle-icon");
                if (icon) icon.innerText = "◀";
                try {
                    localStorage.setItem("aurumiq_sidebar_collapsed", "false");
                } catch (e) {}
            }
            window.dispatchEvent(new Event("resize"));
        });
    }

    let ws = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 10;

    let isMarketClosed = false;
    let wsConnected = false;
    let hasLiveQuote = false;
    let hasReferencePrice = false;
    let referenceFeedStatus = "HEALTHY";
    let isReconnecting = false;
    let feedError = false;

    // 1. Initial State Hydration from server-rendered JSON
    const initDataEl = document.getElementById("initial-projection");
    if (initDataEl) {
        try {
            const initData = JSON.parse(initDataEl.textContent);
            if (initData.is_market_closed !== undefined) {
                isMarketClosed = Boolean(initData.is_market_closed);
            } else if (initData.market_session) {
                isMarketClosed = (initData.market_session === "CLOSED");
            }
            if (initData.reference_price) {
                hasReferencePrice = true;
            }
            if (initData.reference_feed_status) {
                referenceFeedStatus = initData.reference_feed_status;
            }
            if (initData.current_bid && initData.current_ask) {
                hasLiveQuote = true;
            }
        } catch (e) {
            console.debug("Failed parsing initial projection JSON:", e);
        }
    }

    function updateConnectionStatus() {
        const pill = document.getElementById("freshness-pill");
        const text = document.getElementById("freshness-text");
        if (!pill || !text) return;

        // Invariant 1: Governed Market Closure takes precedence over transport retry
        if (isMarketClosed) {
            pill.className = "freshness-indicator closed";
            text.innerText = "MARKET CLOSED";
            setElementText("live-bid", "-");
            setElementText("live-ask", "-");
            setElementText("live-spread", "-");
            return;
        }

        // Invariant 2: Active Reconnection during Open Market
        if (isReconnecting) {
            pill.className = "freshness-indicator reconnecting";
            text.innerText = "RECONNECTING";
            return;
        }

        // Invariant 3: Initial Handshake
        if (!wsConnected) {
            pill.className = "freshness-indicator connecting";
            text.innerText = "CONNECTING";
            return;
        }

        // Invariant 4: Feed Error during Open Market
        // Only triggered on genuine provider/transport failure, not missing execution bid/ask by design
        const isProviderFault = (referenceFeedStatus === "UNHEALTHY" || referenceFeedStatus === "DOWN" || referenceFeedStatus === "ERROR" || referenceFeedStatus === "STALE");
        if (feedError || isProviderFault || (!hasLiveQuote && !hasReferencePrice)) {
            pill.className = "freshness-indicator error";
            text.innerText = "FEED ERROR";
            return;
        }

        // Invariant 5: Live Streaming with verified real execution bid/ask
        if (hasLiveQuote) {
            pill.className = "freshness-indicator fresh";
            text.innerText = "LIVE";
            return;
        }

        // Invariant 6: Reference Market Data Live (Execution quote unavailable by design)
        if (hasReferencePrice && referenceFeedStatus === "HEALTHY") {
            pill.className = "freshness-indicator ref-live";
            text.innerText = "REFERENCE LIVE";
            setElementText("live-bid", "-");
            setElementText("live-ask", "-");
            setElementText("live-spread", "-");
            return;
        }

        // Fallback connecting
        pill.className = "freshness-indicator connecting";
        text.innerText = "CONNECTING";
    }

    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/live/ws/?symbol=XAUUSD`;

        try {
            ws = new WebSocket(wsUrl);

            ws.onopen = function() {
                reconnectAttempts = 0;
                wsConnected = true;
                isReconnecting = false;
                updateConnectionStatus();
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
                wsConnected = false;
                if (isMarketClosed) {
                    // During scheduled closure, maintain MARKET CLOSED badge without showing DISCONNECTED / RETRYING
                    isReconnecting = false;
                    updateConnectionStatus();
                    // Gentle background reconnect to recover when market reopens
                    setTimeout(connectWebSocket, 15000);
                } else {
                    // Market is OPEN: transition to RECONNECTING
                    isReconnecting = true;
                    updateConnectionStatus();
                    if (reconnectAttempts < maxReconnectAttempts) {
                        reconnectAttempts++;
                        setTimeout(connectWebSocket, Math.min(1000 * reconnectAttempts, 5000));
                    } else {
                        isReconnecting = false;
                        feedError = true;
                        updateConnectionStatus();
                    }
                }
            };

            ws.onerror = function() {
                ws.close();
            };
        } catch (e) {
            console.debug("WebSocket connection error:", e);
            wsConnected = false;
            if (!isMarketClosed) {
                isReconnecting = true;
            }
            updateConnectionStatus();
        }
    }

    function handleLiveEvent(payload) {
        if (!payload || !payload.event_type) return;

        if (payload.event_type === "initial_snapshot" && payload.data) {
            const d = payload.data;
            if (d.is_market_closed !== undefined) {
                isMarketClosed = Boolean(d.is_market_closed);
            } else if (d.market_session) {
                isMarketClosed = (d.market_session === "CLOSED");
            }
            if (d.reference_price) {
                hasReferencePrice = true;
                setElementText("ref-price", d.reference_price);
            }
            if (d.reference_feed_status) {
                referenceFeedStatus = d.reference_feed_status;
                setElementText("ref-feed-status", d.reference_feed_status);
            }
            if (d.execution_quote_available !== undefined) {
                setElementText("execution-quote-status", d.execution_quote_available ? "AVAILABLE" : "NOT AVAILABLE");
            }
            if (d.primary_execution_venue_status) {
                setElementText("primary-venue-status", d.primary_execution_venue_status);
            }
            if (d.secondary_execution_venue_status) {
                setElementText("secondary-venue-status", d.secondary_execution_venue_status);
            }
            if (d.current_bid && d.current_ask) {
                hasLiveQuote = true;
                setElementText("live-bid", d.current_bid);
                setElementText("live-ask", d.current_ask);
                setElementText("live-spread", "$" + (d.spread || "0.00"));
            } else {
                hasLiveQuote = false;
                setElementText("live-bid", "-");
                setElementText("live-ask", "-");
                setElementText("live-spread", "-");
            }
            if (d.entry_zone_status) setElementText("live-zone-status", d.entry_zone_status);
            if (d.last_closed_candle_ts) setElementText("last-candle-ts", d.last_closed_candle_ts);
            updateConnectionStatus();
        } else if (payload.event_type === "quote_update" && payload.data) {
            const d = payload.data;
            if (d.reference_price) {
                hasReferencePrice = true;
                setElementText("ref-price", d.reference_price);
            }
            if (d.bid && d.ask) {
                hasLiveQuote = true;
                feedError = false;
                setElementText("live-bid", d.bid);
                setElementText("live-ask", d.ask);
                setElementText("live-spread", "$" + d.spread);
                setElementText("execution-quote-status", "AVAILABLE");
            }
            if (d.entry_zone_status) setElementText("live-zone-status", d.entry_zone_status);
            updateConnectionStatus();
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
            if (d.risk_plan_invalidation_reason) {
                setElementText("risk-invalidation-text", d.risk_plan_invalidation_reason);
                const box = document.getElementById("risk-invalidation-box");
                if (box) box.style.display = "flex";
            } else if (d.is_valid_risk_plan) {
                const box = document.getElementById("risk-invalidation-box");
                if (box) box.style.display = "none";
            }
        }
    }

    function setElementText(id, text) {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    }

    // Apply initial connection status immediately
    updateConnectionStatus();

    // Initialize websocket connection if on overview page
    if (document.getElementById("live-bid")) {
        connectWebSocket();
    }
});
