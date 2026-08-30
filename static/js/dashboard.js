/**
 * AurumIQ Live Monitor — Presentation Layer (Phase 7B)
 * 
 * Strict Invariants:
 *  1. Presentation Only: Zero business formulas or decision calculations in JavaScript.
 *  2. Reconnect Reconciliation: On disconnect, fetches canonical REST state and reconciles.
 *  3. Stale Rejection: Discards out-of-order quote sequences or regressed decision revisions.
 */

class AurumIQLiveDashboard {
    constructor() {
        this.symbol = 'XAUT/USDT';
        this.currentState = null;
        this.lastQuoteSequence = null;
        this.lastDecisionSequence = 0;
        this.ws = null;
        this.reconnectTimer = null;
        this.reconnectAttempts = 0;

        this.init();
    }

    init() {
        this.bootstrapInitialState();
        this.initEventListeners();
        this.initChart();
        this.connectWebSocket();
    }

    bootstrapInitialState() {
        const stateEl = document.getElementById('initialStateJson');
        if (stateEl && stateEl.textContent) {
            try {
                this.currentState = JSON.parse(stateEl.textContent);
                if (this.currentState) {
                    this.lastQuoteSequence = this.currentState.quote_sequence;
                    this.lastDecisionSequence = this.currentState.decision_sequence || 0;
                    this.renderFullState(this.currentState);
                }
            } catch (e) {
                console.error('Failed to parse initial state JSON', e);
            }
        }
    }

    initEventListeners() {
        const btnRefresh = document.getElementById('btnRefreshChart');
        if (btnRefresh) {
            btnRefresh.addEventListener('click', () => this.fetchAndRenderChart());
        }
    }

