# AURUMIQ — XAUUSD SIGNAL INTELLIGENCE

## Full-Python Django Engineering Blueprint — XAUUSD Canonical Edition

**Document Status:** Implementation Blueprint v2.0  
**Date:** 1 September 2026  
**Active Target Instrument:** `XAU/USD` — canonical internal identifier `XAUUSD`  
**Historical Baseline:** `XAUT` / Tether Gold — retained strictly as frozen audit and regression evidence  
**Decision Scope:** `BUY / WAIT / SELL` candidate intelligence with human decision support only  
**Order Execution:** FORBIDDEN — zero live or testnet order placement  

> [!CAUTION]
> ### CRITICAL MIGRATION RULE
> `XAUUSD` is the only active operational signal target.
> 
> Historical `XAUT`, `XAUTUSDT`, USDT/USD normalization, XAUT basis, XAUT exchange fee examples, `XautSignalEngine`, and long-only `BUY_WINDOW` logic may remain only inside explicitly marked **Historical XAUT Frozen Specification / legacy regression** sections.
> 
> They must never be interpreted as current XAUUSD production requirements.

---

# 0. Document Authority, Precedence and Migration Governance

## 0.1 Purpose
This blueprint is the consolidated engineering contract for AurumIQ after migration from the original XAUT-oriented concept to a side-aware XAUUSD decision-support platform.

The application must answer:
> For spot XAUUSD at the current closed-candle timestamp, is there a valid LONG candidate, a valid SHORT candidate, or should the system abstain; what evidence supports that conclusion; where is the structurally valid entry zone; where is invalidation; what are TP1/TP2; what is the conservative planned reward/risk; and is the setup permitted to progress beyond research/paper authority?

## 0.2 Source-of-Truth Precedence
When documents disagree, use this strict order:
1. Verified repository state on `main` and accepted implementation reports/tests.
2. Current XAUUSD addenda in phase specifications.
3. This XAUUSD master blueprint.
4. Historical XAUT frozen sections — audit/regression only, never active XAUUSD defaults.
5. Old examples, screenshots, comments, or prose that conflict with the above are non-authoritative.

## 0.3 No Blanket Search-and-Replace
Do not blindly replace every XAUT string with XAUUSD. Use three classifications:
- **`ACTIVE_XAUUSD`:** operational architecture, UI, API, current tests, and future work.
- **`GENERIC`:** instrument-agnostic infrastructure and pure mathematical components.
- **`LEGACY_XAUT`:** historical frozen behavior retained for audit/regression only.

## 0.4 Current Verified Implementation Position

| Phase | XAUUSD Status | Governance |
| :--- | :--- | :--- |
| **Phase 0** | REUSABLE | Foundation is instrument agnostic |
| **Phase 1** | CORE MIGRATION IMPLEMENTED | Live provider binding and empirical integrity thresholds remain not frozen |
| **Phase 2** | CORE ARCHITECTURE IMPLEMENTED | XAUUSD empirical regime thresholds remain not configured |
| **Phase 3A** | ARCHITECTURE IMPLEMENTED | Empirical calibration pending data |
| **Phase 3B** | IMPLEMENTED / RESEARCH ONLY | Production weight hard locked to 0.0 |
| **Phase 4** | COMPLETED & VERIFIED | Sealed dual-side candidate architecture |
| **Phase 5** | IMPLEMENTED & VERIFIED | Revision 2 + 2.1 implemented, fully tested, and passing all gates |
| **Phase 6** | NOT STARTED FOR XAUUSD | PIT backtest + walk-forward + ablation required |
| **Phase 7** | PRODUCT COMPLETION PAUSED | XAUUSD presentation/live-monitor adaptation pending |
| **Phase 8** | HOLD — TARGET SPECIFICATION | Live paper observation only after Phase 6/7 dependencies |
| **Phase 9** | HOLD — TARGET SPECIFICATION | ML meta-filter only after deterministic baseline is empirically validated |

*The Phase 5 status must only be changed to IMPLEMENTED & VERIFIED after the actual Phase 5 branch has passed required tests, human code review, and the agreed merge gate.*

---

# 1. Non-Negotiable Global Rules

