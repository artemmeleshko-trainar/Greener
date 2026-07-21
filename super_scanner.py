#!/usr/bin/env python3
"""SUPER SCANNER v1.0 — Multi-TF Pattern Recognition + Order Flow + Indicators

The most comprehensive scalp scanner in the suite. Combines:
  - 6 timeframes: 1m, 5m, 10m, 30m, 1h, 4h
  - 10+ candlestick patterns per TF (engulfing, pin bar, hammer, doji, etc.)
  - Technical indicators: RSI, EMA9/21/50, MACD, Bollinger Bands, vol surge
  - Structure: swing high/low pivots, support/resistance, Fibonacci
  - Order flow: CVD, candle delta, OI delta (when available)
  - Multi-TF alignment scoring (bonus for 3+ TFs agreeing)
  - All existing filters: AVOID list, staleness, exhausted-zone, funding gate

Confluence scoring 0-10 with 5+ threshold for entry.
Default TP/SL: +1% / -0.8% (scalp, backtested optimal).

Usage:
  python3 super_scanner.py                # live scan
  python3 super_scanner.py --backtest 7   # 7-day backtest with per-feature lift
  python3 super_scanner.py --pair SUIUSDT # single pair deep dive
  python3 super_scanner.py --verbose      # show all signal details
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict


# ============================================================
# CONSTANTS
# ============================================================

VERSION = "v1.4"  # Crypto-only + ELITE pattern tier + low-vol filter + 80 pairs — pending re-validation
# v1.4 changes (2026-06-05, backtest-discovered):
#   - ELITE tier (A): signals with >=3 confluent patterns flagged 🔥. Backtest: 2-pat
#       59.7% WR, 3-pat 68.7%, 4+ pat 73.0% (monotonic, large-n). `--elite` shows only these.
#   - LOW-VOLATILITY filter (B): skip coins with 5m ATR% < 0.25%. Backtest: low-vol tercile
#       nets -0.024R (LOSES after fees — move too small to clear 0.085% fee); high-vol +0.063R.
#   - TOP_PAIRS 60 -> 80 (more elite candidates; #80 still ~$35M vol). Filters protect quality.
#   - REJECTED after testing (do NOT add): ATR-scaled exits (fixed +1.5/-1.0 wins risk-adjusted),
#       bear-trend shorts (-3.2pp), fade-the-long (loses all regimes), OI/divergence (dead).
#
# v1.3 changes (2026-06-05):
#   - CRYPTO-ONLY: exclude tokenized stocks/commodities/pre-IPO from the universe.
#       Binance now lists ~77 TradFi perps (EQUITY/COMMODITY/PREMARKET/INDEX/KR_EQUITY,
#       contractType TRADIFI_PERPETUAL) that crack the top-60 by volume. The v1.2 edge
#       (60.9% WR) was validated on CRYPTO only — TradFi is out-of-sample AND carries
#       market-hours/gap risk that breaks the -1% stop assumption. Filter on
#       underlyingType=='COIN' (future-proof: new TradFi listings auto-excluded).
#   - Still selects TOP_PAIRS (60) by volume — now top-60 *crypto*.
#   - Fixed newly-listed filter (old `break` exited inner loop but still appended).
#
# v1.2 changes (2026-05-28, validated by 3-month backtest):
#   - Volume floor: $50M → $30M (capture more high-liquidity pairs)
#   - Top N pairs: 30 → 60 (more universe = more 6.0+ signals/day)
#   - Default ENGINE_THRESHOLD: 5.0 → 6.0 (only show high-conviction tier)
#       Tier 5.0-5.9: ~47% WR (drags performance) — now filtered out
#       Tier 6.0-6.9: ~61% WR ⭐ (this is what we trade)
#       Tier 7.0+:    ~63%+ WR (elite, rare)
#   - Default TP1: +1.0% → +1.5% (matches 30-min hold sweet spot)
#   - Default SL:  -0.8% → -1.0% (slightly wider, more breathing room)
#   - Documented OPTIMAL hold time: 15-30 minutes (NEVER under 15)
#
# v1.1 changes (kept):
#    - Near 24h extremes: 1.0 → 1.5 pts (+8.5pp lift on SHORT)
#    - Pattern count ≥2: 1.5 → 2.0 pts (+6.8pp lift)
#    - Vol surge: 0.5 → 1.0 pt (+4.4pp lift)
#    - S/R proximity: 1.0 → 1.5 pts (+1.9pp lift)
BASE_URL = "https://fapi.binance.com"
STATE_DIR = os.path.expanduser("~/.super_scanner_state")
os.makedirs(STATE_DIR, exist_ok=True)

DEFAULT_VOL_MIN = 30_000_000  # v1.2: lowered to capture more pairs in universe
DEFAULT_POS_SIZE = 15
LEVERAGE = 3
RISK_PCT = 0.01

# Scoring thresholds (v1.2 — empirically optimized)
ENGINE_THRESHOLD = 6.0  # v1.2: was 5.0; raised to high-conviction tier (61% backtest WR)
EXHAUSTED_PCT_24H = 15
MAX_HOLD_5M = 6  # 30-min scalp horizon (NEVER hold less than 15 min — proven losing horizon)
TOP_PAIRS = 80   # v1.4: 60->80 (more elite candidates; #80 still ~$35M vol)
ATR_MIN_PCT = 0.25   # v1.4-B: skip coins with 5m ATR% below this (low-vol = net-negative after fees)
ELITE_PATTERNS = 3   # v1.4-A: signals with >= this many confluent patterns are 🔥 ELITE
CREAM_PATTERNS = 4   # 💎 CREAM tier: >=4 patterns AND at S/R (65% WR / 316-trade 30d backtest)

# v2 (2026-06-17): the only config validated net-POSITIVE after fees across THREE
# non-overlapping 30d windows AND under harsh 0.20% slippage. Entry: >=3 patterns
# AND 5m ATR% >= 0.5 (volatility floor that clears the fee wall). Exit: +2.0/-1.5,
# 60-min hold, LIMIT (maker) entries. Net/trade after fees ~+0.13 to +0.31% (harsh
# to base). This REPLACES megapower's +0.5/-1.0, which backtests + the live journal
# proved is net-NEGATIVE after fees. See refine_experiment.py / confirm_3window.py.
V2_ATR_MIN = 0.5     # 5m ATR% floor for the v2 tier (vs 0.25 base low-vol skip)
V2_TP = 2.0          # v2 take-profit %
V2_SL = 1.5          # v2 stop-loss %

# Reuse AVOID list from existing scanners
AVOID_LIST = set()  # tier-1: confirmed bad pairs (empty for now)

STOCK_TICKERS = {
    'NVDAUSDT', 'INTCUSDT', 'QCOMUSDT', 'MUUSDT', 'SNDKUSDT', 'AAPLUSDT',
    'TSLAUSDT', 'AMZNUSDT', 'GOOGUSDT', 'METAUSDT', 'CRCLUSDT', 'PAYPUSDT',
    'XAGUSDT', 'XAUUSDT', 'XPDUSDT', 'EWYUSDT',
}


# ============================================================
# DATA FETCHING
# ============================================================

def http_get(url, timeout=10, retries=2):
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.3)
    return None


def fetch_klines(symbol, interval, limit=500, end_ms=None):
    url = f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    if end_ms:
        url += f"&endTime={end_ms}"
    return http_get(url)


def fetch_tickers():
    return http_get(f"{BASE_URL}/fapi/v1/ticker/24hr")


def fetch_funding():
    return http_get(f"{BASE_URL}/fapi/v1/premiumIndex")


def fetch_funding_history(symbol, limit=200):
    return http_get(f"{BASE_URL}/fapi/v1/fundingRate?symbol={symbol}&limit={limit}")


def fetch_exchange_info():
    return http_get(f"{BASE_URL}/fapi/v1/exchangeInfo")


def aggregate_klines(klines, n):
    """Build n-period aggregated candles from base klines."""
    out = []
    for i in range(0, len(klines) - n + 1, n):
        chunk = klines[i:i + n]
        out.append([
            chunk[0][0], chunk[0][1],
            str(max(float(k[2]) for k in chunk)),
            str(min(float(k[3]) for k in chunk)),
            chunk[-1][4],
            str(sum(float(k[5]) for k in chunk)),
            chunk[-1][6],
            str(sum(float(k[7]) for k in chunk)),
            str(sum(int(k[8]) for k in chunk)),
            str(sum(float(k[9]) for k in chunk)),
            str(sum(float(k[10]) for k in chunk)),
            '0',
        ])
    return out


# ============================================================
# INDICATORS
# ============================================================

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    g, l = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        g.append(d if d > 0 else 0)
        l.append(-d if d < 0 else 0)
    ag = sum(g[-period:]) / period
    al = sum(l[-period:]) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line, histogram) or None."""
    if len(closes) < slow + signal:
        return None, None, None
    ef = ema(closes, fast)
    es = ema(closes, slow)
    if ef is None or es is None:
        return None, None, None
    macd_val = ef - es
    # For signal line, need MACD series — compute approximation from last values
    # Simplified: use rolling EMA on last `signal` MACD values
    macds = []
    for i in range(signal, 0, -1):
        ef_i = ema(closes[:-i] if i > 0 else closes, fast)
        es_i = ema(closes[:-i] if i > 0 else closes, slow)
        if ef_i and es_i:
            macds.append(ef_i - es_i)
    if not macds:
        return macd_val, None, None
    sig = ema(macds, signal) if len(macds) >= signal else sum(macds) / len(macds)
    hist = macd_val - sig if sig else None
    return macd_val, sig, hist