    connectWebSocket() {
        const wsStatusEl = document.getElementById('wsStatus');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/live/?symbol=${encodeURIComponent(this.symbol)}`;

        try {
            // Simulated / ASGI WebSocket connection
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('[AurumIQ] WebSocket connected');
                this.reconnectAttempts = 0;
                this.updateConnectionStatus(true);
                // Reconcile latest canonical state on reconnect (A44)
                this.reconcileCanonicalState();
            };

            this.ws.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    this.handleIncrementalEvent(payload);
                } catch (e) {
                    console.error('[AurumIQ] Failed to parse WebSocket message', e);
                }
            };

            this.ws.onclose = () => {
                console.warn('[AurumIQ] WebSocket disconnected');
                this.updateConnectionStatus(false);
                this.scheduleReconnect();
            };

            this.ws.onerror = (err) => {
                console.error('[AurumIQ] WebSocket error', err);
                this.updateConnectionStatus(false);
            };
        } catch (err) {
            console.warn('[AurumIQ] WebSocket initialization skipped/unsupported in current runtime', err);
            this.updateConnectionStatus(false);
        }
    }

    scheduleReconnect() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        const delay = Math.min(10000, 1000 * Math.pow(1.5, this.reconnectAttempts));
        this.reconnectAttempts++;
        this.reconnectTimer = setTimeout(() => {
            this.connectWebSocket();
        }, delay);
    }

    updateConnectionStatus(isOnline) {
        const dot = document.querySelector('#wsStatus .status-dot');
        const label = document.querySelector('#wsStatus .status-label');
        if (dot && label) {
            dot.className = isOnline ? 'status-dot online' : 'status-dot offline';
            label.textContent = isOnline ? 'CONNECTED' : 'RECONNECTING...';
        }
    }

    async reconcileCanonicalState() {
        try {
            const resp = await fetch(`/live/api/state/?symbol=${encodeURIComponent(this.symbol)}`);
            if (resp.ok) {
                const canonicalState = await resp.json();
                this.currentState = canonicalState;
                this.lastQuoteSequence = canonicalState.quote_sequence;
                this.lastDecisionSequence = canonicalState.decision_sequence || 0;
                this.renderFullState(canonicalState);
            }
        } catch (err) {
            console.error('[AurumIQ] Canonical state reconciliation failed', err);
        }
    }

    handleIncrementalEvent(event) {
        if (!event || event.instrument !== this.symbol) return;

        switch (event.event_type) {
            case 'quote_update':
                this.applyQuoteUpdate(event);
                break;
            case 'signal_update':
                this.applySignalUpdate(event);
                break;
            case 'risk_plan_update':
                this.applyRiskPlanUpdate(event);
                break;
            case 'feed_health_update':
                this.applyFeedHealthUpdate(event);
                break;
            case 'candle_closed':
                this.fetchAndRenderChart();
                break;
            default:
                console.log('[AurumIQ] Unrecognized event', event);
        }
    }

    applyQuoteUpdate(event) {
        // Monotonic sequence validation (P7-23)
        if (event.sequence_number !== null && event.sequence_number !== undefined) {
            if (this.lastQuoteSequence !== null && event.sequence_number <= this.lastQuoteSequence) {
                return; // Discard stale quote
            }
            this.lastQuoteSequence = event.sequence_number;
        }

        const data = event.data;
        if (!data) return;

        const askEl = document.getElementById('heroAsk');
        const spreadEl = document.getElementById('heroSpread');
        const spreadPctEl = document.getElementById('heroSpreadPct');
        const zoneStatusEl = document.getElementById('heroEntryZoneStatus');
        const zoneDistEl = document.getElementById('heroZoneDistance');

        if (askEl) askEl.textContent = data.ask;
        if (spreadEl) spreadEl.textContent = data.spread;
        if (spreadPctEl) spreadPctEl.textContent = `${data.spread_pct}%`;
        if (zoneStatusEl) zoneStatusEl.textContent = data.entry_zone_status;
        if (zoneDistEl && data.distance_to_entry_zone_pct) {
            zoneDistEl.textContent = `${data.distance_to_entry_zone_pct}%`;
        }
    }

    applySignalUpdate(event) {
        // Decision revision monotonicity check (P7-23B)
        if (event.decision_sequence && event.decision_sequence <= this.lastDecisionSequence) {
            return; // Discard stale decision
        }
        if (event.decision_sequence) {
            this.lastDecisionSequence = event.decision_sequence;
        }

        const data = event.data;
        if (!data) return;

        const dirEl = document.getElementById('directionScoreVal');
        const timingEl = document.getElementById('timingScoreVal');
        const fpEl = document.getElementById('signalFingerprint');
        const sigLayerEl = document.getElementById('heroSignalLayer');

        if (dirEl) dirEl.textContent = Math.round(data.direction_score);
        if (timingEl) timingEl.textContent = Math.round(data.timing_score);
        if (fpEl) fpEl.textContent = (data.signal_fingerprint || '').substring(0, 16);
        if (sigLayerEl) {
            sigLayerEl.innerHTML = `Signal Layer: <strong>${data.signal_state}</strong> / <strong>${data.signal_user_decision}</strong>`;
        }

        this.renderReasonsList('positiveReasonsList', data.reasons_positive, '✓', 'positive');
        this.renderReasonsList('negativeReasonsList', data.reasons_negative, '•', 'negative');
    }

    applyRiskPlanUpdate(event) {
        if (event.decision_sequence && event.decision_sequence <= this.lastDecisionSequence) {
            return;
        }
        if (event.decision_sequence) {
            this.lastDecisionSequence = event.decision_sequence;
        }

        const data = event.data;
        if (!data) return;

        this.updateHeroAction(data.effective_action);

        const riskLayerEl = document.getElementById('heroRiskLayer');
        if (riskLayerEl) {
            const statusClass = data.risk_plan_valid ? 'text-success' : 'text-warning';
            const statusText = data.risk_plan_valid ? 'VALID' : `INVALID (effective ${data.effective_action})`;
            riskLayerEl.innerHTML = `Risk Architecture: <strong class="${statusClass}">${statusText}</strong>`;
        }

        const minEl = document.getElementById('riskEntryMin');
        const midEl = document.getElementById('riskEntryMid');
        const maxEl = document.getElementById('riskEntryMax');
        const stopEl = document.getElementById('riskStop');
        const tp1El = document.getElementById('riskTp1');
        const tp2El = document.getElementById('riskTp2');

        if (minEl) minEl.textContent = data.entry_min || '--';
        if (midEl) midEl.textContent = data.entry_mid || '--';
        if (maxEl) maxEl.textContent = data.entry_max || '--';
        if (stopEl) stopEl.textContent = data.stop_final || '--';
        if (tp1El) tp1El.textContent = data.tp1 ? `${data.tp1} (RR ${data.rr_tp1 || '--'})` : '--';
        if (tp2El) tp2El.textContent = data.tp2 ? `${data.tp2} (RR ${data.rr_tp2 || '--'})` : '--';
    }

    applyFeedHealthUpdate(event) {
        const data = event.data;
        if (!data) return;

        const xautEl = document.getElementById('feedXautStatus');
        const xauEl = document.getElementById('feedXauStatus');
        const usdtEl = document.getElementById('feedUsdtStatus');
        const macroEl = document.getElementById('feedMacroStatus');
        const provEl = document.getElementById('feedProviderStatus');

        if (xautEl) xautEl.textContent = data.xaut_status || 'HEALTHY';
        if (xauEl) xauEl.textContent = data.xau_status || 'HEALTHY';
        if (usdtEl) usdtEl.textContent = data.usdt_norm_status || 'HEALTHY';
        if (macroEl) macroEl.textContent = data.macro_status || 'HEALTHY';
        if (provEl) provEl.textContent = data.provider_sync_status || 'HEALTHY';
    }

    updateHeroAction(action) {
        const pill = document.getElementById('heroActionPill');
        const textEl = document.getElementById('heroActionText');
        if (!pill || !textEl) return;

        const act = (action || 'WAIT').toUpperCase();
        pill.className = `action-pill action-${act.toLowerCase()}`;
        textEl.textContent = act;

        const iconEl = pill.querySelector('.action-icon');
        if (iconEl) {
            iconEl.textContent = act === 'BUY' ? '🟢' : act === 'AVOID' ? '🔴' : '🟡';
        }
    }

    renderFullState(state) {
        if (!state) return;
        this.updateHeroAction(state.effective_action);

        const askEl = document.getElementById('heroAsk');
        const spreadEl = document.getElementById('heroSpread');
        const spreadPctEl = document.getElementById('heroSpreadPct');
        if (askEl) askEl.textContent = state.current_ask || '--';
        if (spreadEl) spreadEl.textContent = state.spread || '--';
        if (spreadPctEl) spreadPctEl.textContent = state.spread_pct ? `${state.spread_pct}%` : '--';

        const dirEl = document.getElementById('directionScoreVal');
        const timingEl = document.getElementById('timingScoreVal');
        if (dirEl) dirEl.textContent = Math.round(state.direction_score || 0);
        if (timingEl) timingEl.textContent = Math.round(state.timing_score || 0);

        const zoneStatusEl = document.getElementById('heroEntryZoneStatus');
        if (zoneStatusEl) zoneStatusEl.textContent = state.entry_zone_status || 'NO_ACTIVE_ZONE';
    }

    renderReasonsList(containerId, reasons, bullet, itemClass) {
        const ul = document.getElementById(containerId);
        if (!ul) return;
        ul.innerHTML = '';

        if (!reasons || reasons.length === 0) {
            ul.innerHTML = `<li class="reason-item text-muted">No items recorded.</li>`;
            return;
        }

        reasons.forEach(r => {
            const li = document.createElement('li');
            li.className = `reason-item ${itemClass}`;
            li.innerHTML = `<span class="bullet">${bullet}</span> ${r}`;
            ul.appendChild(li);
        });
    }

    async initChart() {
        await this.fetchAndRenderChart();
    }

    async fetchAndRenderChart() {
        const container = document.getElementById('plotlyChartContainer');
        if (!container || typeof Plotly === 'undefined') return;

        try {
            const resp = await fetch(`/live/api/chart/?symbol=${encodeURIComponent(this.symbol)}&limit=80`);
            if (!resp.ok) return;

            const chartData = await resp.json();
            if (!chartData.timestamps || chartData.timestamps.length === 0) return;

            const traceCandles = {
                x: chartData.timestamps,
                open: chartData.open,
                high: chartData.high,
                low: chartData.low,
                close: chartData.close,
                type: 'candlestick',
                name: 'XAUT/USDT',
                increasing: { line: { color: '#10b981' } },
                decreasing: { line: { color: '#ef4444' } },
            };

            const shapes = [];
            const ov = chartData.overlays || {};

            // Entry corridor shape
            if (ov.entry_min && ov.entry_max) {
                shapes.push({
                    type: 'rect',
                    xref: 'paper',
                    yref: 'y',
                    x0: 0,
                    x1: 1,
                    y0: ov.entry_min,
                    y1: ov.entry_max,
                    fillcolor: 'rgba(245, 158, 11, 0.12)',
                    line: { color: 'rgba(245, 158, 11, 0.5)', width: 1, dash: 'dot' },
                });
            }

            const layout = {
                margin: { t: 20, r: 40, b: 30, l: 50 },
                plot_bgcolor: '#0e1520',
                paper_bgcolor: '#0e1520',
                font: { color: '#94a3b8', family: 'Geist, sans-serif' },
                xaxis: { rangeslider: { visible: false }, gridcolor: '#1f2c3f' },
                yaxis: { gridcolor: '#1f2c3f', side: 'right' },
                shapes: shapes,
                showlegend: false,
            };

            const loader = document.getElementById('chartLoader');
            if (loader) loader.style.display = 'none';

            Plotly.newPlot('plotlyChartContainer', [traceCandles], layout, { responsive: true, displayModeBar: false });
        } catch (e) {
            console.error('[AurumIQ] Chart rendering failed', e);
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.aurumIQDashboard = new AurumIQLiveDashboard();
});