| ID | Rule | Mandatory Behavior |
| :--- | :--- | :--- |
| **R1** | No trading execution | No broker/exchange order, modify-order, cancel-order, leverage, withdrawal, or testnet trading path |
| **R2** | One engine | Live analysis, backtest replay, and paper observation call the same pure-Python decision/risk engines |
| **R3** | Point-in-time correctness | At timestamp T, no feature, structure, cycle, label, target, or score may use information after T |
| **R4** | Closed-candle decisions | Direction/timing decisions use closed 15m/1H/4H/1D data only |
| **R5** | Lower-TF isolation | 1m/5m data is execution/intrabar evidence only and never substitutes Phase 4 scoring evidence |
| **R6** | Immutable provenance | Signals, risk plans, execution simulations and research artifacts are immutable/versioned |
| **R7** | Abstention is valid | WAIT, NO_TRADE, CONFLICT, calibration-required and invalid-risk states are first-class outcomes |
| **R8** | No legacy threshold inheritance | XAUUSD never silently inherits XAUT numerical thresholds |
| **R9** | Engine purity | `engine/` has zero Django ORM, Celery, Redis, Channels or network dependencies |
| **R10** | Canonical target | Operational instrument must normalize to XAUUSD; generic GOLD/XAU labels do not silently map to it |
| **R11** | Dual-side independence | SHORT is not implemented as a sign-negated LONG shortcut |
| **R12** | Candidate/publication separation | Phase 4/5 candidate actions may be BUY/SELL; publication remains WAIT until Phase 6 authority is explicitly frozen |
| **R13** | Phase 3B lock | Spectral research production weight remains 0.0 |
| **R14** | Decimal risk math | XAUUSD ATR, prices, stops, targets and RR use Decimal in Phase 5 |
| **R15** | Structural TP1 only | Phase 5 must never fabricate TP1 from a minimum-RR formula |
| **R16** | Conservative RR | LONG RR uses `entry_max`; SHORT RR uses `entry_min` |
| **R17** | Deduplicated friction | Actual bid/ask spread is never added again synthetically |
| **R18** | Adverse slippage | LONG entry worsens upward; SHORT entry worsens downward |
| **R19** | Strict evidence integrity | Invalid quote/OHLC/timezone evidence cannot create fills or trusted replay |
| **R20** | No fake evidence | Invalid risk plans and NO_FILL outcomes use None, not invented zero-priced evidence |
| **R21** | Canonical fingerprints | Authoritative inputs are serialized deterministically and SHA-256 fingerprinted |
| **R22** | Human decision support | UI/API may explain candidates but cannot place trades |
| **R23** | Tests before completion | A phase is incomplete until its required tests and regression gates pass |
| **R24** | No Phase skipping | Phase 6 empirical validation precedes production authority, Phase 8 paper observation and Phase 9 ML promotion |
| **R25** | Historical XAUT preservation | Frozen XAUT tests/data remain for audit continuity and must not become active XAUUSD behavior |

---

# 2. Product Definition

## 2.1 Primary User Question
For XAUUSD right now:
- What is the LONG directional/timing evidence?
- What is the SHORT directional/timing evidence?
- Is either side a deterministic candidate?
- Are there hard safety blockers?
- If a candidate exists, is its structural risk plan valid?
- What entry zone, stop, TP1, optional TP2 and conservative RR apply?
- What data/calibration limitations prevent publication or promotion?
- Why did the engine choose BUY candidate, SELL candidate, CONFLICT or WAIT?

## 2.2 Primary Outputs
- Long Direction Score / Short Direction Score
- Long Timing Score / Short Timing Score
- Market Regime
- Structure / BOS / Support / Resistance
- Candidate State & Candidate User Decision
- Published State & Published User Decision
- Hard Gate Reasons
- Entry Zone (`entry_min`, `entry_max`, `entry_mid`)
- Stop Structure, Stop ATR, Stop Final, Stop Distance ATR
- TP1, TP2, Planned RR TP1 / TP2
- Risk Candidate Status
- Simulation Eligibility
- Data Quality / Feed Health
- Calibration Status
- Analysis / Policy / Risk / Execution Fingerprints
- Human-readable positive and negative reasons

## 2.3 Active User-Decision Vocabulary
For XAUUSD:
- `BUY`
- `WAIT`
- `SELL`

*`AVOID` is historical XAUT compatibility only. New XAUUSD publication logic must not depend on `AVOID`.*

## 2.4 Explicit Non-Goals
- Auto-trading
- Position sizing
- Leverage
- Portfolio allocation
- Guaranteed win-rate claims
- LLM-originated direction
- High-frequency 1m signal generation
- TradingView scraping as a calculation source
- Phase 3B production scoring before promotion governance
- Phase 9 ML signal origination

---

# 3. Logical System Architecture

```text
READ-ONLY XAUUSD / MACRO / OPTIONAL PROXY SOURCES
                    |
                    v
         INGESTION + VALIDATION
                    |
                    v
               POSTGRESQL
                    |
          CLOSED-CANDLE WINDOWS
                    |
                    v
     FEATURES -> REGIME -> STRUCTURE
                    |
          PHASE 3A / PHASE 3B
                    |
                    v
       PHASE 4 DUAL-SIDE SIGNAL
          |                 |
          | candidate       | publication guard
          v                 v
 BUY/SELL/WAIT candidate    WAIT
          |
          v
 PHASE 5 SIDE-AWARE RISK PLAN
          |
          +--> invalid -> candidate_effective_action=WAIT
          |
          +--> valid candidate BUY/SELL
                    |
                    v
          publication still WAIT
                    |
                    v
 PHASE 6 PIT BACKTEST / WALK-FORWARD / ABLATION
                    |
                    v
       future calibration/promotion governance
                    |
       +------------+-------------+
       |                          |
       v                          v
 Phase 7 UI/Monitor         Phase 8 Paper Audit
                                  |
                                  v
                          Phase 9 ML Meta-Filter
```

*No arrow in this diagram represents automated order placement.*

---

# 4. Deployment and Framework Boundary

```text
Browser
  |
Nginx
  |
Gunicorn / Django
  |---------------- PostgreSQL
  |
Redis <------------ Celery workers / Celery Beat
  |
  +-- market_data
  +-- analysis
  +-- backtest
  +-- machine_learning
  +-- maintenance
```

- **Django** owns persistence, authentication, orchestration, APIs, and presentation.
- **`engine/`** owns deterministic mathematics and domain logic with zero database or framework imports.

