# TRADING BOT V2: MACHINE LOGIC ARCHITECTURE

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       PRODUCTION TRADING BOT                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  TIER 1: DATA INGESTION (Real-time OHLCV + Microstructure)         │
│  ─────────────────────────────────────────────────────────────────  │
│  Exchange APIs (CCXT) → Data Normalizer → OHLCV Cache (Redis)      │
│                                                                      │
│  TIER 2: PATTERN RECOGNITION (Machine's Eyes)                       │
│  ─────────────────────────────────────────────────────────────────  │
│  ├─ Harmonic Detector (Gartley/Butterfly/Crab/Shark)               │
│  ├─ Fibonacci Ratio Validator (±2% tolerance)                       │
│  ├─ Stochastic Momentum Engine (%K, %D crossovers)                 │
│  ├─ Microstructure Scanner (FVG, Order Block, Liquidity Sweep)      │
│  ├─ Dow Theory Swing Validator (HH/HL, LL/LH)                      │
│  ├─ Asian Range Tracker (21:00-05:00 UTC)                          │
│  └─ Bollinger Squeeze Detector (volatility regime)                  │
│                                                                      │
│  TIER 3: CONFLUENCE SCORING (Machine's Brain)                       │
│  ─────────────────────────────────────────────────────────────────  │
│  ├─ Harmonic Confidence (0-1)                                       │
│  ├─ Stochastic Regime (-1 to +1)                                    │
│  ├─ Trend Alignment (-1 to +1)                                      │
│  ├─ FVG/OB Proximity (0-1)                                          │
│  ├─ Microstructure Quality (0-1)                                    │
│  └─ Bayesian Win Probability (0.5-0.95)                            │
│       → FINAL SIGNAL CONFIDENCE (0-100%)                            │
│                                                                      │
│  TIER 4: RISK MANAGEMENT (Machine's Safety)                         │
│  ─────────────────────────────────────────────────────────────────  │
│  ├─ Daily Loss Halt (-1% trigger)                                  │
│  ├─ Drawdown Circuit Breaker (-8% halt)                            │
│  ├─ Max Position Size (3% per trade)                               │
│  ├─ Confidence-Based Sizing (0.7-1.3x multiplier)                  │
│  ├─ R:R Validation (min 1.618:1)                                   │
│  └─ Exchange-Side SL/TP (fail-closed architecture)                 │
│                                                                      │
│  TIER 5: EXECUTION LAYER (Machine's Actions)                        │
│  ─────────────────────────────────────────────────────────────────  │
│  ├─ Order Placer (Entry + SL + TP triplet)                         │
│  ├─ Smart Executor (Limit → Market with slippage budget)           │
│  ├─ Position Tracker (in-memory + SQLite warehouse)                │
│  ├─ Trailing Stop Manager (peak-tracking + breakeven floor)        │
│  └─ Partial Exit Handler (50% at TP, 50% trailing)                │
│                                                                      │
│  TIER 6: ORCHESTRATION (State Machine)                              │
│  ─────────────────────────────────────────────────────────────────  │
│  IDLE → SCANNING → QUALIFYING → READY → EXECUTING → ACTIVE        │
│                          ↑                         ↓                │
│                          └─────────────────────────┘                │
│                        (SL/TP hit or exit condition)                │
│                                                                      │
│  TIER 7: MONITORING & OBSERVABILITY                                │
│  ─────────────────────────────────────────────────────────────────  │
│  ├─ Structured Logging (JSON + trace IDs)                          │
│  ├─ Real-time Metrics (Prometheus export)                          │
│  ├─ Live Dashboard (Streamlit)                                     │
│  ├─ Warehouse (SQLite append-only event log)                       │
│  └─ Alerting (Telegram, Email critical events)                     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Module Organization

```
trading-bot/
│
├── core/                                    # Machine logic
│   ├── patterns/
│   │   ├── __init__.py
│   │   ├── harmonic_detector.py            # Gartley/Butterfly/Crab/Shark (primary edge)
│   │   ├── fibonacci_calculator.py         # Fib ratios + PRZ calculation
│   │   ├── microstructure.py               # FVG, Order Block, Liquidity Sweep
│   │   └── pattern_validator.py            # Confidence scoring
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── technical_analyzer.py           # Main TA pipeline orchestrator
│   │   ├── stochastic_engine.py            # %K, %D, crossover detection
│   │   ├── swing_structure.py              # Dow Theory: HH/HL, LL/LH
│   │   ├── asian_range_tracker.py          # 21:00-05:00 UTC range
│   │   ├── bollinger_squeeze.py            # BB Width compression
│   │   ├── confluence_scorer.py            # Combine 6+ factors → final score
│   │   └── regime_classifier.py            # Bayesian: P(WIN | factors)
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_placer.py                 # Entry + SL + TP placement
│   │   ├── risk_engine.py                  # Position sizing, SL calculation
│   │   ├── position_tracker.py             # State + P&L tracking
│   │   ├── trailing_stop_manager.py        # Peak-tracking, breakeven
│   │   └── partial_exit_handler.py         # Split exits
│   │
│   ├── state_machine/
│   │   ├── __init__.py
│   │   ├── bot_state.py                    # Enum: IDLE, SCANNING, QUALIFYING, ...
│   │   ├── state_transitions.py            # Valid transitions + guards
│   │   ├── event_dispatcher.py             # Publish events to subscribers
│   │   └── bot_orchestrator.py             # State machine orchestrator
│   │
│   ├── tracing/
│   │   ├── __init__.py
│   │   ├── trace_context.py                # TraceContext: session_id, trade_id
│   │   ├── structured_logger.py            # JSON logging with trace IDs
│   │   └── trace_viewer.py                 # CLI tool to inspect traces
│   │
│   └── utils/
│       ├── __init__.py
│       ├── timeframe_utils.py              # 1m, 5m, 15m, 1h, 4h, 1d conversions
│       ├── price_utils.py                  # Pip/pct calculations
│       └── validation.py                   # Input validation
│
├── exchange/
│   ├── __init__.py
│   ├── ccxt_wrapper.py                     # Normalized exchange interface
│   ├── exchange_adapters/
│   │   ├── __init__.py
│   │   ├── binance_adapter.py
│   │   ├── bybit_adapter.py
│   │   └── bitget_adapter.py
│   └── order_book_parser.py                # Microstructure: bids/asks
│
├── data/
│   ├── __init__.py
│   ├── warehouse.py                        # SQLite append-only log
│   ├── ohlcv_cache.py                      # Redis in-memory cache
│   ├── market_state_tracker.py             # Current state snapshot
│   └── schema.sql                          # Warehouse schema
│
├── backtester/
│   ├── __init__.py
│   ├── historical_backtester.py            # Replay engine
│   ├── walk_forward_validator.py           # Out-of-sample validation
│   ├── monte_carlo_tester.py               # Parameter sensitivity
│   └── performance_metrics.py              # WR, PF, Sharpe, MDD
│
├── dashboard/
│   ├── __init__.py
│   ├── web_app.py                          # Streamlit interface
│   ├── metrics_aggregator.py               # Real-time metrics
│   └── alert_notifier.py                   # Telegram/Email
│
├── config/
│   ├── __init__.py
│   ├── base_config.py                      # Default settings
│   ├── coin_universe.py                    # Tradeable coins + weights
│   ├── pattern_params.py                   # Fib tolerances, thresholds
│   ├── risk_params.py                      # SL, TP, sizing rules
│   └── env_loader.py                       # .env → config
│
├── tests/
│   ├── unit/
│   │   ├── test_harmonic_detector.py
│   │   ├── test_fibonacci_calculator.py
│   │   ├── test_stochastic_engine.py
│   │   ├── test_confluence_scorer.py
│   │   ├── test_risk_engine.py
│   │   └── test_order_orchestrator.py
│   ├── integration/
│   │   ├── test_full_signal_generation.py
│   │   ├── test_order_lifecycle.py
│   │   └── test_end_to_end_backtest.py
│   └── fixtures/
│       ├── btc_4h_1year.csv
│       ├── eth_1h_6months.csv
│       ├── known_harmonic_patterns.json
│       └── market_state_samples.json
│
├── scripts/
│   ├── backtest_runner.py
│   ├── live_runner.py
│   ├── analyzer_cli.py
│   ├── db_inspector.py
│   └── promote_agent.py
│
├── main.py                                 # Entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── ARCHITECTURE_V2.md                      # This file
├── DEVELOPMENT.md                          # Dev guidelines
└── README.md
```

## Data Flow: The Complete Journey

```
EXCHANGE DATA STREAM
    │
    ├─ Fetch OHLCV (every 1 min or tick)
    │  ├─ Timeframes: 1h, 4h, 1d
    │  └─ Store in Redis + SQLite
    │
    ├─ TIER 2: PATTERN RECOGNITION
    │  ├─ Harmonic Detector
    │  │  └─ Scan 50 bars → Find swings → Match Fib ratios
    │  ├─ Stochastic Engine
    │  │  └─ Calculate %K, %D, detect crossovers
    │  ├─ Microstructure Scanner
    │  │  └─ Find FVG, OB, liquidity sweeps
    │  └─ Swing Validator (Dow Theory)
    │     └─ Identify HH/HL or LL/LH
    │
    ├─ TIER 3: CONFLUENCE SCORING
    │  ├─ Collect all pattern signals
    │  ├─ Combine into single confidence score
    │  └─ Apply Bayesian classifier: P(WIN | factors)
    │
    ├─ TIER 4: DECISION ENGINE
    │  ├─ Is confidence > 65%?
    │  ├─ Can I risk 3% and make 1.618× return?
    │  ├─ Is daily P&L > -1%?
    │  └─ → Move to READY state
    │
    ├─ TIER 5: EXECUTION
    │  ├─ Wait for price to touch entry level
    │  ├─ Place limit order (PRZ ± 10 pips)
    │  ├─ Place SL order (1 ATR beyond)
    │  ├─ Place TP order (1.618× risk)
    │  └─ → Move to ACTIVE state
    │
    ├─ TIER 6: MONITORING
    │  ├─ Every 10s: Check SL/TP, trailing stops
    │  ├─ Every 5m: Self-check (halts, risk limits)
    │  └─ Log all events to warehouse
    │
    └─ TIER 7: ANALYSIS & LEARNING
       ├─ Closed trades → Calculate WR, PF, Sharpe
       ├─ Update Bayesian priors (confluence factors)
       ├─ Update coin/timeframe rankings
       └─ Auto-promote shadow agents if passing gates
```

## Core Algorithms (Machine's Think Process)

### 1. Harmonic Pattern Detection

```python
def detect_harmonic_pattern(swings: List[Tuple], tolerance: float = 0.02) -> Optional[Pattern]:
    """
    Input: [(price, type, bar_index), ...] where type = "HIGH" | "LOW"
    Output: Pattern(type, PRZ, confidence, swings)
    
    Algorithm:
      1. Validate swing structure (must be X-A-B-C-D)
      2. Calculate all Fib ratios:
         - AB/XA ratio
         - BC/AB ratio
         - CD/BC ratio
      3. Match to template (Gartley vs Butterfly vs Crab vs Shark)
      4. Compute PRZ as cluster of D-point ratios
      5. Confidence = 1 / PRZ_width (narrower = higher confidence)
    """
    pass

def fibonacci_ratio(distance: float, ratio: float) -> float:
    """Compute Fibonacci target: distance × ratio"""
    return distance * ratio

def compute_prz(swings, pattern_type) -> Tuple[float, float]:
    """
    Calculate Potential Reversal Zone (PRZ)
    
    PRZ is where multiple D-point calculations cluster:
      - From AB/XA ratio
      - From CD/BC ratio
      - From ABC pattern
    
    Tight PRZ = high confidence (narrow zone)
    Loose PRZ = low confidence (wide zone)
    """
    pass
```

### 2. Stochastic Momentum with Regime Detection

```python
def calculate_stochastic(closes: List[float], lookback: int = 14) -> Tuple[float, float]:
    """
    %K = 100 × (Close - Low14) / (High14 - Low14)
    %D = SMA(%K, 3)
    
    States:
      - %K < 20, %D < 20: OVERSOLD (bullish setup)
      - %K > 80, %D > 80: OVERBOUGHT (bearish setup)
      - %K > %D (crossover): BULLISH momentum
      - %K < %D (crossover): BEARISH momentum
    """
    low_14 = min(closes[-lookback:])
    high_14 = max(closes[-lookback:])
    K = 100 * (closes[-1] - low_14) / (high_14 - low_14) if high_14 != low_14 else 50
    D = simple_moving_average([K] + k_history[-2:], 3)
    return K, D

def detect_stochastic_crossover(k_current, k_prev, d_current, d_prev) -> str:
    """Detect momentum regime change"""
    if k_prev < d_prev and k_current > d_current:
        return "BULLISH_CROSSOVER"
    elif k_prev > d_prev and k_current < d_current:
        return "BEARISH_CROSSOVER"
    return "NO_CROSSOVER"
```

### 3. Confluence Scoring

```python
def compute_confluence_score(factors: Dict[str, float]) -> float:
    """
    Combine 6+ independent factors into single confidence (0-1)
    
    Factors:
      1. harmonic_confidence (0-1) - weight 0.35
      2. stochastic_regime (-1 to +1) - weight 0.25
      3. trend_alignment (-1 to +1) - weight 0.20
      4. fvg_proximity (0-1) - weight 0.10
      5. ob_proximity (0-1) - weight 0.05
      6. asian_range_bias (0-1) - weight 0.05
    
    Score = weighted_sum(all factors)
    
    Historical calibration:
      - Score 0.90+ = 75% WR (highly confident)
      - Score 0.70-0.89 = 60% WR (moderate)
      - Score 0.50-0.69 = 52% WR (barely above 50%)
      - Score < 0.50 = skip (not worth the risk)
    """
    weights = {
        "harmonic": 0.35,
        "stochastic": 0.25,
        "trend": 0.20,
        "fvg": 0.10,
        "ob": 0.05,
        "asian_range": 0.05,
    }
    
    score = sum(factors.get(key, 0) * weight for key, weight in weights.items())
    return min(max(score, 0.0), 1.0)  # Clamp to [0, 1]

def bayesian_win_probability(score: float) -> float:
    """
    Map confluence score to P(WIN)
    
    Using historical calibration:
      score 0.90 → P(WIN) = 0.75
      score 0.70 → P(WIN) = 0.60
      score 0.50 → P(WIN) = 0.52
      score 0.00 → P(WIN) = 0.50
    
    Linear interpolation between calibration points.
    """
    calibration = [(0.0, 0.50), (0.50, 0.52), (0.70, 0.60), (0.90, 0.75)]
    return interpolate(score, calibration)
```

### 4. Position Sizing (Confidence-Based)

```python
def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    confidence: float,
    max_risk_pct: float = 3.0,
) -> float:
    """
    Position size = f(confidence)
    
    Risk per trade (fixed): account_balance × max_risk_pct / 100
    Stop loss distance: abs(entry_price - stop_loss_price)
    
    Base size = risk / stop_loss_distance
    
    Confidence multiplier:
      - confidence 0.90+ → 1.3× (high conviction)
      - confidence 0.70-0.89 → 1.0× (normal)
      - confidence 0.50-0.69 → 0.7× (low conviction)
      - confidence < 0.50 → skip (don't trade)
    
    Final size = base_size × confidence_multiplier
    """
    risk_per_trade = account_balance * max_risk_pct / 100
    sl_distance = abs(entry_price - stop_loss_price)
    base_size = risk_per_trade / sl_distance
    
    if confidence >= 0.90:
        multiplier = 1.3
    elif confidence >= 0.70:
        multiplier = 1.0
    elif confidence >= 0.50:
        multiplier = 0.7
    else:
        return 0.0  # Skip
    
    return base_size * multiplier
```

### 5. State Machine Orchestration

```python
class BotStateMachine:
    """
    IDLE (sleeping)
      ↓
    SCANNING (analyzing all coins)
      ↓ (pattern found)
    QUALIFYING (checking confluence)
      ↓ (confidence > 65%)
    READY (waiting for entry signal)
      ↓ (price touches entry level)
    EXECUTING (placing order)
      ↓ (order filled)
    ACTIVE (managing position)
      ↓ (SL/TP hit or exit condition)
    IDLE (start over)
    """
    
    states = ["IDLE", "SCANNING", "QUALIFYING", "READY", "EXECUTING", "ACTIVE"]
    
    def __init__(self):
        self.state = "IDLE"
        self.candidates = []  # Qualifying setups
        self.active_positions = []
    
    def tick(self):
        """One cycle: fetch data, scan, qualify, execute, monitor"""
        
        if self.state == "IDLE":
            self.state = "SCANNING"
        
        elif self.state == "SCANNING":
            self.candidates = self.scan_all_coins()
            if self.candidates:
                self.state = "QUALIFYING"
            # Else stay SCANNING
        
        elif self.state == "QUALIFYING":
            qualified = [c for c in self.candidates if c.confidence > 0.65]
            if qualified:
                self.candidates = qualified
                self.state = "READY"
            else:
                self.state = "IDLE"
        
        elif self.state == "READY":
            for candidate in self.candidates:
                if self.price_touches_entry(candidate):
                    self.state = "EXECUTING"
                    break
        
        elif self.state == "EXECUTING":
            self.place_triplet_order()  # entry + SL + TP
            self.state = "ACTIVE"
        
        elif self.state == "ACTIVE":
            self.monitor_positions()  # Check SL/TP, trailing stops
            for position in self.active_positions:
                if position.sl_hit or position.tp_hit:
                    self.close_position(position)
                    self.state = "IDLE"
                    break
    
    def scan_all_coins(self) -> List[PatternDetection]:
        """Scan BTC, ETH, SOL, ARB, DOGE for patterns"""
        candidates = []
        for coin in TRADEABLE_COINS:
            signals = self.technical_analyzer.analyze(coin)
            candidates.extend(signals)
        return sorted(candidates, key=lambda x: x.confidence, reverse=True)
```

## Execution: The Order Triplet

```python
class OrderTriplet:
    """Entry + SL + TP placed atomically"""
    
    def place(self, symbol, side, entry_price, stop_loss, take_profit, quantity):
        """
        Place 3 orders simultaneously:
        
        1. ENTRY: Limit order at entry_price
           - Timeout: 5 minutes (if not filled, cancel and re-evaluate)
        
        2. STOP_LOSS: Stop-loss order at stop_loss price
           - Type: STOP_MARKET (market order when SL touched)
           - FAIL_CLOSED: if placement fails, alert operator
        
        3. TAKE_PROFIT: Take-profit order at take_profit price
           - Type: LIMIT (limit order at TP level)
           - Partial: close 50% at TP, leave 50% for trailing
        
        Returns: (entry_order, sl_order, tp_order) or raises exception
        """
        
        entry_order = self.exchange.place_limit_order(
            symbol, side, entry_price, quantity,
            params={"clientOrderId": f"ENTRY_{uuid}"}
        )
        
        sl_order = self.exchange.place_stop_loss(
            symbol, stop_loss, quantity,
            params={"clientOrderId": f"SL_{uuid}"}
        )
        
        tp_order = self.exchange.place_take_profit(
            symbol, take_profit, quantity * 0.5,  # 50% at TP
            params={"clientOrderId": f"TP_{uuid}"}
        )
        
        return entry_order, sl_order, tp_order
```

## Risk Management: The Safety Net

```python
class RiskEngine:
    """Multi-layer risk protection"""
    
    def __init__(self):
        self.daily_pnl = 0.0
        self.peak_equity = 10000.0
        self.current_equity = 10000.0
        self.active_positions = []
    
    def check_daily_loss_halt(self) -> bool:
        """Halt if daily loss > 1%"""
        daily_loss_pct = self.daily_pnl / self.current_equity
        return daily_loss_pct < -0.01  # Halt if true
    
    def check_drawdown_halt(self) -> bool:
        """Halt if drawdown > 8%"""
        drawdown = (self.current_equity - self.peak_equity) / self.peak_equity
        return drawdown < -0.08  # Halt if true
    
    def check_max_position_count(self) -> bool:
        """Halt if > 5 active positions"""
        return len(self.active_positions) > 5
    
    def check_correlation_risk(self, new_position) -> bool:
        """Skip if correlation with existing positions > 0.7"""
        for existing in self.active_positions:
            corr = calculate_correlation(new_position.symbol, existing.symbol)
            if corr > 0.7:
                return False  # Skip
        return True
    
    def validate_entry(self, entry_price, stop_loss, account_balance) -> bool:
        """Check if entry is valid"""
        risk_per_trade = account_balance * 0.03  # 3% max
        sl_distance = abs(entry_price - stop_loss)
        
        if sl_distance == 0:
            return False
        
        position_size = risk_per_trade / sl_distance
        # Additional checks...
        return True
```

## Monitoring & Observability

```python
class ObservabilityLayer:
    """Structured logging + metrics + alerting"""
    
    def __init__(self):
        self.logger = StructuredLogger()  # JSON output
        self.metrics = MetricsCollector()  # Prometheus
        self.alerter = AlertNotifier()  # Telegram
    
    def log_decision(self, decision_id: str, factors: Dict, confidence: float):
        """Log pattern detection → confidence calculation"""
        self.logger.info({
            "event": "pattern_detected",
            "decision_id": decision_id,
            "factors": factors,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat(),
        })
        self.metrics.histogram("pattern_confidence", confidence)
    
    def log_order_placed(self, order_id: str, symbol: str, side: str, price: float):
        """Log order execution"""
        self.logger.info({
            "event": "order_placed",
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "price": price,
            "timestamp": datetime.utcnow().isoformat(),
        })
        self.metrics.counter("orders_placed", 1, labels={"side": side})
    
    def self_check(self) -> Dict[str, bool]:
        """Health check every 5 minutes"""
        checks = {
            "exchange_latency_ok": self.measure_latency() < 500,  # ms
            "daily_loss_ok": not self.risk_engine.check_daily_loss_halt(),
            "drawdown_ok": not self.risk_engine.check_drawdown_halt(),
            "position_count_ok": self.risk_engine.check_max_position_count(),
        }
        
        for check, status in checks.items():
            if not status:
                self.alerter.send_warning(f"{check} = {status}")
        
        return checks
```

## Main Loop: 60-Second Cycle

```python
class BotEngine:
    """
    Main loop: Every 60 seconds
    
    1. FETCH (2-5s): Get latest OHLCV
    2. ANALYZE (10-20s): Detect patterns, score confluence
    3. QUALIFY (5s): Check if confidence > 65%
    4. EXECUTE (5-10s): Place orders if signal ready
    5. MONITOR (10s): Check positions, SL/TP, halts
    6. SELF_CHECK (5m): Health check
    """
    
    def __init__(self):
        self.ccxt_client = initialize_exchanges()
        self.technical_analyzer = TechnicalAnalyzer()
        self.risk_engine = RiskEngine()
        self.state_machine = BotStateMachine()
        self.warehouse = Warehouse()
        self.observability = ObservabilityLayer()
    
    def run(self):
        """Main loop"""
        cycle_count = 0
        
        while True:
            try:
                cycle_start = time.time()
                
                # STEP 1: FETCH
                self.fetch_latest_ohlcv()
                
                # STEP 2: ANALYZE
                candidates = self.technical_analyzer.scan_all_coins()
                
                # STEP 3: QUALIFY
                qualified = [c for c in candidates if c.confidence > 0.65]
                
                # STEP 4: EXECUTE
                for setup in qualified:
                    if self.price_touches_entry(setup):
                        self.execute_order_triplet(setup)
                
                # STEP 5: MONITOR
                self.monitor_active_positions()
                
                # STEP 6: SELF-CHECK (every 5 min)
                if cycle_count % 5 == 0:
                    health = self.observability.self_check()
                    if not all(health.values()):
                        self.logger.warning(f"Health check failed: {health}")
                
                # LOG
                cycle_time = time.time() - cycle_start
                self.warehouse.log_cycle({
                    "cycle": cycle_count,
                    "duration_ms": cycle_time * 1000,
                    "candidates_found": len(candidates),
                    "qualified": len(qualified),
                })
                
                cycle_count += 1
                
                # Sleep until next cycle (target 60s)
                sleep_time = max(0, 60 - cycle_time)
                time.sleep(sleep_time)
                
            except Exception as e:
                self.logger.error(f"Bot engine error: {e}", exc_info=True)
                self.observability.alerter.send_critical(f"Bot crashed: {e}")
                # Restart logic (exponential backoff)
                time.sleep(min(300, 10 * (cycle_count % 10)))
```

## Testing Strategy

### Unit Tests
```python
def test_harmonic_gartley_detection():
    """Harmonic detector should find Gartley in known data"""
    detector = HarmonicDetector()
    swings = FIXTURE_GARTLEY_SWINGS
    pattern = detector.detect(swings)
    
    assert pattern.type == "GARTLEY"
    assert 0.65 <= pattern.confidence <= 0.75
    assert pattern.prz_low < pattern.prz_high

def test_stochastic_crossover():
    """Stochastic should detect momentum regime change"""
    closes = load_fixture("eth_1h_100_bars.csv")["close"]
    K, D = calculate_stochastic(closes)
    
    # Should be in OVERSOLD or OVERBOUGHT
    assert (K < 20 and D < 20) or (K > 80 and D > 80)

def test_confidence_scoring():
    """Confluence score should be 0-1 range"""
    factors = {
        "harmonic": 0.75,
        "stochastic": 0.85,
        "trend": 0.90,
    }
    score = compute_confluence_score(factors)
    
    assert 0.0 <= score <= 1.0
    assert score > 0.75  # Should be high

def test_position_sizing():
    """Position size should scale with confidence"""
    size_high_conf = calculate_position_size(10000, 50000, 49000, confidence=0.90)
    size_low_conf = calculate_position_size(10000, 50000, 49000, confidence=0.60)
    
    assert size_high_conf > size_low_conf
```

### Integration Tests
```python
def test_full_signal_generation():
    """OHLCV → Pattern → Confluence → Signal"""
    ohlcv = load_fixture("btc_4h_200_bars.csv")
    
    analyzer = TechnicalAnalyzer()
    signal = analyzer.analyze(ohlcv)
    
    assert signal is not None
    assert signal.type in ["GARTLEY", "BUTTERFLY", "CRAB", "SHARK"]
    assert signal.r_r_ratio >= 1.618

def test_backtest_on_known_data():
    """Backtest should achieve 60%+ WR on BTC 1h"""
    ohlcv = load_fixture("btc_1h_1year.csv")
    
    backtester = HistoricalBacktester(ohlcv)
    results = backtester.run()
    
    assert results["wr"] >= 0.60
    assert results["profit_factor"] >= 1.5
    assert results["max_drawdown"] <= 0.15
```

### Monte Carlo Tests
```python
def test_parameter_robustness():
    """Does strategy work with ±10% parameter noise?"""
    base_params = {"fib_tolerance": 0.02, "min_confluence": 3}
    
    for i in range(100):
        noisy_params = add_noise(base_params, 0.10)
        detector = HarmonicDetector(**noisy_params)
        results = detector.backtest(ohlcv_data)
        
        # WR should not vary >5%
        assert abs(results["wr"] - base_results["wr"]) < 0.05
```

## Deployment Checklist

- [ ] Unit test coverage > 80%
- [ ] Integration tests pass
- [ ] Walk-forward backtest validates (5yr data, 20% holdout)
- [ ] Monte Carlo sensitivity test passes
- [ ] Paper mode 2+ weeks with WR > 55%
- [ ] Daily P&L > +0.5% average
- [ ] Latency < 100ms per cycle
- [ ] All health checks pass
- [ ] Operator review + sign-off
- [ ] Deploy to CONTROLLED_LIVE (50% capital)

---

## Next Steps: Implementation Phases

### Phase 1 (Weeks 1-2): Foundation
- [ ] Set up project structure
- [ ] Implement CCXT wrapper
- [ ] Create base utilities (EMA, Stochastic, ATR)
- [ ] Set up SQLite warehouse

### Phase 2 (Weeks 3-4): Pattern Detection
- [ ] Harmonic detector (Gartley)
- [ ] Fibonacci calculator
- [ ] Unit tests for all ratios
- [ ] Backtest on 1y BTC data

### Phase 3 (Weeks 5-6): Confluence
- [ ] Stochastic momentum engine
- [ ] FVG + Order Block detector
- [ ] Confluence scorer
- [ ] Backtest with gating

### Phase 4 (Weeks 7-8): Execution
- [ ] Order placer
- [ ] Risk engine
- [ ] Position tracker
- [ ] Paper trading 1 week

### Phase 5 (Weeks 9-10): Orchestration
- [ ] State machine
- [ ] Bayesian classifier
- [ ] Bot engine main loop
- [ ] Dashboard (Streamlit)

### Phase 6 (Weeks 11-12): Validation
- [ ] Walk-forward backtest
- [ ] Monte Carlo testing
- [ ] Paper mode 2 weeks
- [ ] Production deployment