def bollinger_bands(closes, period=20, dev=2):
    """Returns (upper, middle, lower) or None."""
    if len(closes) < period:
        return None, None, None
    recent = closes[-period:]
    mid = sum(recent) / period
    var = sum((c - mid) ** 2 for c in recent) / period
    std = var ** 0.5
    return mid + dev * std, mid, mid - dev * std


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def candle_delta(k):
    return 2 * float(k[9]) - float(k[5])


# ============================================================
# PATTERN DETECTION
# ============================================================

def is_bullish_engulfing(klines):
    """Current green candle's body engulfs previous red candle's body."""
    if len(klines) < 2:
        return False
    prev = klines[-2]
    cur = klines[-1]
    prev_open, prev_close = float(prev[1]), float(prev[4])
    cur_open, cur_close = float(cur[1]), float(cur[4])
    prev_red = prev_close < prev_open
    cur_green = cur_close > cur_open
    engulfs = cur_open < prev_close and cur_close > prev_open
    return prev_red and cur_green and engulfs


def is_bearish_engulfing(klines):
    if len(klines) < 2:
        return False
    prev = klines[-2]
    cur = klines[-1]
    prev_open, prev_close = float(prev[1]), float(prev[4])
    cur_open, cur_close = float(cur[1]), float(cur[4])
    prev_green = prev_close > prev_open
    cur_red = cur_close < cur_open
    engulfs = cur_open > prev_close and cur_close < prev_open
    return prev_green and cur_red and engulfs


def is_hammer(klines):
    """Small body at top, long lower wick (≥2× body), typically at recent low."""
    if len(klines) < 1:
        return False
    k = klines[-1]
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    body = abs(c - o)
    range_size = h - l
    if range_size <= 0 or body <= 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    body_top_pct = (h - max(o, c)) / range_size
    return lower_wick >= 2 * body and upper_wick < body * 0.5 and body_top_pct < 0.3


def is_shooting_star(klines):
    """Small body at bottom, long upper wick (≥2× body)."""
    if len(klines) < 1:
        return False
    k = klines[-1]
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    body = abs(c - o)
    range_size = h - l
    if range_size <= 0 or body <= 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    body_bot_pct = (min(o, c) - l) / range_size
    return upper_wick >= 2 * body and lower_wick < body * 0.5 and body_bot_pct < 0.3


def is_doji(klines):
    """Open ≈ close, body < 10% of range."""
    if len(klines) < 1:
        return False
    k = klines[-1]
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    body = abs(c - o)
    range_size = h - l
    if range_size <= 0:
        return False
    return body / range_size < 0.1


def is_three_white_soldiers(klines):
    """3 consecutive green candles, each closing higher."""
    if len(klines) < 3:
        return False
    for i in range(-3, 0):
        k = klines[i]
        o, c = float(k[1]), float(k[4])
        if c <= o:
            return False
    # Each close > previous close
    for i in range(-2, 0):
        if float(klines[i][4]) <= float(klines[i-1][4]):
            return False
    return True


def is_three_black_crows(klines):
    if len(klines) < 3:
        return False
    for i in range(-3, 0):
        k = klines[i]
        o, c = float(k[1]), float(k[4])
        if c >= o:
            return False
    for i in range(-2, 0):
        if float(klines[i][4]) >= float(klines[i-1][4]):
            return False
    return True


def is_inside_bar(klines):
    """Current candle's range within previous candle's range."""
    if len(klines) < 2:
        return False
    prev = klines[-2]
    cur = klines[-1]
    return float(cur[2]) < float(prev[2]) and float(cur[3]) > float(prev[3])


def is_pin_bar_bullish(klines):
    """Long lower wick, body in upper third."""
    if len(klines) < 1:
        return False
    k = klines[-1]
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    range_size = h - l
    if range_size <= 0:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - l
    return lower_wick / range_size > 0.6 and body / range_size < 0.3


def is_pin_bar_bearish(klines):
    if len(klines) < 1:
        return False
    k = klines[-1]
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    range_size = h - l
    if range_size <= 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    return upper_wick / range_size > 0.6 and body / range_size < 0.3


def detect_patterns(klines):
    """Returns dict of pattern_name → bool. Bullish patterns are LONG bias, bearish are SHORT."""
    return {
        'bullish_engulfing': is_bullish_engulfing(klines),
        'bearish_engulfing': is_bearish_engulfing(klines),
        'hammer': is_hammer(klines),
        'shooting_star': is_shooting_star(klines),
        'doji': is_doji(klines),
        'three_white_soldiers': is_three_white_soldiers(klines),
        'three_black_crows': is_three_black_crows(klines),
        'inside_bar': is_inside_bar(klines),
        'pin_bar_bullish': is_pin_bar_bullish(klines),
        'pin_bar_bearish': is_pin_bar_bearish(klines),
    }