---

# 5. Repository / Module Architecture

Target structure:
```text
AurumIQ/
  engine/
    core/
      types.py
      interfaces.py
    indicators/
    regime/
    structure/
    cycles/
    signals/
      xauusd_*.py
    risk/
      planner.py                 # historical XAUT frozen
      stops.py                   # historical XAUT frozen
      targets.py                 # historical XAUT frozen
      execution.py               # historical XAUT frozen
      intrabar.py                # historical XAUT frozen
      xauusd_policy.py
      xauusd_fingerprints.py
      xauusd_stops.py
      xauusd_targets.py
      xauusd_planner.py
      xauusd_execution.py
      xauusd_intrabar.py
    backtest/
    ml/

  apps/
    accounts/
    instruments/
    market_data/
    signals/
    backtests/
    live_monitor/
    alerts/
    dashboard/
    audit/
    system_health/

  docs/
    phases/
```

*Frozen historical modules must not be modified merely to add XAUUSD symmetry.*

---

# 6. Canonical Instrument and Market Data Model

## 6.1 Canonical Target
- **Display:** `XAU/USD`
- **Canonical Identifier:** `XAUUSD`
- **Asset Class:** Commodity / Spot Gold
- **Quote Currency:** `USD`

Do not treat these as canonical XAUUSD aliases:
`GOLD`, `XAU`, `GOLD_REFERENCE`, `XAUT`, `XAUTUSD`, `XAUTUSDT`.

## 6.2 Historical Baseline
`XAUT` remains permitted only for:
- Frozen historical market stores
- Historical USDT/USD normalization regression
- Historical XAUT long-only signal/risk tests
- Audit comparison
- Legacy documentation clearly labeled as frozen

## 6.3 XAUUSD Listing Roles
The active architecture distinguishes:
- `PRIMARY_XAUUSD_SPOT`
- `SECONDARY_XAUUSD_SPOT`
- `LEGACY_GOLD_REFERENCE`
- `LEGACY_EXECUTION`

Provider resolution must be strictly deterministic.

---

# 7. Phase 1 — XAUUSD Ingestion Engine

## 7.1 Primary/Secondary Providers
- Use independent read-only XAUUSD spot providers.
- Default provider binding may remain `NOT_CONFIGURED` until authorized endpoints/credentials are supplied.
- Failure to configure the primary provider must fail closed.

## 7.2 Direct USD Normalization
XAUUSD native USD data uses:
$$\text{normalization} = \text{DIRECT\_USD}, \quad \text{quote\_rate} = 1, \quad \text{close\_usd} = \text{close}$$
USDT/USD normalization is not an active dependency.

## 7.3 Multi-Source Integrity
- Primary and secondary closed candles are aligned by timestamp.
- Source disagreement may flag data quality but must not create directional alpha by averaging prices.
- Thresholds are explicit XAUUSD configuration and default to `None` until empirically frozen.

## 7.4 Provider Health
- Required health states include an explicit `NOT_CONFIGURED`.
- Data-quality hard failure blocks candidate analysis/publication.

---

# 8. Volume Semantics

Spot gold has no single centralized exchange-wide volume. Use explicit evidence types:
- `REAL_VOLUME`
- `TICK_VOLUME`
- `PROXY_VOLUME`
- `UNAVAILABLE`

**Rules:**
1. Never fabricate missing volume.
2. Do not mix incompatible volume semantics in one rolling feature.
3. Missing/unusable volume contributes zero positive score.
4. GC futures volume may be a labeled optional proxy only when PIT-safe.
5. Proxy volume never overwrites spot XAUUSD candle data.

---

# 9. Timeframes and Closed-Candle Contract

Operational scoring timeframes:
- **15m:** Primary timing/candidate resolution
- **1H:** Momentum turn / confirmation
- **4H:** Macro structure/trend
- **1D:** Daily trend

`1m` and `5m` are strictly reserved for:
- Causal execution simulation
- Limit-touch evidence
- Intrabar ambiguity replay

*Missing 1H evidence must not be replaced by 15m evidence. Unclosed candles cannot become authoritative decision evidence.*

---

# 10. Phase 2 — Indicators, Regime and Structure

- Indicators remain pure and causal.
- Regime outputs: `BULL_TREND`, `BEAR_TREND`, `RANGE`, `HIGH_VOLATILITY`, `TRANSITION`, `UNKNOWN`.
- For XAUUSD, empirical regime thresholds remain: `NOT_CONFIGURED`, `NOT_FROZEN`, `REVALIDATION_REQUIRED`.
- An uncalibrated profile must return fail-neutral/unknown classifications rather than silently inheriting historical XAUT thresholds.
- Structure must preserve: confirmed swing high/low, HH / HL / LH / LL, BOS, support/resistance zones, `detected_at` / PIT knowledge time, `StructureResult.timestamp`, zone `created_at`, active/inactive state, touch count.
- Zero centered-fractal look-ahead.

---

# 11. Phase 3A — Robust Time Cycle

Implemented architecture computes deterministic descriptive facts before empirical calibration:
- DST-aware session classification
- Session progress
- Swing market age & swing known age
- Calendar context & macro event proximity
- Macro blackout
- Sample-quality metadata

*XAUUSD empirical scoring/calibration status remains `PENDING_DATA` until supported by actual datasets. Candidate artifacts do not automatically become production-frozen scoring profiles. Zero legacy XAUT numerical fallback.*

---

# 12. Phase 3B — Experimental Spectral Research

- Research components: ACF, FFT, Wavelet, Hilbert phase, multi-method reliability.
- **Governance:** `production_weight = 0.0`.
- XAUUSD detection/reliability/promotion thresholds remain `None` until configured and validated.
- Phase 3B may produce descriptive research outputs, but cannot originate or amplify a production BUY/SELL decision while locked.
- *The old statement that "Phase 4+ is NOT STARTED" is obsolete and removed from current documentation.*

---

# 13. Phase 4 — Dual-Side Direction, Timing and State Machine

## 13.1 Independent Side Scores
$$\text{LongDirectionScore}, \quad \text{ShortDirectionScore}$$
$$\text{LongTimingScore}, \quad \text{ShortTimingScore}$$
SHORT must be independently reasoned; do not negate LONG.

## 13.2 Candidate State Machine
XAUUSD candidate states:
`NO_TRADE`, `WATCH_LONG`, `READY_LONG`, `BUY_WINDOW`, `WATCH_SHORT`, `READY_SHORT`, `SELL_WINDOW`, `CONFLICT`, `FORCE_WAIT`.

Candidate decisions: `BUY`, `SELL`, `WAIT`.

## 13.3 Two-Layer Authority
- **Layer A (Candidate Mechanics):** May produce `BUY_WINDOW / BUY`, `SELL_WINDOW / SELL`, or `WAIT`.
- **Layer B (Publication Guard):** Until empirical promotion is explicitly authorized:
  $$\text{published state} = \text{NO\_TRADE}, \quad \text{published user\_decision} = \text{WAIT}$$
  Candidate state/decision remain preserved for audit. No test-mode bypass may silently enable production publication.

## 13.4 Conflict Resolution
If both sides qualify incompatibly, resolve deterministically to:
$$\text{CONFLICT} \longrightarrow \text{WAIT}$$

---

# 14. Hard Safety Gates

Hard gates strictly dominate numeric scores:
- Unclosed candle
- Stale critical feed
- Provider transition
- Missing critical XAUUSD feed
- Macro blackout
- Malformed authoritative evidence
- Calibration governance failure where required

*Hard-gated decisions must never be rescued by high Direction/Timing scores.*

---

# 15. Phase 5 — Side-Aware Risk Planning

**Current governance:** Implementation contract approved, implemented, and verified on `feat/xauusd-phase5-side-aware-risk`.

## 15.1 Source Eligibility
- **LONG risk planning requires both:** `candidate_state == BUY_WINDOW` and `candidate_user_decision == BUY`.
- **SHORT risk planning requires both:** `candidate_state == SELL_WINDOW` and `candidate_user_decision == SELL`.

Phase 5 may demote but never promote:
- $\text{BUY} \longrightarrow \text{BUY}$ or $\text{WAIT}$
- $\text{SELL} \longrightarrow \text{SELL}$ or $\text{WAIT}$
- $\text{WAIT} \longrightarrow \text{WAIT}$

**Forbidden:** $\text{WAIT} \to \text{BUY}$, $\text{WAIT} \to \text{SELL}$, $\text{BUY} \to \text{SELL}$, $\text{SELL} \to \text{BUY}$. Published action remains `WAIT`.

## 15.2 Authoritative Time
$$T = \text{phase4\_snapshot.timestamp}$$
No independent override. Structure and zones are eligible only if:
$$\text{StructureResult.timestamp} \le T, \quad \text{zone.created\_at} \le T, \quad \text{zone.is\_active} == \text{True}$$

---

# 16. Deterministic Entry Selection

## 16.1 LONG
Use active `SUPPORT` zones from 15m.
- Select sorting: `price_high DESC`, `created_at ASC`, `price_low ASC`, `zone_fingerprint ASC`.
- Coordinates:
  $$\text{entry\_min} = \text{support.price\_low}, \quad \text{entry\_max} = \text{support.price\_high}, \quad \text{entry\_mid} = \frac{\text{entry\_min} + \text{entry\_max}}{2}$$

## 16.2 SHORT
Use active `RESISTANCE` zones from 15m.
- Select sorting: `price_low ASC`, `created_at ASC`, `price_high DESC`, `zone_fingerprint ASC`.
- Coordinates:
  $$\text{entry\_min} = \text{resistance.price\_low}, \quad \text{entry\_max} = \text{resistance.price\_high}, \quad \text{entry\_mid} = \frac{\text{entry\_min} + \text{entry\_max}}{2}$$

*Input collection order must not affect the result.*

---

# 17. Stop Mathematics

All risk mathematics strictly uses `Decimal`.

## 17.1 LONG
$$\text{stop\_structure} = \text{support.price\_low} - \text{structure\_buffer}$$
$$\text{stop\_atr} = \text{entry\_mid} - (\text{atr\_multiplier} \times \text{atr14})$$
$$\text{stop\_final} = \min(\text{stop\_structure}, \text{stop\_atr})$$
$$\text{planned\_risk} = \text{entry\_max} - \text{stop\_final}$$
$$\text{stop\_distance\_atr} = \frac{\text{planned\_risk}}{\text{atr14}}$$