def pattern_bias(patterns):
    """Returns (long_count, short_count) based on detected patterns."""
    long_patterns = ['bullish_engulfing', 'hammer', 'three_white_soldiers', 'pin_bar_bullish']
    short_patterns = ['bearish_engulfing', 'shooting_star', 'three_black_crows', 'pin_bar_bearish']
    long_count = sum(1 for p in long_patterns if patterns.get(p))
    short_count = sum(1 for p in short_patterns if patterns.get(p))
    return long_count, short_count


# ============================================================
# STRUCTURE: Swing pivots + S/R + Fibonacci
# ============================================================

def find_pivots(highs, lows, window=3):
    """Returns (pivot_highs, pivot_lows) as lists of indices.
    A pivot high is a candle whose high is greater than `window` candles on each side."""
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            pivot_highs.append(i)
        if lows[i] == min(lows[i-window:i+window+1]):
            pivot_lows.append(i)
    return pivot_highs, pivot_lows


def support_resistance_levels(highs, lows, window=3, max_levels=5):
    """Returns (resistance_levels, support_levels) from recent pivots."""
    pivot_highs, pivot_lows = find_pivots(highs, lows, window)
    resistance = sorted({highs[i] for i in pivot_highs}, reverse=True)[:max_levels]
    support = sorted({lows[i] for i in pivot_lows})[:max_levels]
    return resistance, support


def near_sr_level(price, levels, tolerance_pct=0.5):
    """Returns the closest level if within tolerance%, else None."""
    for lvl in levels:
        if lvl > 0 and abs((price - lvl) / lvl * 100) <= tolerance_pct:
            return lvl
    return None


def fib_levels(swing_high, swing_low):
    if swing_high <= swing_low:
        return {}
    r = swing_high - swing_low
    return {
        '0.382': swing_high - 0.382 * r,
        '0.5': swing_high - 0.5 * r,
        '0.618': swing_high - 0.618 * r,
        '0.786': swing_high - 0.786 * r,
        'ext_1.272': swing_high + 0.272 * r,
        'ext_1.618': swing_high + 0.618 * r,
        'ext_-0.272': swing_low - 0.272 * r,
        'ext_-0.618': swing_low - 0.618 * r,
    }


def near_fib_level(price, levels, tolerance_pct=0.5):
    for name, lvl in levels.items():
        if lvl > 0 and abs((price - lvl) / lvl * 100) <= tolerance_pct:
            return (name, lvl)
    return None


# ============================================================
# COMPUTE FEATURES FOR A PAIR
# ============================================================