**Require:** $\text{atr14} > 0$, $\text{stop\_final} < \text{entry\_min}$, $\text{planned\_risk} > 0$, $\text{stop\_distance\_atr} \le \text{max\_stop\_distance\_atr}$.

## 17.2 SHORT
$$\text{stop\_structure} = \text{resistance.price\_high} + \text{structure\_buffer}$$
$$\text{stop\_atr} = \text{entry\_mid} + (\text{atr\_multiplier} \times \text{atr14})$$
$$\text{stop\_final} = \max(\text{stop\_structure}, \text{stop\_atr})$$
$$\text{planned\_risk} = \text{stop\_final} - \text{entry\_min}$$
$$\text{stop\_distance\_atr} = \frac{\text{planned\_risk}}{\text{atr14}}$$

**Require:** $\text{atr14} > 0$, $\text{stop\_final} > \text{entry\_max}$, $\text{planned\_risk} > 0$, $\text{stop\_distance\_atr} \le \text{max\_stop\_distance\_atr}$. Boundary equality is valid.

---

# 18. Target Selection and Reward/Risk

## 18.1 TP1 is Structural Only
- **LONG TP1:** Nearest valid `RESISTANCE` where $\text{price\_low} > \text{entry\_max}$.
- **SHORT TP1:** Nearest valid `SUPPORT` where $\text{price\_high} < \text{entry\_min}$.
- If no structural TP1 exists:
  $$\text{INVALID\_RISK\_CANDIDATE}, \quad \text{candidate\_effective\_action} = \text{WAIT}$$
  Never fabricate TP1 from $\text{min\_rr} \times \text{risk}$. Never skip the nearest structural TP1 merely to improve RR.

## 18.2 Target Evidence Deduplication
15m and 4H target evidence may overlap. After PIT filtering, deduplicate by canonical `zone_fingerprint` before deterministic sorting.

## 18.3 Conservative TP1 RR
- **LONG:**
  $$\text{planned\_rr\_tp1} = \frac{\text{tp1} - \text{entry\_max}}{\text{entry\_max} - \text{stop\_final}}$$
- **SHORT:**
  $$\text{planned\_rr\_tp1} = \frac{\text{entry\_min} - \text{tp1}}{\text{stop\_final} - \text{entry\_min}}$$
- **Gate:** Require $\text{planned\_rr\_tp1} \ge \text{min\_rr\_tp1}$. Equality is valid.

## 18.4 Optional TP2
- **LONG structural TP2:** First later deterministic resistance whose $\text{price\_low} > \text{tp1}$.
- **SHORT structural TP2:** First later deterministic support whose $\text{price\_high} < \text{tp1}$.
- Equal-price targets are skipped.
- If no structural TP2 exists and `tp2_atr_multiplier` is configured, use an ATR-derived candidate only if it lies strictly beyond TP1.
- *TP2 cannot rescue invalid TP1.*

---

# 19. Phase 5 Policy Governance

- `XauUsdRiskProfile` is XAUUSD-specific.
- Uncalibrated production profile defaults empirical numerics to `None` (structure buffer, ATR multiplier, max stop distance ATR, minimum TP1 RR, optional TP2 multiplier, execution latency, synthetic spread, slippage).
- Acceptance/unit tests may use clearly labeled `TEST_ONLY` configured policies.
- Production authorization remains `False` pending later governance.

---

# 20. Canonical Provenance and Fingerprints