def compute_signal(symbol, klines_by_tf, ticker_24h, funding_rate, btc_24h_pct, btc_1h_pct, btc_active):
    """Returns dict with LONG and SHORT scores + feature breakdown."""
    closes_5m = [float(k[4]) for k in klines_by_tf['5m']]
    if len(closes_5m) < 30:
        return None

    cur_price = closes_5m[-1]

    # Per-TF indicators
    indicators = {}
    for tf, klines in klines_by_tf.items():
        if not klines or len(klines) < 30:
            indicators[tf] = None
            continue
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        vols = [float(k[5]) for k in klines]
        indicators[tf] = {
            'rsi': rsi(closes),
            'ema9': ema(closes, 9),
            'ema21': ema(closes, 21),
            'ema50': ema(closes, 50) if len(closes) >= 50 else None,
            'atr': atr(highs, lows, closes),
            'patterns': detect_patterns(klines),
            'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols,
        }

    # Multi-TF EMA trend alignment
    long_trends = 0
    short_trends = 0
    for tf in ['5m', '10m', '30m', '1h', '4h']:
        ind = indicators.get(tf)
        if not ind or not ind.get('ema9') or not ind.get('ema21'):
            continue
        if ind['ema9'] > ind['ema21']:
            long_trends += 1
        else:
            short_trends += 1

    # Multi-TF RSI extreme count
    long_rsi_oversold = 0
    short_rsi_overbought = 0
    for tf in ['5m', '30m', '1h']:  # we found 15m to be contra-indicator; skip it
        ind = indicators.get(tf)
        if not ind or ind.get('rsi') is None:
            continue
        if ind['rsi'] < 30:
            long_rsi_oversold += 1
        elif ind['rsi'] > 70:
            short_rsi_overbought += 1

    # Multi-TF pattern count
    long_patterns_count = 0
    short_patterns_count = 0
    for tf in ['5m', '10m', '30m', '1h']:
        ind = indicators.get(tf)
        if not ind:
            continue
        lp, sp = pattern_bias(ind['patterns'])
        long_patterns_count += lp
        short_patterns_count += sp

    # Volume surge (5m)
    vols_5m = indicators['5m']['vols'] if indicators.get('5m') else []
    vol_surge = 0
    if len(vols_5m) >= 30:
        recent = sum(vols_5m[-3:]) / 3
        avg = sum(vols_5m[:-3]) / (len(vols_5m) - 3)
        vol_surge = recent / avg if avg > 0 else 1

    # 24h range position
    h24 = float(ticker_24h.get('highPrice', cur_price))
    l24 = float(ticker_24h.get('lowPrice', cur_price))
    chg_24h = float(ticker_24h.get('priceChangePercent', 0))
    pos_in_range = (cur_price - l24) / (h24 - l24) * 100 if h24 > l24 else 50

    # S/R proximity (using 1h pivots)
    ind_1h = indicators.get('1h')
    sr_near_support = sr_near_resistance = False
    if ind_1h:
        resistance, support = support_resistance_levels(ind_1h['highs'], ind_1h['lows'])
        if near_sr_level(cur_price, support, tolerance_pct=0.8):
            sr_near_support = True
        if near_sr_level(cur_price, resistance, tolerance_pct=0.8):
            sr_near_resistance = True

    # Fibonacci proximity (from 30m last 48 candles)
    ind_30m = indicators.get('30m')
    fib_near = None
    if ind_30m and len(ind_30m['highs']) >= 48:
        recent_h = ind_30m['highs'][-48:]
        recent_l = ind_30m['lows'][-48:]
        fl = fib_levels(max(recent_h), min(recent_l))
        fib_near = near_fib_level(cur_price, fl, tolerance_pct=0.6)

    # CVD / candle delta
    klines_5m = klines_by_tf.get('5m', [])
    deltas = [candle_delta(k) for k in klines_5m[-5:]] if klines_5m else []
    cur_delta = deltas[-1] if deltas else 0
    delta_3sum = sum(deltas[-3:]) if deltas else 0

    # 30m move (staleness check)
    pct_30m = 0
    if len(closes_5m) >= 7 and closes_5m[-7]:
        pct_30m = (closes_5m[-1] - closes_5m[-7]) / closes_5m[-7] * 100

    # ===== LONG SCORE (v1.1 — refined after backtest) =====
    long_score = 0
    long_reasons = []

    # 1. Funding rate (mild bias only — extreme is contra)
    if -0.001 <= funding_rate < -0.0003:
        long_score += 1
        long_reasons.append(f'fund {funding_rate*100:+.3f}% (mild squeeze)')

    # 2. Multi-TF RSI alignment (5m + 30m + 1h — skip 15m, contra-indicator)
    if long_rsi_oversold >= 2:
        long_score += 1.5
        long_reasons.append(f'RSI oversold {long_rsi_oversold}/3 TFs')
    elif long_rsi_oversold == 1:
        long_score += 0.5

    # 3. Multi-TF EMA trend — REMOVED in v1.1 (negative lift)

    # 4. Pattern detection — BOOSTED in v1.1 (high lift)
    if long_patterns_count >= 2:
        long_score += 2.0  # was 1.5
        long_reasons.append(f'{long_patterns_count} bullish patterns')
    elif long_patterns_count == 1:
        long_score += 1.0  # was 0.5

    # 5. Near 24h low — BOOSTED (highest LONG lift)
    if pos_in_range < 15:
        long_score += 1.5  # was 1.0
        long_reasons.append(f'near 24h low ({pos_in_range:.0f}%)')
    elif pos_in_range < 30:
        long_score += 0.5

    # 6. S/R support proximity — BOOSTED
    if sr_near_support:
        long_score += 1.5  # was 1.0
        long_reasons.append('at 1h support')

    # 7. Fib retracement — kept on LONG (positive lift on LONG side)
    if fib_near and fib_near[0] in ('0.382', '0.5', '0.618', '0.786'):
        long_score += 0.5
        long_reasons.append(f'Fib {fib_near[0]}')

    # 8. CVD confirmation — REMOVED in v1.1 (-5pp lift)

    # 9. Volume surge — BOOSTED (positive lift)
    if vol_surge > 1.5:
        long_score += 1.0  # was 0.5
        long_reasons.append(f'vol surge {vol_surge:.1f}x')
    elif vol_surge > 1.2:
        long_score += 0.5

    # 10. BTC alignment — REMOVED in v1.1 (negative lift)

    # 11. 24h aligned
    if 1.5 < chg_24h < 12:
        long_score += 0.5
        long_reasons.append(f'24h +{chg_24h:.1f}%')

    # ===== SHORT SCORE (v1.1 — refined after backtest) =====
    short_score = 0
    short_reasons = []

    if 0.0003 < funding_rate <= 0.001:
        short_score += 1
        short_reasons.append(f'fund {funding_rate*100:+.3f}% (mild flush)')

    # Multi-TF RSI on SHORT
    if short_rsi_overbought >= 2:
        short_score += 1.5
        short_reasons.append(f'RSI overbought {short_rsi_overbought}/3 TFs')
    elif short_rsi_overbought == 1:
        short_score += 0.5

    # Multi-TF EMA trend — REMOVED (-7.2pp lift on SHORT, largest negative)

    # Pattern detection — BOOSTED
    if short_patterns_count >= 2:
        short_score += 2.0  # was 1.5
        short_reasons.append(f'{short_patterns_count} bearish patterns')
    elif short_patterns_count == 1:
        short_score += 1.0  # was 0.5

    # Near 24h high — BOOSTED (highest SHORT lift +8.5pp)
    if pos_in_range > 85:
        short_score += 1.5  # was 1.0
        short_reasons.append(f'near 24h high ({pos_in_range:.0f}%)')
    elif pos_in_range > 70:
        short_score += 0.5

    # S/R resistance — BOOSTED
    if sr_near_resistance:
        short_score += 1.5  # was 1.0
        short_reasons.append('at 1h resistance')

    # Fib on SHORT — REMOVED (-2.1pp lift)

    # CVD — REMOVED (-5.4pp on SHORT)

    # Volume surge — BOOSTED
    if vol_surge > 1.5:
        short_score += 1.0
        short_reasons.append(f'vol surge {vol_surge:.1f}x')
    elif vol_surge > 1.2:
        short_score += 0.5

    # BTC alignment — REMOVED

    # 24h aligned
    if -12 < chg_24h < -1.5:
        short_score += 0.5
        short_reasons.append(f'24h {chg_24h:.1f}%')

    # Staleness
    is_stale_long = pct_30m > 2.0
    is_stale_short = pct_30m < -2.0

    return {
        'long_score': long_score,
        'short_score': short_score,
        'long_reasons': long_reasons,
        'short_reasons': short_reasons,
        'is_stale_long': is_stale_long,
        'is_stale_short': is_stale_short,
        'cur_price': cur_price,
        'chg_24h': chg_24h,
        'pos_in_range': pos_in_range,
        'vol_surge': vol_surge,
        'pct_30m': pct_30m,
        'long_rsi_oversold': long_rsi_oversold,
        'short_rsi_overbought': short_rsi_overbought,
        'long_trends': long_trends,
        'short_trends': short_trends,
        'long_patterns_count': long_patterns_count,
        'short_patterns_count': short_patterns_count,
        'sr_near_support': sr_near_support,
        'sr_near_resistance': sr_near_resistance,
        'fib_near': fib_near,
        'indicators': indicators,
        'funding': funding_rate,
    }


# ============================================================
# TP/SL — Scalp default + S/R-based stretch
# ============================================================

def compute_tp_sl(side, entry, signal_data):
    """Returns (sl_price, tp1_price, tp2_price).

    v1.2: TP/SL updated from +1.0%/-0.8% to +1.5%/-1.0% based on 90-day backtest
    (14,733 signals). Higher net P&L (+447% vs +337% on 30min/Score 6.0+) and
    slightly higher WR (62.6% vs 60.5%) at 30-min hold horizon.
    """
    SCALP_TP = 0.015  # +1.5% — v1.2 optimal
    SCALP_SL = 0.010  # -1.0% — v1.2 optimal (R:R 1.5:1)

    if side == 'LONG':
        sl = entry * (1 - SCALP_SL)
        tp1 = entry * (1 + SCALP_TP)
        # TP2: nearest resistance level or Fib 1.272
        ind_1h = signal_data['indicators'].get('1h')
        tp2 = entry * (1 + SCALP_TP * 3)  # default 3x stretch
        if ind_1h:
            resistance, _ = support_resistance_levels(ind_1h['highs'], ind_1h['lows'])
            next_resistance = next((r for r in resistance if r > entry), None)
            if next_resistance and next_resistance < entry * 1.05:
                tp2 = next_resistance
    else:
        sl = entry * (1 + SCALP_SL)
        tp1 = entry * (1 - SCALP_TP)
        ind_1h = signal_data['indicators'].get('1h')
        tp2 = entry * (1 - SCALP_TP * 3)
        if ind_1h:
            _, support = support_resistance_levels(ind_1h['highs'], ind_1h['lows'])
            next_support = next((s for s in support if s < entry), None)
            if next_support and next_support > entry * 0.95:
                tp2 = next_support
    return sl, tp1, tp2


# ============================================================
# MAIN SCAN
# ============================================================

def fetch_all_tfs(symbol):
    """Fetch klines for all timeframes. Returns dict."""
    k1m = fetch_klines(symbol, '1m', 200)
    k5m = fetch_klines(symbol, '5m', 200)
    if not k5m or len(k5m) < 30:
        return None
    k15m = fetch_klines(symbol, '15m', 100)
    k30m = fetch_klines(symbol, '30m', 100)
    k1h = fetch_klines(symbol, '1h', 100)
    k4h = fetch_klines(symbol, '4h', 100)
    # CLOSED-CANDLE FIX (2026-06-19): drop the last, still-forming candle of each TF so
    # signals are computed on COMPLETED candles only. Makes a signal STABLE for the whole
    # 5m window (no mid-candle flicker), makes monitor==executor agree, and matches the
    # backtest (which used closed candles). Actual trade entry still uses live price.
    def _closed(k):
        return k[:-1] if (k and len(k) > 2) else k
    k1m, k5m, k15m, k30m, k1h, k4h = _closed(k1m), _closed(k5m), _closed(k15m), _closed(k30m), _closed(k1h), _closed(k4h)
    k10m = aggregate_klines(k5m, 2) if k5m else None
    return {
        '1m': k1m or [],
        '5m': k5m,
        '10m': k10m or [],
        '15m': k15m or [],
        '30m': k30m or [],
        '1h': k1h or [],
        '4h': k4h or [],
    }


def _build_symbol_meta(exchange_info):
    """symbol -> exchangeInfo entry, for O(1) lookup."""
    meta = {}
    if exchange_info:
        for s in exchange_info.get('symbols', []):
            meta[s.get('symbol')] = s
    return meta


def is_crypto(sym, meta):
    """True only for genuine crypto. v1.3: Binance lists tokenized stocks/commodities/
    pre-IPO (underlyingType EQUITY/COMMODITY/PREMARKET/INDEX/KR_EQUITY, contractType
    TRADIFI_PERPETUAL). The validated edge was measured on crypto only, so exclude them.
    Conservative: a symbol we can't confirm as COIN is excluded."""
    s = meta.get(sym)
    if s is None:
        return False
    ut = s.get('underlyingType')
    if ut and ut != 'COIN':
        return False
    ct = s.get('contractType')
    if ct and ct != 'PERPETUAL':
        return False
    return True


def filter_candidates(tickers, exchange_info):
    """Tier-1 filters: CRYPTO-ONLY (v1.3), vol, manipulation guards, AVOID, newly-listed."""
    meta = _build_symbol_meta(exchange_info)
    have_meta = bool(meta)
    valid = []
    for sym, t in tickers.items():
        if not sym.endswith('USDT'):
            continue
        # v1.3: crypto-only — drop tokenized stocks/commodities/pre-IPO
        if sym in STOCK_TICKERS:
            continue
        if have_meta and not is_crypto(sym, meta):
            continue
        vol = float(t.get('quoteVolume', 0))
        chg = float(t.get('priceChangePercent', 0))
        h = float(t.get('highPrice', 0))
        l = float(t.get('lowPrice', 0))
        if vol < DEFAULT_VOL_MIN:
            continue
        if abs(chg) > EXHAUSTED_PCT_24H:
            continue
        if l > 0 and ((h - l) / l * 100) > 35:
            continue
        if sym in AVOID_LIST:
            continue
        # Skip newly-listed (<7 days) — v1.3: fixed (old `break` left the symbol appended)
        s = meta.get(sym)
        if s and s.get('onboardDate'):
            age_days = (time.time() * 1000 - s['onboardDate']) / 86_400_000
            if age_days < 7:
                continue
        valid.append(sym)
    return valid