## 20.1 UTC Serialization
Every authoritative timestamp must:
- Be timezone aware with non-None `utcoffset()`
- Convert to UTC preserving microseconds
- Serialize as canonical ISO-8601 with trailing `Z` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`)

## 20.2 StructureZone Fingerprint
Binds all authoritative fields:
`zone_type`, `price_low`, `price_high`, `created_at`, `touches`, `is_active`.  
Canonical sorted compact JSON $\to$ SHA-256.

## 20.3 Quote Evidence Fingerprint
Binds: `evidence_type=QUOTE`, `timestamp`, `bid`, `ask`, `source`.

## 20.4 Candle Evidence Fingerprint
Binds complete canonical `CandleData` evidence:
`evidence_type=CANDLE`, `timestamp_open`, `timestamp_close`, `open`, `high`, `low`, `close`, `volume`, `is_closed`, `source_id`, `quote_rate`, `close_usd`, `volume_evidence`.

## 20.5 Risk Plan Fingerprint
Binds: Phase 4 fingerprint, candidate state/decision, side, authoritative $T$, Decimal ATR, entries, stops, stop distance, TP1/TP2, planned RR, zone fingerprints, policy fingerprint, risk version, and caller-injected code revision.

## 20.6 No False Code Revision
The Phase 4 baseline SHA must never be hardcoded as the producing Phase 5 code revision. `code_revision` is required caller-injected provenance.

---

# 21. Execution Simulation

## 21.1 Quote Integrity
Valid quote requires: aware timestamp, finite `bid` / `ask`, $\text{bid} > 0$, $\text{ask} > 0$, $\text{bid} \le \text{ask}$. Sort valid eligible quotes chronologically.

## 21.2 Candle Integrity
Require:
- `timestamp_open` and `timestamp_close` aware, with $\text{timestamp\_close} > \text{timestamp\_open}$.
- Open, High, Low, Close Decimal, finite, $> 0$. Volume Decimal, finite, $\ge 0$.
- Geometric invariants: $\text{low} \le \text{high}$, $\text{high} \ge \text{open}$, $\text{high} \ge \text{close}$, $\text{low} \le \text{open}$, $\text{low} \le \text{close}$.
- Malformed candle evidence is never repaired silently.

## 21.3 MARKET_AFTER_SIGNAL
Earliest executable time: $\text{signal\_generated\_at} + \text{latency}$.
- **LONG:** $\text{raw} = \text{ASK}, \quad \text{observed\_spread} = \text{ASK} - \text{BID}, \quad \text{synthetic\_spread} = 0, \quad \text{fill} = \text{ASK} + \text{adverse\_slippage}$.
- **SHORT:** $\text{raw} = \text{BID}, \quad \text{observed\_spread} = \text{ASK} - \text{BID}, \quad \text{synthetic\_spread} = 0, \quad \text{fill} = \text{BID} - \text{adverse\_slippage}$.
- Observed spread is informational; do not add it again synthetically.

## 21.4 NEXT_BAR_OPEN
Use first valid bar with $\text{timestamp\_open} \ge \text{earliest\_exec\_ts}$. Synthetic spread and slippage are applied exactly once to `bar.open`.

## 21.5 LIMIT_ZONE
- **LONG quote trigger:** $\text{ASK} \le \text{limit}$, $\text{fill} \le \text{limit}$.
- **SHORT quote trigger:** $\text{BID} \ge \text{limit}$, $\text{fill} \ge \text{limit}$.
- **Candle mode:** LONG $\to \text{low} \le \text{limit}$, SHORT $\to \text{high} \ge \text{limit}$.
- Pre-activation touches are ignored. Mid-bar activation without intrabar evidence $\to \text{NO\_FILL}$.

## 21.6 NO_FILL
$$\text{is\_filled} = \text{False}, \quad \text{raw\_executable\_price} = \text{None}, \quad \text{fill\_price} = \text{None}, \quad \text{source\_evidence\_fingerprint} = \text{None}$$
*Do not fabricate zero-priced evidence.*

---

# 22. Side-Aware Intrabar Resolution

- **LONG:** TP hit $= \text{high} \ge \text{TP}$, SL hit $= \text{low} \le \text{SL}$.
- **SHORT:** TP hit $= \text{low} \le \text{TP}$, SL hit $= \text{high} \ge \text{SL}$.
- If both occur in one bar $\to$ ambiguous.

**Hierarchy:**
1. 4H / 1H parent $\to$ validate and replay 15m $\to$ ambiguous 15m child $\to$ 1m preferred $\to$ 5m fallback.
2. 15m parent $\to$ 1m preferred $\to$ 5m fallback.
3. Malformed lower-TF evidence $\to \text{CONSERVATIVE\_SL\_FIRST}$.
4. Malformed parent $\to$ fail closed / unresolved.

**WORST_CASE:**
- **LONG:** $\text{stop} - \text{adverse\_gap}$
- **SHORT:** $\text{stop} + \text{adverse\_gap}$

---

# 23. Phase 6 — ONE Canonical Backtesting and Validation Phase

There must not be two independent active "Phase 6" specifications with overlapping ownership.
- **Canonical active document:** `PHASE_6_BACKTEST_VALIDATION.md` (owns both Phase 6A and 6B).
- The older `PHASE_6_BACKTESTING_ABLATION.md` is superseded and preserved under historical appendices.

## 23.1 Phase 6A — PIT Replay and Walk-Forward Validation
Evaluate multi-year XAUUSD data in three dimensions:
1. LONG / BUY replay
2. SHORT / SELL replay
3. Combined side-aware reporting

**Requirements:** Exact same Phase 4 and Phase 5 pure engines, point-in-time feature reconstruction, strict post-signal execution timing, side-aware cost/friction, chronological folds, dependency purging, embargo, zero OOS access during candidate selection, immutable run fingerprints, normalized R metrics, zero account sizing.

## 23.2 Phase 6B — Ablation and Calibration Evidence
Ablate: regime, structure/BOS, multi-timeframe trend, Phase 3A session, swing duration, macro blackout, optional Phase 3B research features, later optional ML components.
*Ablation must never mutate the baseline. No feature is promoted merely because it improves in-sample win rate.*

## 23.3 Required XAUUSD Phase 6 Contracts
- `XAU-P6-01`: LONG PIT replay
- `XAU-P6-02`: SHORT PIT replay
- `XAU-P6-03`: Combined parity/reporting

*Phase 6 produces evidence; it does not place orders.*

---

# 24. Backtest Metrics

Minimum reporting:
Candidate count by side, eligible risk plans, fill rate, no-fill rate, resolved trades, trades/month, win rate, average win/loss R, expectancy R, profit factor, maximum drawdown in R, drawdown duration, consecutive losses, MFE / MAE, cost drag, performance by regime, session, side, and cycle/research bucket where valid.

*Do not interpret a tiny sample as statistical proof.*

---

# 25. Signal Outcome / Triple Barrier

- **LONG:** Profit barrier $= \text{TP}$, Loss barrier $= \text{stop below entry}$, Time barrier $= \text{Phase 6 validated horizon}$.
- **SHORT:** Profit barrier $= \text{TP below entry}$, Loss barrier $= \text{stop above entry}$, Time barrier $= \text{Phase 6 validated horizon}$.
- **Outcomes:** `TP_FIRST`, `SL_FIRST`, `TIMEOUT`, `NO_FILL`, `UNRESOLVED` where explicitly supported.
- Store MFE/MAE and exact causal timestamps.

---

# 26. Phase 7 — XAUUSD Dashboard, LiveMonitor and Alerts

Historical XAUT dashboard implementation is reusable UI infrastructure, not current data semantics.

**XAUUSD adaptation displays:**
- Live XAUUSD quote/freshness
- LONG direction/timing & SHORT direction/timing
- Candidate state/decision & Published state/decision
- Active support/resistance & Side-aware risk plan
- Data-quality/feed-health reasons & Calibration status
- Phase 3B research status separately from production scores
- Last authoritative analysis timestamp

Live Redis keys and examples must use XAUUSD (`livequote:XAUUSD`, never `livequote:XAUTUSDT`). Alerts are informational only (`WATCH_LONG_CREATED`, `READY_LONG`, `BUY_WINDOW_CANDIDATE`, `WATCH_SHORT_CREATED`, `READY_SHORT`, `SELL_WINDOW_CANDIDATE`, `CONFLICT`, `ENTRY_ZONE_REACHED`, `INVALIDATION_TOUCHED`, `LIVE_DATA_STALE`, `PROVIDER_UNHEALTHY`, `MACRO_BLACKOUT`).

*No alert implies automatic execution.*

---

# 27. API Blueprint

Suggested active API contract:
```http
GET /api/v1/analysis/current/
GET /api/v1/signals/current/
GET /api/v1/signals/history/
GET /api/v1/risk/current/
GET /api/v1/cycles/current/
GET /api/v1/market/candles/
GET /api/v1/backtests/
POST /api/v1/backtests/
GET /api/v1/system/health/
GET /api/v1/config/active/
```

Example current XAUUSD response:
```json
{
  "instrument": "XAUUSD",
  "timestamp": "2026-09-01T00:00:00.000000Z",
  "published": {
    "state": "NO_TRADE",
    "decision": "WAIT"
  },
  "candidate": {
    "state": "BUY_WINDOW",
    "decision": "BUY"
  },
  "long": {
    "direction_score": null,
    "timing_score": null
  },
  "short": {
    "direction_score": null,
    "timing_score": null
  },
  "risk": {
    "side": "LONG",
    "status": "INVALID_RISK_CANDIDATE",
    "entry_min": null,
    "entry_max": null,
    "stop_final": null,
    "tp1": null,
    "tp2": null,
    "planned_rr_tp1": null
  },
  "calibration_status": "PENDING_PHASE6",
  "production_authorized": false
}
```
*`null` is always preferable to invented evidence.*

---

# 28. Phase 8 — Live Paper Observation

Phase 8 is not real trading. It observes production-like read-only feeds and records:
- LONG candidates & SHORT candidates
- WAIT/conflict decisions
- Side-aware simulated fills
- Side-aware TP/SL/time outcomes
- Feed continuity & errors
- Parity against identical-window Phase 6 replay

*The 14-day continuity concept is an infrastructure stability gate only and is not statistical proof of profitability. Phase 8 must not start before Phase 6/7 dependencies are explicitly approved.*

---

# 29. Phase 9 — ML Meta-Filter

ML never originates a trade. Pipeline:
```text
DETERMINISTIC XAUUSD CANDIDATE
          |
          v
      ML META-FILTER
     ACCEPT / REJECT
          |
          v
     deterministic risk gate
          |
          v
      candidate / WAIT