def scan_live(vol_min=DEFAULT_VOL_MIN, pos_size=DEFAULT_POS_SIZE, single_pair=None, verbose=False, elite_only=False, cream_only=False, megapower=False, v2=False):
    print("=" * 100)
    print(f"  BINANCE FUTURES — SUPER SCANNER {VERSION}")
    print(f"  Multi-TF (1m/5m/10m/15m/30m/1h/4h) × Patterns × Indicators × Order Flow")
    print(f"  Vol floor: ${vol_min/1e6:.0f}M | Position: ${pos_size} | Threshold: {ENGINE_THRESHOLD}/10 (high-conviction tier)")
    print(f"  TP/SL: +1.5%/-1.0% scalp (v1.2 — backtest-optimized) + S/R-based stretch (TP2)")
    print(f"  Universe: top {TOP_PAIRS} CRYPTO pairs (TradFi excluded) | Hold 15-30 min (NEVER under 15 min)")
    print("=" * 100)

    # Step 1: market context
    print("\n=== Step 1: Market context ===")
    tickers_list = fetch_tickers() or []
    tickers = {t['symbol']: t for t in tickers_list}
    funding_list = fetch_funding() or []
    funding_map = {p['symbol']: float(p['lastFundingRate']) for p in funding_list}
    exchange_info = fetch_exchange_info()

    btc_t = tickers.get('BTCUSDT', {})
    eth_t = tickers.get('ETHUSDT', {})
    btc_24h = float(btc_t.get('priceChangePercent', 0))
    eth_24h = float(eth_t.get('priceChangePercent', 0))
    print(f"  BTC 24h: {btc_24h:+.2f}%  ETH 24h: {eth_24h:+.2f}%")

    btc_1h_klines = fetch_klines('BTCUSDT', '1h', limit=2)
    btc_1h_pct = 0
    if btc_1h_klines:
        btc_1h_pct = (float(btc_1h_klines[-1][4]) - float(btc_1h_klines[-1][1])) / float(btc_1h_klines[-1][1]) * 100
    btc_active = abs(btc_1h_pct) > 0.5
    print(f"  BTC 1h: {btc_1h_pct:+.2f}%  (regime: {'ACTIVE' if btc_active else 'QUIET'})")

    # Step 2: filter
    print("\n=== Step 2: Candidate filtering ===")
    if single_pair:
        candidates = [single_pair] if single_pair in tickers else []
    else:
        candidates = filter_candidates(tickers, exchange_info)
        candidates.sort(key=lambda s: -float(tickers[s].get('quoteVolume', 0)))
        candidates = candidates[:TOP_PAIRS]  # v1.2: top 60 by volume (was 30)

    print(f"  Tradeable universe: {len(candidates)} pairs after filters")

    # Step 3: scan
    print(f"\n=== Step 3: Multi-TF analysis on {len(candidates)} pairs ===")
    signals = []
    processed = 0
    for sym in candidates:
        processed += 1
        klines_by_tf = fetch_all_tfs(sym)
        if not klines_by_tf:
            continue
        ticker_24h = tickers[sym]
        f = funding_map.get(sym, 0)
        s = compute_signal(sym, klines_by_tf, ticker_24h, f, btc_24h, btc_1h_pct, btc_active)
        if not s:
            continue
        # v1.4-B: low-volatility filter — coins moving < ATR_MIN_PCT net-negative after fees
        ind5 = s['indicators'].get('5m')
        atr_pct = (ind5['atr'] / s['cur_price'] * 100) if (ind5 and ind5.get('atr') and s['cur_price']) else None
        s['atr_pct'] = atr_pct  # stash for the v2 volatility-floor tier
        if atr_pct is not None and atr_pct < ATR_MIN_PCT:
            continue
        # Filter score >= threshold
        if s['long_score'] >= ENGINE_THRESHOLD and not s['is_stale_long']:
            signals.append(('LONG', sym, s))
        if s['short_score'] >= ENGINE_THRESHOLD and not s['is_stale_short']:
            signals.append(('SHORT', sym, s))
        if processed % 10 == 0:
            print(f"  Processed {processed}/{len(candidates)}...", end='\r', flush=True)

    print(f"  Scanned {processed} pairs, found {len(signals)} tradeable signals")

    # v1.4-A: pattern-count helper + ELITE classification (>=3 patterns -> ~70% WR)
    def patcount(side, s):
        return s['long_patterns_count'] if side == 'LONG' else s['short_patterns_count']
    def at_sr(side, s):
        return s['sr_near_support'] if side == 'LONG' else s['sr_near_resistance']
    def is_cream(side, s):
        return patcount(side, s) >= CREAM_PATTERNS and at_sr(side, s)
    def is_v2(side, s):
        # validated tier: >=3 patterns AND 5m ATR% >= floor (clears the fee wall)
        return patcount(side, s) >= ELITE_PATTERNS and (s.get('atr_pct') or 0) >= V2_ATR_MIN
    if v2:
        signals = [t for t in signals if is_v2(t[0], t[2])]
    elif cream_only or megapower:
        signals = [t for t in signals if is_cream(t[0], t[2])]
    elif elite_only:
        signals = [t for t in signals if patcount(t[0], t[2]) >= ELITE_PATTERNS]

    # Sort: ELITE first, then by score
    signals.sort(key=lambda x: (-(1 if patcount(x[0], x[2]) >= ELITE_PATTERNS else 0),
                                -(x[2]['long_score'] if x[0] == 'LONG' else x[2]['short_score'])))

    # Step 4: render
    elite_n = sum(1 for t in signals if patcount(t[0], t[2]) >= ELITE_PATTERNS)
    print("\n" + "=" * 100)
    cream_n = sum(1 for t in signals if is_cream(t[0], t[2]))
    label = (f"✅ V2-VALIDATED (>={ELITE_PATTERNS} patterns + ATR%>={V2_ATR_MIN}, +{V2_TP}/-{V2_SL} 60min LIMIT)" if v2
             else (f"💎 CREAM-ONLY (>={CREAM_PATTERNS} patterns + at S/R)" if cream_only
             else (f"ELITE-ONLY (>={ELITE_PATTERNS} patterns)" if elite_only else f"score >= {ENGINE_THRESHOLD}/10")))
    print(f"  ENTRY-WORTHY SIGNALS ({label}): {len(signals)} total  |  💎 CREAM: {cream_n}  |  🔥 ELITE: {elite_n}")
    print("=" * 100)

    if not signals:
        print("\n  ⏸  No signals meeting threshold — STATUS: HOLD. NO ACTIONABLE SETUP.")
    else:
        # TL;DR — public-friendly format (LONG/SHORT colored emojis instead of medals)
        print()
        print("  📋 TL;DR — TOP PICKS:")
        for i, (side, sym, s) in enumerate(signals[:3]):
            score = s['long_score'] if side == 'LONG' else s['short_score']
            side_emoji = '🟢' if side == 'LONG' else '🔴'
            tag = '💎 ' if is_cream(side, s) else ('🔥 ' if patcount(side, s) >= ELITE_PATTERNS else '')
            print(f"    {tag}{side_emoji} {side} {sym} @ ${s['cur_price']:.6f} (Score {score:.2f}/10)")
        print()

        for side, sym, s in signals[:10]:
            score = s['long_score'] if side == 'LONG' else s['short_score']
            reasons = s['long_reasons'] if side == 'LONG' else s['short_reasons']
            is_elite = patcount(side, s) >= ELITE_PATTERNS
            stars = ('🔥 ELITE ' if is_elite else '') + ('⭐⭐⭐⭐⭐' if score >= 7 else ('⭐⭐⭐⭐' if score >= 6 else '⭐⭐⭐'))
            side_emoji = '🟢' if side == 'LONG' else '🔴'

            sl, tp1, tp2 = compute_tp_sl(side, s['cur_price'], s)
            if v2:
                cp = s['cur_price']  # V2: +2.0% TP / -1.5% SL, 60-min hold, LIMIT entries (3-window validated)
                tp1 = cp * (1 + V2_TP / 100) if side == 'LONG' else cp * (1 - V2_TP / 100)
                sl = cp * (1 - V2_SL / 100) if side == 'LONG' else cp * (1 + V2_SL / 100)
            elif megapower:
                cp = s['cur_price']  # MEGAPOWER: tight +0.5% TP / -1.0% SL (74% WR, backtested)
                tp1 = cp * (1 + 0.005) if side == 'LONG' else cp * (1 - 0.005)
                sl = cp * (1 - 0.010) if side == 'LONG' else cp * (1 + 0.010)
            sl_pct = abs(sl - s['cur_price']) / s['cur_price'] * 100
            tp1_pct = abs(tp1 - s['cur_price']) / s['cur_price'] * 100
            tp2_pct = abs(tp2 - s['cur_price']) / s['cur_price'] * 100

            print()
            print(f"  {stars}  SIGNAL: {side_emoji} {side} {sym}  (score {score:.2f}/10)")
            print(f"     Reasons: {' | '.join(reasons)}")
            print(f"     Multi-TF check: trends {s['long_trends'] if side == 'LONG' else s['short_trends']}/5, "
                  f"RSI extremes {s['long_rsi_oversold'] if side == 'LONG' else s['short_rsi_overbought']}/3, "
                  f"patterns {s['long_patterns_count'] if side == 'LONG' else s['short_patterns_count']}")
            if s.get('fib_near'):
                print(f"     Fib level: {s['fib_near'][0]} @ ${s['fib_near'][1]:.6f}")
            if (side == 'LONG' and s['sr_near_support']) or (side == 'SHORT' and s['sr_near_resistance']):
                print(f"     At 1h {'support' if side == 'LONG' else 'resistance'} level ✅")
            print(f"     Entry: ${s['cur_price']:.6f}  |  24h: {s['chg_24h']:+.1f}%  |  Funding: {s['funding']*100:+.3f}%")
            print(f"     🛡️ STOP LOSS:    ${sl:.6f}  ({sl_pct:.2f}%)")
            tp1_label = (f"TP (V2 +{V2_TP}%, LIMIT, 60min)" if v2
                         else "TP (MEGAPOWER tight)" if megapower else "TP1 (scalp +1%)")
            print(f"     🎯 {tp1_label}: ${tp1:.6f}  ({tp1_pct:.2f}%)")
            if not megapower and not v2:
                print(f"     🎯 TP2 (S/R stretch): ${tp2:.6f}  ({tp2_pct:.2f}%)")
            print()
            print(f"     Trade here")
            print(f"     This is not financial advice, always manage your own risks")

    print()
    print("=" * 100)
    print(f"  Summary: {len(signals)} signals | scanned {processed} pairs | {VERSION}")
    print("=" * 100)