```
- Features must be PIT-safe with side/direction explicitly included.
- Research model order: Logistic Regression $\to$ XGBoost $\to$ LightGBM.
- Keep the simplest robust OOS winner.
- Probability must be calibrated separately from rule-quality scores.
- ML promotion never bypasses Phase 4/5 safety or publication authority.

---

# 30. Security

- Public/read-only market data permissions only
- No trade/withdraw keys
- Secrets strictly via environment (zero secrets in Git)
- CSRF protection, HTTPS / secure cookies, restricted admin
- Append-only audit trail with protected audit foreign keys (no hard deletion of historical audit evidence)
- At least one effective active admin invariant

---

# 31. Observability

Health checks report separately:
- Primary XAUUSD feed / Secondary XAUUSD feed
- Macro blackout feed / Optional volume/proxy feed
- PostgreSQL / Redis / Celery queues / Scheduler
- Last closed-candle analysis
- Current Phase 4 candidate authority / Phase 5 availability
- Calibration status

Structured log example:
```json
{
  "event": "signal_generated",
  "instrument": "XAUUSD",
  "candidate_state": "BUY_WINDOW",
  "candidate_decision": "BUY",
  "published_state": "NO_TRADE",
  "published_decision": "WAIT",
  "analysis_fp": "...",
  "policy_fp": "...",
  "code_revision": "..."
}
```

---

# 32. Celery / Scheduling

Queues: `market_data`, `analysis`, `backtest`, `machine_learning`, `maintenance`.

Core tasks:
- Ingest XAUUSD market data
- Validate closed candles/provider health
- Analyze closed candle & persist immutable signal snapshot
- Build side-aware risk plan & update research outcomes
- Run Phase 6 backtests & build walk-forward report
- Run ablation
- Train ML only after explicit authorization
- System-health maintenance (all tasks must be idempotent)

---

# 33. Testing Strategy

## 33.1 Causality Tests
Mutation of data after $T$ must not alter analysis at $T$.

## 33.2 Side Symmetry Without Shortcut
Test LONG and SHORT independently.

## 33.3 XAUUSD Acceptance Contracts
Keep existing approved contracts:
`XAU-P1-01`, `XAU-P1-02`, `XAU-P2-01`, Phase 3A XAUUSD acceptance suite, Phase 3B XAUUSD acceptance suite, `XAU-P4-01..04`, `XAU-P5-01..03`, planned `XAU-P6-01..03`, planned Phase 7/8/9 contracts.

## 33.4 Phase 5 Hostile Suite
Approved implementation must execute matrix H1–H74, including:
- Deterministic zone/target tie handling
- Decimal ATR gates & RR boundaries
- Quote/candle validation
- Source evidence fingerprints & target deduplication
- TP2 strictly beyond TP1
- No WAIT promotion
- No fake invalid/no-fill evidence
- Malformed intrabar evidence fail-closed

## 33.5 Historical Regression
Historical XAUT suites remain green without changing their assertions merely to fit XAUUSD.

---

# 34. Configuration Governance

Do not keep the old master-blueprint "starting values" as active XAUUSD defaults.
The following remain unconfigured until supported by approved evidence:
- Direction thresholds & Timing thresholds
- XAUUSD regime thresholds
- Provider disagreement thresholds & Stale feed thresholds
- Phase 3A empirical weights & Phase 3B thresholds
- Structure buffer, ATR stop multiplier, max stop distance ATR, minimum RR, TP2 ATR multiplier
- Latency, spread, and slippage assumptions
- Phase 6 holding horizon & ML probability threshold

*Test fixtures may use explicit TEST_ONLY values.*

---

# 35. Phase Dependency Roadmap

```text
PHASE 0  Foundation
   |