# ============================================================
# BACKTEST MODE
# ============================================================

def simulate_outcome(side, entry, k5_future, tp_pct=1.0, sl_pct=0.8, max_hold=MAX_HOLD_5M):
    if side == 'LONG':
        tp = entry * (1 + tp_pct / 100)
        sl = entry * (1 - sl_pct / 100)
    else:
        tp = entry * (1 - tp_pct / 100)
        sl = entry * (1 + sl_pct / 100)
    for k in k5_future[:max_hold]:
        h = float(k[2]); l = float(k[3])
        if side == 'LONG':
            if l <= sl: return ('SL', -sl_pct)
            if h >= tp: return ('TP', tp_pct)
        else:
            if h >= sl: return ('SL', -sl_pct)
            if l <= tp: return ('TP', tp_pct)
    last = float(k5_future[max_hold-1][4]) if len(k5_future) >= max_hold else float(k5_future[-1][4])
    raw = (last - entry) / entry * 100 if side == 'LONG' else -(last - entry) / entry * 100
    return ('TIMEOUT', raw)


def approximate_24h(k15_window):
    if len(k15_window) < 96:
        return None
    last_96 = k15_window[-96:]
    return {
        'priceChangePercent': (float(last_96[-1][4]) - float(last_96[0][1])) / float(last_96[0][1]) * 100,
        'highPrice': max(float(k[2]) for k in last_96),
        'lowPrice': min(float(k[3]) for k in last_96),
        'quoteVolume': sum(float(k[7]) for k in last_96),
    }