PHASE 1  XAUUSD ingestion architecture
   |
PHASE 2  indicators/regime/structure
   |
PHASE 3A robust time-cycle architecture
   |
PHASE 3B research-only spectral layer (weight 0)
   |
PHASE 4  dual-side candidate engine          [VERIFIED]
   |
PHASE 5  side-aware risk/execution           [IMPLEMENTED & VERIFIED]
   |
PHASE 6  ONE canonical PIT backtest + walk-forward + ablation
   |
PHASE 7  XAUUSD dashboard/live monitor completion
   |
PHASE 8  live paper observation
   |
PHASE 9  ML meta-filter research
```

*Phase 6 is one phase, not two independent parallel Phase 6s.*

---

# 36. Documentation Synchronization Rule

Whenever a phase status changes:
- Update the phase file header
- Update `README.md`
- Update `SUMMARY.md`
- Update this master blueprint status matrix
- Update any cross-phase dependency statement
- Do not modify historical frozen sections unless correcting a factual archival error
- Document the producing commit/PR/test evidence

*A phase document must never say IMPLEMENTED & VERIFIED merely because a plan is approved.*

---

# 37. AI Coding Agent Master Contract

You are the principal Python/Django engineer for AurumIQ.

- **ACTIVE TARGET:** `XAU/USD`, canonical `XAUUSD`.
- **HISTORICAL BASELINE:** `XAUT` exists only as frozen audit/regression evidence. Do not reactivate `XAUTUSDT`, XAUT basis, USDT normalization, `XautSignalEngine`, AVOID-oriented, or long-only behavior in active XAUUSD code.
- **PRODUCT GOAL:** Build a deterministic, point-in-time, side-aware XAUUSD decision-support platform with independent LONG and SHORT candidate logic.
- **CURRENT DECISION AUTHORITY:** Candidate layer may resolve `BUY / SELL / WAIT`. Published production decision remains `WAIT` until empirical Phase 6 governance explicitly authorizes promotion.
- **NON-NEGOTIABLES:**
  - No order execution
  - One pure engine for live/backtest/paper
  - Closed-candle scoring only
  - 1m/5m execution/intrabar only
  - No legacy XAUT threshold inheritance
  - No fake evidence
  - Deterministic fingerprints
  - Immutable audit snapshots
  - Independent LONG/SHORT semantics
  - Phase 3B production weight 0
  - No Phase 6 promotion without verified PIT backtest evidence
  - No ML signal origination

*IMPLEMENT IN DEPENDENCY ORDER. A phase is complete only after required unit, hostile, acceptance, regression, Django, migration, and production-build gates pass.*

---

# 38. Final Engineering Principles

1. `XAUUSD` is the active signal instrument.
2. `XAUT` is historical evidence, not the live target.
3. `BUY` and `SELL` candidates are equally first-class.
4. `WAIT` is a correct output.
5. Candidate action is not production authority.
6. No structural evidence means no fabricated risk plan.
7. No structural TP1 means invalid risk candidate.
8. Risk mathematics must be conservative and reproducible.
9. Future data may never leak backward into a decision.
10. Research features must prove value out of sample.
11. Documentation status must follow verified code, not planned code.
12. Phase 6 is a single canonical empirical-validation phase.
13. No part of AurumIQ places real orders.

> **Implementation Target:** A user can open AurumIQ and understand XAUUSD LONG/SHORT evidence, candidate state, risk architecture, data quality, and reasons, while production authority remains safely governed and the system has zero capability to place a trade.