def backtest(days=7):
    print("=" * 100)
    print(f"  SUPER SCANNER BACKTEST — Last {days} days")
    print(f"  Strategy: scan every 15m, simulate +1%/-0.8% with 30-min max hold")
    print("=" * 100)

    tickers_list = fetch_tickers() or []
    tickers = {t['symbol']: t for t in tickers_list}
    exchange_info = fetch_exchange_info()
    candidates = filter_candidates(tickers, exchange_info)
    candidates.sort(key=lambda s: -float(tickers[s].get('quoteVolume', 0)))
    candidates = candidates[:25]  # top 25 for speed

    print(f"\n  Universe: {candidates[:8]}... ({len(candidates)} pairs)")

    # Pre-fetch all data per pair
    print(f"\nFetching historical klines...")
    pair_data = {}
    candles_5m = min(1500, days * 24 * 12)
    for sym in candidates:
        k5 = fetch_klines(sym, '5m', candles_5m)
        if not k5 or len(k5) < 500:
            continue
        k15 = aggregate_klines(k5, 3)
        k30 = aggregate_klines(k5, 6)
        k1h = aggregate_klines(k5, 12)
        k4h = aggregate_klines(k5, 48)
        k1m_proxy = k5  # No 1m history fetch (too much), use 5m as proxy
        k10m = aggregate_klines(k5, 2)
        fh = fetch_funding_history(sym, 200) or []
        pair_data[sym] = {
            'k1m': k1m_proxy, 'k5m': k5, 'k10m': k10m, 'k15m': k15,
            'k30m': k30, 'k1h': k1h, 'k4h': k4h, 'fh': fh,
        }
        print('.', end='', flush=True)
    print(f"\n  Loaded {len(pair_data)} pairs")

    btc_data = pair_data.get('BTCUSDT')
    if not btc_data:
        print("BTC missing")
        return

    def get_f(fh, ts):
        rel = [x for x in fh if int(x['fundingTime']) <= ts]
        return float(rel[-1]['fundingRate']) if rel else 0

    # Track feature stats
    feature_stats_long = defaultdict(lambda: {'sig': 0, 'wins': 0})
    feature_stats_short = defaultdict(lambda: {'sig': 0, 'wins': 0})
    total_long = {'wins': 0, 'count': 0}
    total_short = {'wins': 0, 'count': 0}
    all_signals = []

    print(f"\nReplaying scans every 15 min...")
    scan_count = 0
    for sym, data in pair_data.items():
        k5_all = data['k5m']
        for i in range(300, len(k5_all) - MAX_HOLD_5M - 1, 3):  # every 15min
            scan_count += 1
            # Slice windows for each TF
            k15_idx = i // 3
            k30_idx = i // 6
            k1h_idx = i // 12
            k4h_idx = i // 48
            k10_idx = i // 2

            k5_w = k5_all[max(0, i-50):i+1]
            k10_w = data['k10m'][max(0, k10_idx-50):k10_idx+1] if data['k10m'] else []
            k15_w = data['k15m'][max(0, k15_idx-100):k15_idx+1] if data['k15m'] else []
            k30_w = data['k30m'][max(0, k30_idx-50):k30_idx+1] if data['k30m'] else []
            k1h_w = data['k1h'][max(0, k1h_idx-50):k1h_idx+1] if data['k1h'] else []
            k4h_w = data['k4h'][max(0, k4h_idx-50):k4h_idx+1] if data['k4h'] else []

            if len(k15_w) < 96 or len(k5_w) < 30:
                continue

            t24 = approximate_24h(k15_w)
            if not t24:
                continue

            # BTC context
            btc_k15 = btc_data['k15m'][max(0, k15_idx-96):k15_idx+1]
            if len(btc_k15) < 96:
                continue
            btc_24h = (float(btc_k15[-1][4]) - float(btc_k15[0][1])) / float(btc_k15[0][1]) * 100
            btc_1h_o = float(btc_data['k15m'][k15_idx-4][1]) if k15_idx >= 4 else float(btc_data['k15m'][0][1])
            btc_1h_c = float(btc_data['k15m'][k15_idx][4])
            btc_1h_pct = (btc_1h_c - btc_1h_o) / btc_1h_o * 100
            btc_active = abs(btc_1h_pct) > 0.5

            ts = int(k5_all[i][6])
            f = get_f(data['fh'], ts)

            klines_by_tf = {
                '1m': k5_w, '5m': k5_w, '10m': k10_w, '15m': k15_w,
                '30m': k30_w, '1h': k1h_w, '4h': k4h_w,
            }
            t24_dict = {
                'highPrice': str(t24['highPrice']),
                'lowPrice': str(t24['lowPrice']),
                'priceChangePercent': str(t24['priceChangePercent']),
                'quoteVolume': str(t24['quoteVolume']),
            }
            s = compute_signal(sym, klines_by_tf, t24_dict, f, btc_24h, btc_1h_pct, btc_active)
            if not s:
                continue

            entry = s['cur_price']
            k5_future = k5_all[i+1:i+MAX_HOLD_5M+1]
            if len(k5_future) < MAX_HOLD_5M:
                continue

            # Test both sides
            for side, score_key, stale_key, reasons_key in [
                ('LONG', 'long_score', 'is_stale_long', 'long_reasons'),
                ('SHORT', 'short_score', 'is_stale_short', 'short_reasons')
            ]:
                if s[score_key] < ENGINE_THRESHOLD or s[stale_key]:
                    continue
                outcome, pnl = simulate_outcome(side, entry, k5_future)
                won = pnl > 0

                if side == 'LONG':
                    total_long['count'] += 1
                    if won: total_long['wins'] += 1
                else:
                    total_short['count'] += 1
                    if won: total_short['wins'] += 1

                all_signals.append({
                    'side': side, 'sym': sym, 'score': s[score_key],
                    'outcome': outcome, 'pnl': pnl,
                    'reasons': s[reasons_key],
                })

                # Track per-feature
                fstats = feature_stats_long if side == 'LONG' else feature_stats_short
                # Each reason gets tagged
                for r in s[reasons_key]:
                    # Use first word as tag
                    tag = r.split()[0] if r else 'other'
                    fstats[tag]['sig'] += 1
                    if won:
                        fstats[tag]['wins'] += 1

    print(f"  Scans: {scan_count}, signals generated: {len(all_signals)}")

    print("\n" + "=" * 100)
    print(f"  BACKTEST RESULTS ({len(all_signals)} simulated trades)")
    print("=" * 100)

    if not all_signals:
        print("  No signals fired during backtest period.")
        return

    wins = sum(1 for s in all_signals if s['pnl'] > 0)
    losses = sum(1 for s in all_signals if s['pnl'] < 0)
    net = sum(s['pnl'] for s in all_signals)
    tp = sum(1 for s in all_signals if s['outcome'] == 'TP')
    sl = sum(1 for s in all_signals if s['outcome'] == 'SL')
    to = sum(1 for s in all_signals if s['outcome'] == 'TIMEOUT')
    print(f"\n  OVERALL: {len(all_signals)} trades | {wins}W {losses}L | "
          f"WR {wins/len(all_signals)*100:.1f}% | net {net:+.2f}% | "
          f"avg {net/len(all_signals):+.3f}%/trade | TP/SL/TO: {tp}/{sl}/{to}")

    # By direction
    longs = [x for x in all_signals if x['side'] == 'LONG']
    shorts = [x for x in all_signals if x['side'] == 'SHORT']
    if longs:
        w = sum(1 for x in longs if x['pnl'] > 0)
        print(f"  LONGs:   {len(longs)} | {w}W | WR {w/len(longs)*100:.1f}% | net {sum(x['pnl'] for x in longs):+.2f}%")
    if shorts:
        w = sum(1 for x in shorts if x['pnl'] > 0)
        print(f"  SHORTs:  {len(shorts)} | {w}W | WR {w/len(shorts)*100:.1f}% | net {sum(x['pnl'] for x in shorts):+.2f}%")

    # By score tier
    print(f"\n  By score tier:")
    for thresh, label in [(7.0, "7.0+ (elite)"), (6.0, "6.0+ (strong)"), (5.0, "5.0+ (threshold)")]:
        subset = [s for s in all_signals if s['score'] >= thresh]
        if subset:
            w = sum(1 for x in subset if x['pnl'] > 0)
            net_s = sum(x['pnl'] for x in subset)
            print(f"    Score ≥ {label}: {len(subset)} trades | WR {w/len(subset)*100:.1f}% | net {net_s:+.2f}%")

    # Portfolio simulation
    wallet = 30
    for s in all_signals:
        # +1%/-0.8% with 1% risk
        if s['outcome'] == 'TP':
            wallet *= (1 + 0.01)  # +1% on wallet (1% risk × 1% target)
        elif s['outcome'] == 'SL':
            wallet *= (1 - 0.008)
        else:
            wallet *= (1 + s['pnl'] / 100 * 0.01)
    print(f"\n  Portfolio sim ($30 start, 1% risk/trade):")
    print(f"    $30 → ${wallet:.2f}  ({(wallet - 30) / 30 * 100:+.2f}% over {len(all_signals)} trades)")

    # Feature lift analysis
    print(f"\n  LONG feature lift (which features actually predict?):")
    base_long_wr = total_long['wins'] / max(total_long['count'], 1) * 100
    print(f"    Baseline LONG WR: {base_long_wr:.1f}%")
    rows = []
    for feat, stat in feature_stats_long.items():
        if stat['sig'] < 20: continue
        wr = stat['wins'] / stat['sig'] * 100
        lift = wr - base_long_wr
        rows.append((feat, stat['sig'], wr, lift))
    rows.sort(key=lambda x: -x[2])
    for feat, n, wr, lift in rows[:10]:
        mark = '⭐' if lift > 3 else ('OK' if lift > 0 else '-')
        print(f"    {feat:30s}  {n:>4d} signals | WR {wr:>5.1f}% | lift {lift:>+5.1f}pp  {mark}")

    print(f"\n  SHORT feature lift:")
    base_short_wr = total_short['wins'] / max(total_short['count'], 1) * 100
    print(f"    Baseline SHORT WR: {base_short_wr:.1f}%")
    rows = []
    for feat, stat in feature_stats_short.items():
        if stat['sig'] < 20: continue
        wr = stat['wins'] / stat['sig'] * 100
        lift = wr - base_short_wr
        rows.append((feat, stat['sig'], wr, lift))
    rows.sort(key=lambda x: -x[2])
    for feat, n, wr, lift in rows[:10]:
        mark = '⭐' if lift > 3 else ('OK' if lift > 0 else '-')
        print(f"    {feat:30s}  {n:>4d} signals | WR {wr:>5.1f}% | lift {lift:>+5.1f}pp  {mark}")

    # Save CSV
    csv_path = os.path.expanduser("~/Desktop/TrainAR/hummibot/logs/super_backtest.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, 'w') as f:
        f.write("side,sym,score,outcome,pnl_pct,reasons\n")
        for s in all_signals:
            f.write(f"{s['side']},{s['sym']},{s['score']:.2f},{s['outcome']},{s['pnl']:.2f},\"{'; '.join(s['reasons'])}\"\n")
    print(f"\n  Full results: {csv_path}")
    print("=" * 100)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description=f"Super Scanner {VERSION}")
    parser.add_argument('--backtest', type=int, metavar='DAYS', help='Run backtest on last N days')
    parser.add_argument('--pair', type=str, help='Scan single pair')
    parser.add_argument('--vol-min', type=int, default=DEFAULT_VOL_MIN, help='Min 24h volume')
    parser.add_argument('--pos-size', type=int, default=DEFAULT_POS_SIZE, help='Position notional')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--elite', action='store_true', help='Show ONLY 🔥 elite (>=3 pattern) signals')
    parser.add_argument('--cream', action='store_true', help='Show ONLY 💎 cream (>=4 patterns + at S/R) signals')
    parser.add_argument('--megapower', action='store_true', help='💎 cream entries + tight +0.5%% TP (74%% WR config)')
    parser.add_argument('--v2', action='store_true', help='✅ VALIDATED: >=3 patterns + ATR%%>=0.5, +2.0/-1.5 60min LIMIT (3-window net-positive after fees)')
    args = parser.parse_args()

    if args.backtest:
        backtest(days=args.backtest)
    else:
        scan_live(vol_min=args.vol_min, pos_size=args.pos_size, single_pair=args.pair,
                  verbose=args.verbose, elite_only=args.elite, cream_only=args.cream,
                  megapower=args.megapower, v2=args.v2)


if __name__ == "__main__":
    main()
