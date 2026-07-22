#!/usr/bin/env python3
"""
combo_bot.py — COMBINED long+short bot (regime-complementary). v2-style autonomous loop, hedge mode.

TWO LEGS in ONE process, scanning every 5 min on a shared account (Binance USDT-M, HEDGE mode so LONG & SHORT
coexist), sharing equity + a dynamic 5-slot pool:

  • LONG leg = the MAKER dip-buy bot (ALL its validated logic kept 1:1):
      anti-chop SMA100-rising · RS-rank 7d · froth-cap 60% · fresh-guard 6h · today-gate +2% · breadth-throttle · dispersion z-gate ·
      illiq-drop · struct4-SL · native algo STOP_MARKET + maker stop-LIMIT (MAKER_SL) · loss-streak breaker ·
      GTX maker dip entry −1.2% · TP +4% maker  ── PLUS NEW: trailing stop (activate @+1%, trail 0.5%).
  • SHORT leg = the SHORT-FADE busted-breakout bot (1h), maker-retest entry, native BUY-stop, trailing, hybrid sizing
      + 07-07: weak-coin gate loosened (coin7d -10%->0%) GATED BY a volume-spike(>=1.5x) + rejection-wick(>=0.5) quality
      filter on the busted-breakout bar (trapped-long-liquidation signature) -> ~2.5x more shorts at WR ~69-75% for balance.

WHY combined (validated 2026, 6 mo): the two legs are regime-complementary (corr ≈ −0.12). 50/50 with trailing =
0 losing months / 6, ret/stdev 13.4 (best of any config). The short leg covers the dip-buy's weak months & vice-versa.
⚠️ Honest: the dip-buy backtest is optimistic (live underperformed in June risk-off); combined magnitudes are rosy,
but the smoothing/complementarity is structural. Short leg = 1h-ONLY (15m was net-negative in every test).

DYNAMIC SLOTS: 5 shared, each leg max 3 (so always 3/2 or 2/3 — neither hogs). A free slot goes to the leg holding
FEWER positions (tie → alternate). Keeps both legs evenly fed.

Run:  python3 combo_bot.py            # PAPER (no orders)
      python3 combo_bot.py --live     # LIVE (real Binance USDT-M, hedge mode)
      python3 combo_bot.py --status   # print state JSON
"""
import os, sys, json, time, hmac, hashlib, math, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))   # this file's own dir — ensures super_scanner.py is found locally
for p in ("/root/v2bot", "/Users/macbook/Desktop/TrainAR/super-scanner-repo",
          "/Users/macbook/Desktop/TrainAR/grid-bot", _HERE):   # _HERE inserted LAST -> ends up first -> standalone-safe
    if os.path.isdir(p): sys.path.insert(0, p)
import super_scanner as ss   # zero external deps; lives next to combo_bot.py
try:
    sys.path.insert(0, "/Users/macbook/Desktop/TrainAR/hummibot")
    import v2_monitor as mon
    _tg = mon.telegram
except Exception:
    def _tg(*a, **k): pass
if "--paper" in __import__("sys").argv:
    _tg = lambda *a, **k: None   # PAPER: no Telegram (avoid confusing the live combo channel)

# ========================= CONFIG =========================
TOTAL_SLOTS = int(os.environ.get("TOTAL_SLOTS", "5"))   # shared across both legs; ALSO the sizing divisor (margin = equity/TOTAL_SLOTS). Env-gated 20-07 (Artem: 4 slots = conscious +25%-sizing choice; default 5 = byte-identical).
LEG_MAX     = 3            # neither leg may hold more than this (=> always 3/2 or 2/3)
LEV         = 3
WATCH_SIZE  = 150; WATCH_VOLMIN = 10_000_000   # shared volatile USDT-perp watchlist
POLL_SEC    = 2            # manage open positions every this many sec (native algo stops are primary; this is backstop)
REFRESH_SEC = 300         # scan signals every 5 min
TF = "1h"; BAR_SEC = 3600
REGIME_SMA  = 200          # BTC regime readout (info only)
FEE_MK = 0.02; FEE_TK = 0.05

# --- exit (both legs): TRAILING-stop to lock green (Artem's validated idea) — per-leg width L_TRAIL / S_TRAIL ---
L_TRAIL    = float(os.environ.get("L_TRAIL", "0.005"))         # LONG trail width (B1 09-07: 0.5%->0.3%). Tighter give-back lifts the FIRST-armed stop ABOVE the
                           # fee wall: first stop = (1+L_TRAIL_ACT)(1-L_TRAIL)-1 = +0.135% NET (clean green) vs 0.5/0.5%'s
                           # -0.066% NET (the fee-scratch = the EGLD case). Backtest 6H1+3H2: +9-12pp long return, ret/DD UP,
                           # SL count + maxDD UNCHANGED, MORE +3% runners banked (40->43/22->26), holds 5/6 IS + 3/3 OOS at
                           # BNB fees; our live-period +12% ($3.65->$4.10 on $50). Rollback one line: L_TRAIL = 0.005.
S_TRAIL    = float(os.environ.get("S_TRAIL", "0.005"))         # SHORT trail width (env-tunable 14-07)
# GREEN-LOCK 09-07 (per-leg trail activation). LONG green-lock re-validated: 6-win H1 + 3-win OOS-H2, TP=4 config:
# +6-7pp WR (54->61 / 58->64), +4-10 more wins, cost -9..-10pp total ret (banks sub-1% green Artem was closing by hand).
# LONG-ONLY — SHORT green-lock is REFUTED (WR 68->41%), so S_TRAIL_ACT stays 1%.
L_TRAIL_ACT = float(os.environ.get("L_TRAIL_ACT", "0.010"))        # LONG: activate trail at +0.5% (green-lock ON). set 0.01 to revert.
S_TRAIL_ACT = float(os.environ.get("S_TRAIL_ACT", "0.0075"))         # SHORT: activate trail at +1% (unchanged; short green-lock refuted)
# FAT-TAIL CHANDELIER FADE EXIT (C) — ride a cascade with a WIDE 1h-ATR chandelier instead of the fixed R-multiple TP.
# ⛔ BUILT but REFUTED by backtest (grid-bot/fade_chandelier_ab.py, 64 real fades 29-06..13-07): chandelier = −$13..18 vs
# fixed's ~breakeven, robust in BOTH halves, ALL K (2.5/3/4). Fat tail is REAL (chandelier top-3 winners ~7× bigger) but
# too RARE — only ~12/64 fades cascade; the other ~49 mean-revert and the chandelier gives back the small green into the
# SL (−$0.20/trade vs fixed −$0.01). Confirms the short-green-lock refutation: widening the short trail = strictly worse.
# ⇒ STAYS OFF (S_CHAND=0). Future path for the tail is NOT a wider exit on ALL fades — it's a TIGHTER (liq-gated) ENTRY,
# then maybe a chandelier on THAT sub-population, which needs its own backtest once enough liq-gated fills exist.
S_CHAND     = os.environ.get("S_CHAND", "0") not in ("0", "false", "False", "no")
S_CHAND_K   = float(os.environ.get("S_CHAND_K", "3.0"))    # chandelier width in 1h-ATRs (wide = keep the cascade fat tail)
S_CHAND_ARM = float(os.environ.get("S_CHAND_ARM", "0.010")) # arm the chandelier once the fade is +this in favor (price down 1%)
# ANTI-MARTINGALE PYRAMID ON FADE (E) — add to a WINNING fade, aggregate stop -> breakeven, max 1 add. SAFE (validator
# proved: can't lose beyond the initial 1R; never blows up — unlike the -92%DD add-to-LOSERS martingale). BUT ⛔ backtest-
# REFUTED (grid-bot/fade_pyramid_ab.py, 64 real fades): Δ -$0.16 both halves, worse under slip. Only 7/64 (11%) fades reach
# +1R (our tight 0.75%/0.5% trail closes them first), of those only 2 +EV. STRUCTURAL: pyramid needs the winner to RUN, the
# tight trail is built to STOP it running — mutually contradictory. Adding at +1R = -EV under this exit. STAYS OFF
# (S_PYRAMID=0). (Would only pay paired with the wide chandelier S_CHAND, which is itself refuted.) Rollback: S_PYRAMID=0.
S_PYRAMID   = os.environ.get("S_PYRAMID", "0") not in ("0", "false", "False", "no")
S_PYR_R     = float(os.environ.get("S_PYR_R", "1.0"))    # add once the fade is +this*R in profit
S_PYR_FRAC  = float(os.environ.get("S_PYR_FRAC", "0.5")) # add this fraction of base qty (<= half base = ruin guard)
# EQUITY-KELLY (E, part 2) — the bot ALREADY sizes every slot = equity/TOTAL_SLOTS and auto-scales with realized equity via
# live_balance() each tick = portfolio-level anti-martingale (size up after wins, down after losses; can't blow to zero).
# E_SIZE_FRAC is a fractional-Kelly dial ON TOP (0.5 = half-Kelly conservative, 1.0 = full equal-slot default). Applied to
# the fade + event legs (snap/trend); dip/mom keep their own validated sizing untouched. Rollback: E_SIZE_FRAC=1.0.
E_SIZE_FRAC = float(os.environ.get("E_SIZE_FRAC", "1.0"))
# OI-CAPITULATION gate 09-07 (WATCHED/EXPERIMENTAL — literature-backed, but n=16 real-tape, NOT yet significant).
# Theory (Glassnode LPOC + Gate/XT): buy the FLUSH not the crowd — OI(contracts) FALLING during the dip = long
# capitulation = local bottom = better dip-buy; OI RISING into a dip = crowded/short-stacking = worse. Real-tape (78
# OI-covered longs): gate OI-drop<-0.5% -> -$2.15 to +$0.81, WR 45->56, but keeps only 20% of dips (n=16). Use as a
# SOFT size-throttle or a watched hard gate; validate over weeks. 0.0 = OFF (default; needs more data to trust).
L_OI_CAPIT = 0.0           # HARD gate: require 30min OI-contracts slope <= this to arm a LONG (e.g. -0.005). 0 = off.
L_OI_CAPIT_SOFT = float(os.environ.get("L_OI_CAPIT_SOFT", "0"))      # SOFT (WATCHED 09-07): size a non-capitulation dip x this (0.5), full size on OI-drop<-0.5%.
                           # Real-tape (78 OI-covered longs, $50): +$0.79 -> +$2.27 (+$1.48); keeps ALL frequency, bounded
                           # downside, fail-safe (API error -> full size). Logs L-OI-SOFT counterfactual per arm. 0 = off.
# FUNDING-PERCENTILE SIZE-UP (09-07, audit#3, WATCHED ON): a LONG on a coin whose 30d funding PERCENTILE <= L_FUND_SIZEUP
# (bottom-decile = crowded-short = squeeze fuel) is sized x L_FUND_BOOST. Validated: bottom-funding longs OUTPERFORM in
# H1 AND H2-OOS (bottom-decile WR 72/73% vs top 63/69%); in-harness ×1.5 = +40pp(H1)/+26pp(H2) return, ret/DD improves in
# BOTH, trade-COUNT UNCHANGED (pure SIZE multiplier, NOT a gate). Fail-safe: funding API error -> no boost (base size).
# ⚠️ WATCHED — judge over WEEKS; it AMPLIFIES the crowded-short subset (bigger $ both ways), doesn't add green count.
# ROLLBACK (one line): L_FUND_SIZEUP = 0.0  (or systemd Environment=L_FUND_SIZEUP=0).
L_FUND_SIZEUP = float(os.environ.get("L_FUND_SIZEUP", "0.10"))   # funding-percentile threshold (bottom decile). 0 = OFF.
L_FUND_BOOST  = float(os.environ.get("L_FUND_BOOST", "1.5"))     # size multiplier on a qualifying (low-funding) long.
# LIQUIDATION SIZE-UP (09-07, edge #1, WATCHED): size a long dip x L_LIQ_BOOST when a MODERATE recent long-liquidation
# flush preceded it (forced selling exhausting = bounce). Backtest on our real fills (Coinalyze free hist): moderate
# flush (0<liq20<~median) WR 54% vs 44% no-flush, +$2.98, HELD IN BOTH HALVES; BIG flush = worse (naive size-up REFUTED).
# Coinalyze REST (works on this VPS; forceOrder WS is network-blocked, lesson #43). FALLBACK: Coinalyze error/timeout/no-key
# -> None -> no boost, trade proceeds normally. SIZE-UP ONLY, never a gate, never reduces/skips a trade. Rollback: L_LIQ_SIZEUP=0.
L_LIQ_SIZEUP  = float(os.environ.get("L_LIQ_SIZEUP", "1"))       # 1 = ON (watched), 0 = OFF. Inert unless COINALYZE_KEY is set.
L_LIQ_BOOST   = float(os.environ.get("L_LIQ_BOOST", "1.5"))      # size multiplier on a moderate-flush long.
L_LIQ_HI      = float(os.environ.get("L_LIQ_HI", "800"))         # moderate flush = 0 < long-liq(last 20min, USD) < this; >= this = big flush (base size).
COINALYZE_KEY = os.environ.get("COINALYZE_KEY", "")             # free Coinalyze API key (set as systemd Environment; absent -> liq inert).
# BID-REPRICE (09-07, edge #2): on a SHALLOW post-only reject (market <= this% past the -1.2% limit), re-place the maker
# buy ONCE at the current bid (still maker) instead of walking away. Deep rejects (>this% = fast knife) stay skipped
# (validated: chasing them = -$14.57). Backtest: +$0.84/10d, +51 recovered fills, no knives. ADDS trades only. Rollback: L_BID_REPRICE=0.
L_BID_REPRICE = float(os.environ.get("L_BID_REPRICE", "0.3"))   # shallow-overshoot threshold %; 0 = OFF.

# --- LONG leg (maker dip-buy) params (validated config, kept 1:1) ---
L_DIP       = 1.2          # % below last close for the maker GTX LIMIT BUY
L_TP        = float(os.environ.get("L_TP", "3.0"))          # % take-profit (maker) — the trailing rides above this once activated. 06-07: 3.0->4.0.
                           # Validated: kline 6-window sweep +27% ($193 vs $152, 5/5 windows) + prior 189-fill 1m replay
                           # +62%$$. Mechanism (measured): does NOT cut green COUNT (160->157) — same greens, FEWER reds
                           # (96->90), WR 62->64%, +29% $/green (trail already protects green; raising the ceiling lets
                           # winners run instead of capping at +3%). Opposite of the scalp-exit green-count trap.
L_SL        = 1.0          # % base stop
L_STRUCT_SL_BARS = 4       # structural SL: min(low, last N bars)*0.9985 capped at -SL%
L_TREND_LEN = 100; L_SLOPE_MIN = 1.0; L_SLOPE_BARS = 24
L_RS_LOOKBACK = 168; L_MOM_CAP = float(os.environ.get("L_MOM_CAP", "30.0")); L_TODAY_MIN = 2.0
L_FRESH_BARS = 6; L_FRESH_MIN = -4.0
L_LAST1_MIN = -1.0         # LAST-HOUR gate: reject if the just-closed 1h candle is redder than this (29-06 research: ret/DD 4.7->13.8 H1 / 1.9->11.5 OOS, instant-stops -60%)
L_TTL_BARS = 12            # cancel an unfilled dip limit after this many hours
L_RUNAWAY_DROP = float(os.environ.get("L_RUNAWAY_DROP", "4.0"))   # RUNAWAY-DROP: cancel a PENDING dip limit if price ran > this% ABOVE the limit (dip won't fill → free the slot for a fresher coin). ⬇5→4 (Artem, 13-07): the validated 89-arm data showed 0/8 runaways EVER returned to fill even at 5% → tightening to 4% frees the slot sooner with ~zero good-fills lost (a coin ≥4% above the −1.2% dip almost never dips back). 0=off.
L_MAKER_SL  = True         # MAKER-SL (04-07, R3.2 port): primary stop = native stop-LIMIT resting AT the SL price (fill floored at SL,
                           # no slip) + taker STOP_MARKET backstop L_MAKER_SL_GAP% lower (gaps/downtime) + software poll only below the
                           # backstop pre-trail. Backtest: beats base ALL 6 windows (avg ret/DD 13.9->24.5). False = old taker-only stop.
L_MAKER_SL_GAP = 0.3       # backstop distance % below SL
# --- MAKER-TRAIL-EXIT A/B (07-07): fees eat ~98% of gross (-$2.10/3d). The TRAIL exit is a TAKER mkt_sell (0.05%).
# On a TRAIL (profit-lock) exit, the A/B arm rests a post-only reduce-only MAKER sell (0.02%) just above px and waits
# L_MT_WAIT s; if it fills we save the taker fee, else taker fallback. Only TRAIL (never SL — a stop must exit fast).
# 50/50 split by fill-time parity so the other half stays the taker baseline to compare. Native backstop stays armed
# during the wait (catastrophe net). Env-gated + DEFAULT OFF -> deploying the code is inert until L_MAKER_TRAIL=1. ⚠️ LIT
# showed the risk (a fast reversal misses the maker -> taker-fallback exits ~L_MT_WAIT s lower); the A/B measures if the
# fee saving on bounces beats the miss cost on continuations.
L_MAKER_TRAIL = os.environ.get("L_MAKER_TRAIL", "") in ("1", "true", "True", "yes")
L_MT_WAIT = float(os.environ.get("L_MT_WAIT", "3.0"))   # seconds to wait for the maker exit before taker fallback
L_MT_EDGE = 0.0001         # rest the maker sell just above px (~1 tick) — consult: want FILL-RATE not price improvement; the $ is all in the fee delta
# --- PASSIVE stop-LIMIT (07-07 consult gem): the native maker stop-LIMIT (fix #7) fills TAKER ~81% because its limit is
# set marketable (==trigger). Rest the limit L_MSL_TICKS ticks ABOVE the trigger -> fills MAKER on the bounce (the ~81%),
# backstop unchanged for the no-bounce crash tail. Strictly better (better fill px + maker fee, zero added risk). ON by default.
L_MSL_PASSIVE = True
L_MSL_TICKS = 2            # ticks above the SL trigger to rest the stop-LIMIT passively (maker)
L_GRIND_VETO = True        # G-VETO (04-07, fix #8): cancel a PENDING dip-limit when price APPROACHES it SLOWLY (grind, not flush).
                           # Fast liquidation flushes (>=L_GV_DROP% fall within L_GV_WIN sec) blast through and fill as usual;
                           # slow grinds (informed selling) get cancelled before they fill. Live tape: fast fills WR 57% +19.0%,
                           # grind fills WR 34% -14.2%; all-trades $50 replay: $55.19 -> $59.45. MoonBot Drops-class primitive
                           # (their rec: 2%/600s), SSRN cascade anatomy. False = off (instant rollback).
L_GV_WIN  = 300            # velocity lookback (seconds)
L_GV_DROP = 1.2            # required fall % from the window high down to the limit to count as a FLUSH
L_GV_BAND = 0.3            # approach band: veto evaluates once price is within this % above the limit
L_BREADTH = (4, 2, 1)      # breadth throttle (counts long qualifiers)
L_Z_GATE = 0.0             # DISPERSION Z-GATE (06-07): arm a dip only on coins whose 7d-RS is clearly ABOVE the candidate
                           # pack (momentum z-score vs the pool >= this; needs >=3 candidates to judge dispersion). 0 = off.
                           # Validated: 8-window $50 sweep on the TP=4% base = +$6/50 (+3.4%), PLATEAU 0.3-0.6, 2 OOS windows
                           # neutral, trade count UP 325->330 (improves SELECTION, not volume), green 209->214, WR 64->65%.
                           # Real-tape tertile agrees (top xsec +$2.97 vs bottom -$0.31). NOT the refuted absolute-RS floor —
                           # this is RELATIVE-to-the-current-pack + conditional (inert when <3 candidates or zero dispersion).
                           # rs-rank tightening was redundant (breadth already caps arms); the dispersion form is the edge.
L_LOSS_STREAK_N = 3; L_COOLDOWN_SEC = 1*3600; L_BREAKER_RESET = True
L_ILLIQ_DROP = True
# LOWER-WICK VETO (07-07, WATCHED): skip arming a dip if the recent down-move left NO buyer-defense — mean lower-wick
# fraction over the last L_WICK_BARS closed 1m bars < L_WICK_VETO (a sellers-only falling knife closes on its lows, wick~0).
# Real-fill replay on 257 fills: +$5.89/+145% (ceiling), 4/4 OOS windows, crash-exclusion passed (dropped non-crash WR33%);
# 3 independent judges = DEPLOY-AS-WATCHED @ 0.15. Rollback = L_WICK_VETO=0. Judge over WEEKS; watch the veto-log counterfactuals.
L_WICK_VETO = float(os.environ.get("L_WICK_VETO", "0.15")); L_WICK_BARS = 5

# --- SHORT leg (short-fade) params (validated) ---
_S_TF_BPD = {"15m": 96, "30m": 48, "1h": 24, "2h": 12, "4h": 6}   # bars/day per timeframe
S_SCAN_TFS = [(t, _S_TF_BPD[t]) for t in os.environ.get("S_SCAN_TFS", "1h").split(",") if t in _S_TF_BPD] or [("1h", 24)]
                            # env-tunable (14-07): e.g. S_SCAN_TFS="1h,15m" to scan both. Default 1h-only (15m historically net-neg — Artem's freq call).
S_N = 20; S_K = 3; S_PUMP_MIN = 1.0; S_R = 3.0
S_COIN7D_CAP = float(os.environ.get("S_COIN7D_CAP", "-10.0")); S_TREND_DAYS = 7   # env-tunable (14-07): set high (e.g. 1000) to DISABLE the weak-coin gate = fade any coin
S_COIN7D_MIN = float(os.environ.get("S_COIN7D_MIN", "-1e9"))   # c7d FLOOR (16-07, Artem): only fade coins with 7d-change >= MIN. Default -1e9 = OFF (no floor). LIVE=0 → fade only FLAT-to-STRONG coins (analysis: c7d<0 losers avg -4.5% vs c7d>0 winners +9.8% → +$0.99/76%WR on survivors). Fires only when S_COIN7D_MIN <= c7d <= S_COIN7D_CAP.
S_SL_FIXED = float(os.environ.get("S_SL_FIXED", "0")); S_TP_FIXED = float(os.environ.get("S_TP_FIXED", "0"))   # fixed fade SL/TP fraction (0=off → structural stop / R-target). Artem 14-07.
                                       # filter below (bare loosen = -$22.8 squeeze disaster; filtered = +$11.4). Gets ~2.5x more
                                       # shorts at WR ~69-75% for long/short balance. Also drives rd_c7d's un-weaken threshold.
S_VOL_MULT = 0   # SHORT volume-spike filter: the busted-breakout bar's volume must be >= this x avg(20). 0 = off.
S_WICK_FRAC = 0  # SHORT rejection-wick filter: the breakout bar's upper-wick fraction must be >= this. 0 = off. ESSENTIAL squeeze-
                   # killer when the coin7d gate is loosened (vol-only without wick = -$22.8; vol+wick = +$11.4 combo +4%, WR 69-75%,
                   # 5/7 windows better; real-tape: high-vol shorts WR91% vs low-vol 50%). Validated: 7-window $50, short DD dilutes at
                   # full-bot level (short is a small -0.12-corr slice). W7 OOS slightly negative -> WATCHED EXPERIMENT (rollback: cap=-10).
S_FILL_WIN_BARS = 6; S_ENTRY_MAX_AGE_MIN = 20; S_MAX_RETEST_DIST = 0.06
S_RUNAWAY_DROP = float(os.environ.get("S_RUNAWAY_DROP", "4.0"))   # FADE runaway (NEW 13-07): cancel a PENDING fade retest if price ran > this% from the retest entry (either way — the drop happened without us / or the pump kept running = thesis dead) → free the slot. Mirrors L_RUNAWAY_DROP; fade had only a time deadline before. 0=off.
S_RISK_FRAC = float(os.environ.get("S_RISK_FRAC", "0.01"))   # SHORT hybrid per-trade risk cap. Default 0.01; live-config uses 0.015 (fade is under its Kelly — fade+sizing agents). notional = min(equal_slot, equity*S_RISK_FRAC/stop_dist). Caps the
                            # per-short-trade $-loss at ~1% of account (structural short stops can be +8% wide; equal-slot alone
                            # = up to 12% single-trade loss — validated 9-win: WTL 12.4%->1.0%, ret/DD 0.14->0.53. Wide-stop shorts
                            # carry the BEST edge (WR 59%->83% by stop-dist) so we KEEP them, just size them down. Long leg unchanged.
# SIZING (29-06): EQUAL SLOT SHARE — every position (long OR short) uses margin = equity/TOTAL_SLOTS (=equity/5),
# notional = margin*LEV. So all 5 slots split the budget evenly and ALWAYS fit (5×equity/5 = full equity margin);
# no leg balloons. (Replaced the short's old 1%-risk sizing, which could take 32% of the account on a tight stop.)
RISK_FRAC = 0.01           # (legacy, unused for sizing now — kept for reference)

# --- FADE-QUALITY FIXES (15-07, env-gated, DEFAULT-OFF; byte-identical until enabled) ---------------------------------
# Source: grid-bot/gladiator_fade_analysis_15jul.md. Losers were HOT coins in strong uptrends (high c7d / big pump) +
# re-faded coins + loss clusters. FIX1 (c7d ceiling) needs no code — it's just an env value on the existing S_COIN7D_CAP.
S_PUMP_MAX = float(os.environ.get("S_PUMP_MAX", "0"))                        # FIX2: skip the fade if the TRIGGERING pump > this% (a rocket, not a bust). 0 = off.
S_LOSS_STREAK_N = int(os.environ.get("S_LOSS_STREAK_N", "0"))               # FIX3: pause NEW fades after this many CONSECUTIVE real fade SL stops. 0 = off.
S_STREAK_COOLDOWN_SEC = float(os.environ.get("S_STREAK_COOLDOWN_SEC", "3600"))  # FIX3: how long the fade-arm pause lasts once the streak trips.
S_COIN_COOLDOWN_SEC = float(os.environ.get("S_COIN_COOLDOWN_SEC", "0"))     # FIX4: skip re-fading a coin within this many sec of ITS last real SL. 0 = off.
# FIX5 BOOK-WIDE circuit-breaker (16-07, Artem): pause ALL fades after S_BOOK_BREAKER_N real stops within a ROLLING window (TIME-based, NOT
# consecutive — a win sprinkled inside a squeeze cluster does NOT reset it). Faster+broader than FIX3 (which needs 3 CONSECUTIVE + a win resets).
# Motivation: 16-07 live took 5 stops in 63min (a correlated market squeeze) — FIX3 only tripped after 3 and couldn't stop already-open legs.
S_BOOK_BREAKER_N = int(os.environ.get("S_BOOK_BREAKER_N", "0"))             # stops within the window that trip the book-wide pause. 0 = OFF (default).
S_BOOK_WINDOW_SEC = float(os.environ.get("S_BOOK_WINDOW_SEC", "1800"))      # rolling window for counting stops (default 30m).
S_BOOK_COOLDOWN_SEC = float(os.environ.get("S_BOOK_COOLDOWN_SEC", "3600"))  # how long ALL fades pause once the book-breaker trips (default 1h).
# FIX5b BREAKEVEN-TIGHTEN on a book-breaker trip (16-07, Artem): when the book-wide breaker fires, also de-risk the OPEN fades — move each
# to breakeven. RED (px>=entry) closes now at market ~breakeven; GREEN (px<entry) keeps its TP and rests the stop at breakeven (a reverting
# winner can still run). Booked with tag "BE" so these squeeze-guard exits do NOT feed the streak/book counters (no cascade). 0 = OFF.
S_BOOK_BE_TIGHTEN = int(os.environ.get("S_BOOK_BE_TIGHTEN", "0"))
# ===== GREENER (21-07): S1 LIQ-AT-FILL GATE — candidate #1 of the WR70 study (84.4% WR / +0.136%/tr on 64 real fills w/ measured
# slippage; the 12-07 arm-time gate is DEAD on the full tape — the catalyst must be alive AT THE FILL, not at the signal).
# Mechanics: (a) arming a fade requires trailing-30min LONG-liq >= GR_FILL_LIQ_MIN (FAIL-CLOSED: data None -> no arm — 2 of 4
# slots depend on liq data, silence must not fire blind fades); (b) a PENDING retest is CANCELLED the minute liq30 drops below
# the floor (the resting limit may only fill while the catalyst is alive = reproduces the studied population). 0 = OFF (inert).
GR_FILL_LIQ_MIN = float(os.environ.get("GR_FILL_LIQ_MIN", "0"))
# ===== GREENER E5 SQUEEZE-FADE (ported from clever-bot 21-07; 4/4 OOS +0.297%/tr on the long tape, ~48% WR BY DESIGN — time-exit
# engine, judged on expectancy not WR): taker market SELL into a SHORT-liq flush spike (shorts liquidating = price spiking up =
# fade it), exit = TIME (SQF_HOLD_MIN) or the WIDE catastrophe stop (+SQF_SL). NO TP / NO trail (validated: tight exits are all
# net-negative on this engine). Port notes: U1/ledger/allocator stripped (clever-bot brain, not needed); PUMP CHECK RUNS FIRST
# (free Binance klines) and the Coinalyze liq call runs ONLY for coins already pumping -> ~0-10 calls/min, inside the 40/min
# free tier (clever-bot's 150-coin liq sweep would 429). DEFAULT OFF (SQF_ENABLED=0) = byte-identical.
SQF_ENABLED       = os.environ.get("SQF_ENABLED", "0") not in ("0", "false", "False", "no")
SQF_S5_MIN        = float(os.environ.get("SQF_S5_MIN", "12000"))    # short-liq $ over trailing 5min >= this
SQF_DOM           = float(os.environ.get("SQF_DOM", "2.0"))         # short-liq dominates: S15 > SQF_DOM * L15
SQF_PUMP_MIN      = float(os.environ.get("SQF_PUMP_MIN", "1.0"))    # prior-15m pump >= this % (checked FIRST, before any liq call)
SQF_HOLD_MIN      = int(os.environ.get("SQF_HOLD_MIN", "60"))       # TIME exit (minutes). No take-profit.
SQF_SL            = float(os.environ.get("SQF_SL", "0.04"))         # catastrophe stop ONLY (+4%)
SQF_CAP           = int(os.environ.get("SQF_CAP", "0"))             # max concurrent squeeze-fades. 0 = engine takes no slots.
SQF_COOLDOWN_MIN  = int(os.environ.get("SQF_COOLDOWN_MIN", "60"))   # one squeeze-fade per coin per this many minutes
SQF_SCAN_MAX      = int(os.environ.get("SQF_SCAN_MAX", "12"))       # liq-call burst cap per 1m scan (top pumps first) — Coinalyze free tier safety
# ===== E5 MAKER-ENTRY HYBRID (22-07 variation study + 2 audits): limit SELL above market instead of instant taker.
# Better entry + maker fee, worth ~+$0.05-0.13/trade under BOTH fill rules; adverse selection is real (the ~9-18% signals
# that never reach the limit are ~90%-WR instant reversals) but net of skipped winners the hybrid still wins, and the
# taker FALLBACK on TTL keeps every trade (execution improvement, not a signal change). SQF_MAKER=0 (default) = byte-identical.
SQF_MAKER         = os.environ.get("SQF_MAKER", "0") not in ("0", "false", "False", "no")
SQF_MAKER_BUMP    = float(os.environ.get("SQF_MAKER_BUMP", "0.003"))   # limit at signal px * (1+this)
SQF_MAKER_TTL_SEC = int(os.environ.get("SQF_MAKER_TTL_SEC", "300"))    # unfilled after this -> cancel (race-safe) + taker market fallback
# ===== E5 TRAIL (22-07, Artem's order, tuned on the 12 real night paths — grid + corrected sim, fill-bar contamination excluded):
# arm the trail the moment favorable excursion reaches SQF_TRAIL_ACT (all 12 night trades were green >=0.3% early — Artem's thesis,
# verified); width is TWO-PHASE: tight SQF_TRAIL_EARLY for the first SQF_TRAIL_EARLY_MIN minutes (banks instant spikes — Artem's
# +$0.48-in-seconds case), then SQF_TRAIL after (0.75% flat beat 0.5/0.6 monotonically — squeeze reversion is noisy, grinders need
# room). TIME60 + the +4% catastrophe stop REMAIN as backstops underneath. Night replay: -$1.88 actual -> ~+$0.28 under this config
# (2 of 4 catastrophes become scratches; the 2 instant-death stops had no post-fill green and are unfixable by any trail).
# SQF_TRAIL_ACT=0 (default) = trail OFF = byte-identical.
SQF_TRAIL_ACT       = float(os.environ.get("SQF_TRAIL_ACT", "0"))
SQF_TRAIL           = float(os.environ.get("SQF_TRAIL", "0.0075"))
SQF_TRAIL_EARLY     = float(os.environ.get("SQF_TRAIL_EARLY", "0.003"))
SQF_TRAIL_EARLY_MIN = int(os.environ.get("SQF_TRAIL_EARLY_MIN", "10"))
# FIX-A (22-07, Artem): NATIVE exchange trailing stop. The software 2s-poll trail books at whatever price the poll catches —
# on violent squeeze bounces that overshot the stop by 0.08-0.31% on ALL THREE armed exits of the trail era (ACE: stop
# 0.098715, booked 0.0990179 = peak +$0.33 turned into −$0.04). Fix: on arm, REST a real STOP_MARKET BUY at the trail level;
# ratchet it via cancel/replace, throttled to >=SQF_TRAIL_REPLACE_BP improvement (API churn guard). The +4% catastrophe stop
# stays resting the whole time (covers the cancel->place gap); the software poll remains as a belt-and-braces fallback.
SQF_TRAIL_NATIVE     = os.environ.get("SQF_TRAIL_NATIVE", "1") not in ("0", "false", "False", "no")
SQF_TRAIL_REPLACE_BP = float(os.environ.get("SQF_TRAIL_REPLACE_BP", "0.0005"))   # min stop move (frac) to justify cancel/replace
# BE-FLOOR (22-07, Artem: "після зелені — ніколи в червоне"): once ARMED, the trail stop may never sit above
# entry*(1-SQF_TRAIL_BE) — shallow-green bounces exit ~breakeven instead of locking a red (the UNI case: peak +0.42%
# with base width 0.75% put the stop ABOVE entry -> -$0.20). Deep moves are untouched (the ratcheted trail level goes
# below the floor and takes over). Cost, stated honestly: green->wiggle-above-entry->THEN-run trades (PUMP-class) get
# cut ~flat. 0 = OFF (byte-identical).
SQF_TRAIL_BE = float(os.environ.get("SQF_TRAIL_BE", "0"))

# ========================= MOMENTUM LEG (v4 config A "Drive") — ported from momentum_engine.py =========================
# THIRD strategy merged into the shared pool: LONG-ONLY multi-setup momentum on a MOVERS universe (full perp pool ranked
# by recency-weighted RS vs BTC, $3M liq floor). Backtest-validated (5×60d $50 3x): +110-146%, 4/5 GREEN, DD~33% (the
# accepted price of a fat-tail book — ruin prevented by the 15% margin buffer + tight stops + the TOTAL-LONG=3 cap, NOT by
# clipping winners). It is a 2nd UNCORRELATED edge class the dip-buy structurally can't catch (opp_cost_test.py: combo's
# dip-wait MISSED +$20.44/$50 of momentum on our own tape). See ULTRA_BUILD_PLAN.md.  ⚠️ magnitudes UNPROVEN live.
M_ENABLED   = os.environ.get("M_ENABLED", "0") not in ("0", "false", "False", "no")   # ⛔ DEFAULT OFF in soldier: momentum is backtest-REFUTED on our tape (−$24.32/7%WR full-tape, chop-loser). Set M_ENABLED=1 only in a trend window. (ultra had it ON as its reason-to-exist; soldier does not.)
M_POOL = 300; M_N_UNI = 120; M_LIQ_FLOOR = 3e6   # movers universe: rank top-300-by-vol pool by RS, $3M median-7d-$vol floor
M_RS_LONG   = 90           # long only top-decile RS (percentile within the movers universe)
M_BASE_MAX  = 0.06         # "tight base" = 24-bar range / mean <= this (6%)
M_SPREAD_MIN = 0.02        # 1h EMA7-vs-EMA99 spread floor (established uptrend)
M_ER_MIN    = 0.30         # Kaufman efficiency-ratio floor (directional, not chop)
M_EXT_CAP   = 8.0          # skip if price extended > this many ATR above EMA25 (climax guard)
M_VMULT     = 2.0          # break/ignite bar volume must be >= this x avg
M_TB_LONG   = 0.58         # taker-buy delta SOFT booster (rank tilt), NEVER a gate (validated: soft>gate, 4/5 vs 2/5)
M_STOP_CAP  = float(os.environ.get("M_STOP_CAP", "0.015")); M_SL_ATR = 2.0   # tight R = closest of {structural low, this%, 2xATR}
                           # PHASE-2 (10-07): 2.0% -> 1.5% (mom_exit_sweep2.py): tighter stop = smaller R -> more winners arm &
                           # reach the trail -> +$32 vs +$25 AND far better concentration robustness (top-2-removed +$8 vs +$0.5).
                           # Rollback: M_STOP_CAP=0.02. (Actual stop = CLOSEST of this%, 2xATR, structural — structural often tighter.)
# PHASE-2 PYRAMID (10-07, mom_exit_sweep2 + quant agent): add M_PYRAMID_FRAC of base size once a runner is >= +M_PYRAMID_R·R,
# and raise the aggregate stop to BREAKEVEN (the whole enlarged stack can no longer lose). Concentrates size into proving
# tail-candidates = expectancy-positive on an outlier-carried book (+$43 vs +$32). ⚠️ RISK: gap-through on the bigger position
# -> guarded by (a) add only after +2R with stop>=BE, (b) add<=half base, (c) the existing TOTAL-LONG<=3 heat cap. Env-toggle.
M_PYRAMID   = os.environ.get("M_PYRAMID", "1") not in ("0", "false", "False", "no")   # add to winners at +2R. Rollback: M_PYRAMID=0.
M_PYRAMID_R = 2.0          # add once the runner reaches +this·R
M_PYRAMID_FRAC = 0.5       # add this fraction of the base qty (<= half base, per the ruin-guard)
M_ARM_R     = 1.0          # arm the ride (breakeven->trail) at +1R
M_TRAIL_K   = float(os.environ.get("M_TRAIL_K", "4.0"))   # 🎯 chandelier trail width in 1h-ATRs. WIDE = keeps the fat tail.
                           # TUNED 10-07 on 286 REAL missed-momentum opps (mom_exit_sweep2.py, honest taker+chase fills):
                           # k≈4.0 = +$25/$50 (robust +$22 @0.35%-slip, skew 5.0, 2nd-uncorrelated-edge) vs the OLD TW~2.5 = +$1.9
                           # (13× better). Judged by expectancy+skew NOT win-rate (10% WR, outlier-carried). NEVER tighten when a
                           # runner stalls (the old TW->1.0 rule CLIPPED winners that later resume — the single worst EV leak).
                           # Further validated upside (phase-2, gated): tighter stop -> +$32, +pyramid@+2R -> +$43. Rollback: M_TRAIL_K=2.5.
M_IG_MOM    = 0.035        # ignition = +3.5% over 2 bars
M_SETUPS    = ["break", "retest", "ignite"]
M_CHASE     = {"break": 0.0015, "ignite": 0.0025}   # IOC marketable-limit chase caps (retest is a passive GTX maker limit)
M_MAKER_SL_GAP = 0.003     # STOP_MARKET backstop 0.3% below the maker-SL trigger
M_FT_WIN    = 2            # follow-through window (bars) before arm — no +0.5R by bar 2 => fakeout exit
M_MAX_HOLD_H = 72          # time-stop: flat a pre-arm position hanging this many hours
M_UNIVERSE_SEC = 300       # re-rank the movers universe every 5 min
M_DAILY_HARD = 0.12        # crash net: flatten non-armed momentum legs if the UTC day is -12%
M_PEND_TTL_MIN = {"break": 30, "retest": 45, "ignite": 15}   # cancel an unfilled entry after this many minutes
M_PEND_RUNAWAY = 0.010     # cancel a pending if price ran > 1% past the entry (won't fill)
M_FEE_SIDE  = 0.00045      # per-side fee estimate for momentum paper accounting (LIVE books from real balance)
M_FETCH_WORKERS = int(os.environ.get("M_FETCH_WORKERS", "12"))   # SCAN-PERF: parallel kline fetches for the movers scan +
                           # per-bar feature warm (was serial ~144s at pool 300 -> ~10-20s). I/O-bound, safe at 12 (Binance
                           # weight budget 2400/min; a full scan ≈600 weight). Set 1 to force serial (debug/rate-limit).

# ========================= 🔒 ULTRA SLOT ALLOCATION (2 agents + Artem, confirmed — DO NOT re-open) =========================
# Shared pool of TOTAL_SLOTS(5) + per-strategy CAPS (soft, not hard reservations) + one LOAD-BEARING total-long cap.
# A free slot goes to whoever FIRES (emergent regime-awareness: momentum self-idles in chop because no runners arm; fade
# self-fires in chop; dip fires on pullbacks). NO computed regime detector (built & FALSIFIED). SHIP STATIC.
M_MOM_CAP        = int(os.environ.get("M_MOM_CAP", "2"))       # momentum-long cap (env for paper momentum-only tests)
M_DIP_CAP        = int(os.environ.get("M_DIP_CAP", "2"))       # dip-long cap (ballast; set 0 in a paper momentum-only run)
S_FADE_CAP       = int(os.environ.get("S_FADE_CAP", "2"))      # short-fade cap (set 0 in a paper momentum-only run)
M_TOTAL_LONG_CAP = int(os.environ.get("M_TOTAL_LONG_CAP", "3"))# 🔒 dip+mom COMBINED long cap — THE ruin guard (real money only). dip & momentum are BOTH long crypto beta
                           # (positively correlated: 76-93% of long stops fire together in a flush). Without this, risk-on
                           # could stack 2 mom + 2 dip = 4 correlated longs at 3x -> a -25% cluster ≈ liquidation on $56.

# ========================= SHORT-LIQ CATALYST GATE (validated — short_liq_test.py) =========================
# The short-fade only makes money when a pump fails WITH a real down-catalyst; a pump with no catalyst keeps going = the
# squeeze that stops us out. On our 38 real shorts: LONG-LIQ flush present (>=$552 trapped-longs liquidating in last 20min)
# = 100% WR (10/10, +$4.49); NO long-liq (pure fade) = 63% WR, -$0.24 (the squeezes). GATE: require long-liq20 >= S_LIQ_MIN
# to arm a fade. Reuses liq_flush() (sums Coinalyze `l` = long-liqs; for a SHORT we WANT it HIGH = trapped longs flushing =
# down catalyst). FAIL-SAFE: Coinalyze down (None) -> STILL FIRE + log (a data outage must not silently kill the short leg).
# ⚠️ n=10 for the 100% bucket = SMALL -> WATCHED + one-line rollback. DEFAULT OFF (0); set S_LIQ_MIN=552 to arm the gate.
S_LIQ_MIN = float(os.environ.get("S_LIQ_MIN", "552"))   # min long-liq20 USD to arm a fade. 0 = OFF. 552 = validated catalyst.

LIVE = "--live" in sys.argv; STATUS = "--status" in sys.argv
DAEMON_DIR = (os.environ.get("COMBO_DATA_DIR")          # override (e.g. tests) so they never touch the live log/state
              or ("/root/v2bot/data/combobot" if os.path.isdir("/root/v2bot") else
                  os.path.expanduser("~/Library/Application Support/trainar-combobot")))
STATE = os.path.join(DAEMON_DIR, "combo_state.json")
LOG = os.path.join(DAEMON_DIR, "logs", "combo_trades.log")
STATUS_FILE = os.path.join(DAEMON_DIR, "logs", "combo_status.txt")
# EXIT TELEMETRY = FOREVER-history (intended-vs-real exit fills + MAE + R-multiple). It MUST survive a clean
# reset, so it lives OUTSIDE DAEMON_DIR/logs (which the deploy reset wipes) — a sibling dir like data/oilogger.
# Tests (COMBO_DATA_DIR set) keep it under the isolated test dir so they never touch live telemetry.
_TEL_OVERRIDE = os.environ.get("COMBO_DATA_DIR")
TEL_DIR = (os.path.join(_TEL_OVERRIDE, "telemetry") if _TEL_OVERRIDE
           else os.path.join(os.path.dirname(DAEMON_DIR), "combotel"))   # VPS: /root/v2bot/data/combotel (reset-safe)
EXIT_TEL = os.path.join(TEL_DIR, "exit_telemetry.csv")   # 07-07 booking-fix + 09-07 R-multiple; reset-safe forever-history
BINANCE_ENV = ("/root/v2bot/data/secrets/binance.env" if os.path.exists("/root/v2bot/data/secrets/binance.env")
               else os.path.expanduser("~/Library/Application Support/trainar-gridbot/secrets/binance.env"))
B = "https://fapi.binance.com"

def now(): return time.time()
def ts(): return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
def log(m):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    open(LOG, "a").write(f"[{ts()}] {m}\n"); print(f"[{ts()}] {m}", flush=True)
def fmt(p): return f"{p:.6g}"

# ========================= STATE =========================
def load_state():
    base = {"equity": 0.0, "long": [], "short": [], "mom": [], "snap": [], "trend": [], "squeeze": [], "realized": 0.0, "wins": 0, "losses": 0,
            "loss_streak": 0, "paused_until": 0, "last_sig": {}, "slot_turn": "short", "lbreadth": 0, "sfires": 0,
            "fade_streak": 0, "fade_paused_until": 0, "fade_sl_ts": {}, "fade_pause_logged": 0, "fade_cd_logged": {},
            "day": "", "day_eq": 0.0, "muni_ts": 0}
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            for k, v in base.items(): s.setdefault(k, v)
            return s
        except Exception: pass
    return base
def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True); json.dump(s, open(STATE, "w"), indent=2)

# ========================= EXCHANGE SPECS =========================
_SPECS = {}; _SPECS_TS = 0; _OFF = None; HEDGE = False
def specs(sym):
    global _SPECS, _SPECS_TS
    if not _SPECS or now()-_SPECS_TS > 6*3600:
        info = ss.fetch_exchange_info() or {}; d = {}
        for s in info.get("symbols", []):
            step = minq = minn = tick = None
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE": step = float(f["stepSize"]); minq = float(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL": minn = float(f["notional"])
                elif f["filterType"] == "PRICE_FILTER": tick = float(f["tickSize"])
            d[s["symbol"]] = {"step": step, "minq": minq, "minn": (minn or 5.0), "tick": tick,
                              "qp": int(s.get("quantityPrecision", 3)), "pp": int(s.get("pricePrecision", 2))}
        if d: _SPECS = d; _SPECS_TS = now()
    return _SPECS.get(sym)
def round_tick(px, sp): return round(math.floor(px/sp["tick"])*sp["tick"], sp["pp"])
def calc_qty(sym, px, notional):
    sp = specs(sym)
    if not sp or not sp["step"] or px <= 0: return None, sp
    qty = round(math.floor((notional/px)/sp["step"])*sp["step"], sp["qp"])
    if qty < sp["minq"] or qty*px < sp["minn"]: return None, sp
    return qty, sp

# ========================= SIGNED REST =========================
def _keys():
    k = s = ""
    if os.path.exists(BINANCE_ENV):
        for line in open(BINANCE_ENV):
            if line.startswith("BINANCE_KEY="): k = line.split("=", 1)[1].strip()
            elif line.startswith("BINANCE_SECRET="): s = line.split("=", 1)[1].strip()
    return k, s
def _offset():
    global _OFF
    if _OFF is None:
        try: _OFF = json.loads(urllib.request.urlopen(f"{B}/fapi/v1/time", timeout=10).read())["serverTime"]-int(now()*1000)
        except Exception: _OFF = 0
    return _OFF
def _signed(path, params, method="POST"):
    key, secret = _keys(); params["timestamp"] = int(now()*1000)+_offset(); params["recvWindow"] = 5000
    q = urllib.parse.urlencode(params); sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{B}{path}?{q}&signature={sig}", method=method, headers={"X-MBX-APIKEY": key})
    try: return json.loads(urllib.request.urlopen(req, timeout=12).read())
    except urllib.error.HTTPError as e: return {"_error": e.code, "_body": e.read().decode()}
def detect_hedge():
    global HEDGE
    for _ in range(3):
        r = _signed("/fapi/v1/positionSide/dual", {}, "GET")
        if isinstance(r, dict) and "dualSidePosition" in r: HEDGE = bool(r["dualSidePosition"]); return
        time.sleep(1)
    HEDGE = True; log("detect_hedge failed 3x -> default HEDGE=True")
def set_lev(sym): return _signed("/fapi/v1/leverage", {"symbol": sym, "leverage": LEV})

# --- LONG order helpers (positionSide LONG) ---
def limit_buy(sym, qty, px):                        # GTX maker dip entry (dip leg) / retest maker entry (momentum leg)
    p = {"symbol": sym, "side": "BUY", "type": "LIMIT", "timeInForce": "GTX", "quantity": qty, "price": px}
    if HEDGE: p["positionSide"] = "LONG"
    return _signed("/fapi/v1/order", p)
def ioc_buy(sym, qty, px):                          # momentum leg: IOC marketable-limit (chase-capped taker for break/ignite)
    p = {"symbol": sym, "side": "BUY", "type": "LIMIT", "timeInForce": "IOC", "quantity": qty, "price": px}
    if HEDGE: p["positionSide"] = "LONG"
    return _signed("/fapi/v1/order", p)
def limit_sell_tp(sym, qty, px):                    # maker TP, closes LONG
    p = {"symbol": sym, "side": "SELL", "type": "LIMIT", "timeInForce": "GTX", "quantity": qty, "price": px}
    if HEDGE: p["positionSide"] = "LONG"
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/order", p)
def mkt_sell(sym, qty):                             # taker close LONG (stop backstop / trailing)
    p = {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": qty}
    if HEDGE: p["positionSide"] = "LONG"
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/order", p)
def stop_limit_sell(sym, qty, trig, limit_px):      # MAKER-SL primary: native stop-LIMIT (posts a LIMIT SELL at limit_px on trigger)
    p = {"symbol": sym, "side": "SELL", "type": "STOP", "algoType": "CONDITIONAL",
         "triggerPrice": trig, "price": limit_px, "quantity": qty, "timeInForce": "GTC"}
    if HEDGE: p["positionSide"] = "LONG"
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/algoOrder", p)
def stop_market_sell(sym, trig):                    # native algo stop for LONG (SELL STOP_MARKET, closePosition)
    p = {"symbol": sym, "side": "SELL", "type": "STOP_MARKET", "algoType": "CONDITIONAL",
         "triggerPrice": trig, "closePosition": "true"}
    if HEDGE: p["positionSide"] = "LONG"
    return _signed("/fapi/v1/algoOrder", p)

# --- SHORT order helpers (positionSide SHORT) ---
def market_short(sym, qty):
    p = {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": qty}
    if HEDGE: p["positionSide"] = "SHORT"
    return _signed("/fapi/v1/order", p)
def limit_short_open(sym, qty, px):                 # GTC maker SELL above market (retest entry)
    p = {"symbol": sym, "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": qty, "price": px}
    if HEDGE: p["positionSide"] = "SHORT"
    return _signed("/fapi/v1/order", p)
def limit_buy_tp(sym, qty, px):                     # maker TP, closes SHORT
    p = {"symbol": sym, "side": "BUY", "type": "LIMIT", "timeInForce": "GTX", "quantity": qty, "price": px}
    if HEDGE: p["positionSide"] = "SHORT"
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/order", p)
def mkt_buy_close(sym, qty):                        # taker close SHORT (stop backstop / trailing)
    p = {"symbol": sym, "side": "BUY", "type": "MARKET", "quantity": qty}
    if HEDGE: p["positionSide"] = "SHORT"
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/order", p)
def stop_market_buy(sym, trig):                     # native algo stop for SHORT (BUY STOP_MARKET, closePosition)
    p = {"symbol": sym, "side": "BUY", "type": "STOP_MARKET", "algoType": "CONDITIONAL",
         "triggerPrice": trig, "closePosition": "true"}
    if HEDGE: p["positionSide"] = "SHORT"
    return _signed("/fapi/v1/algoOrder", p)
def stop_market_buy_rq(sym, qty, trig):             # FIX-A3: TRAIL stop for SHORT = qty-based (NOT closePosition) — Binance allows only
    p = {"symbol": sym, "side": "BUY", "type": "STOP_MARKET", "algoType": "CONDITIONAL",   # ONE closePosition STOP_MARKET per side, so
         "triggerPrice": trig, "quantity": qty}                                            # the trail must coexist with the catastrophe
    if HEDGE: p["positionSide"] = "SHORT"           # hedge mode: positionSide implies reduce; reduceOnly param would be rejected
    else: p["reduceOnly"] = "true"
    return _signed("/fapi/v1/algoOrder", p)

def cancel_algo(sym, algo_id): return _signed("/fapi/v1/algoOrder", {"symbol": sym, "algoId": algo_id}, "DELETE")
def algo_open_ids(sym):
    r = _signed("/fapi/v1/openAlgoOrders", {}, "GET")
    return {str(o.get("algoId")) for o in r if isinstance(o, dict) and o.get("symbol") == sym} if isinstance(r, list) else set()
def order_status(sym, oid): return _signed("/fapi/v1/order", {"symbol": sym, "orderId": oid}, "GET")
def cancel(sym, oid): return _signed("/fapi/v1/order", {"symbol": sym, "orderId": oid}, "DELETE")
RCF_RETRIES = 5            # booking-fix: userTrades query attempts (Binance index can lag a fill >0.6s — LIT missed at 3x/0.3s)
RCF_GAP = 0.4              # seconds between attempts (closes are rare -> <=1.6s total is fine; the position is already
                           # closed on the exchange by a native order, so other positions' native stops are unaffected)
def real_close_fill(sym, side, since_ms):
    """READ-ONLY booking-fix (07-07): qty-weighted avg fill price of the reduce-only CLOSE fills (realizedPnl!=0) for
    `sym` on the closing `side` ('SELL' closes a long, 'BUY' closes a short) since `since_ms`. Lets us book an exit at
    the ACTUAL exchange fill instead of the 2s-poll/bounced-mark px — which under/over-reported P&L (e.g. a wick stop
    that filled at -1.18% got booked at the bounced mark -0.34%). Returns avg fill px, or None on any failure (caller
    falls back to the design px). RETRIES because on a FAST close (e.g. LDO stopped 3s before the query) Binance's
    userTrades index lags the fill -> the first query returns empty -> without the retry the whole point (the deep-tail
    correction) silently falls back to the bounced mark. weight 5/call x <=3 = <=15/close; closes are rare -> no ban risk."""
    if not LIVE: return None
    if since_ms and since_ms < 1e12: since_ms = since_ms * 1000   # UNITS GUARD (12-07, synced from combo)
    since_ms = max(int(since_ms or 0), int((now()-6*3600)*1000))
    for attempt in range(RCF_RETRIES):
        try:
            tr = _signed("/fapi/v1/userTrades", {"symbol": sym, "startTime": int(since_ms), "limit": 50}, "GET")
        except Exception:
            tr = None
        if isinstance(tr, list):
            f = [t for t in tr if t.get("side") == side and float(t.get("realizedPnl", 0) or 0) != 0]
            q = sum(float(t["qty"]) for t in f)
            if q > 0:
                return sum(float(t["price"])*float(t["qty"]) for t in f) / q
        if attempt < RCF_RETRIES - 1:
            time.sleep(RCF_GAP)   # wait for Binance to index the just-happened fill, then re-query
    return None
def exit_tel(sym, leg, tag, pos, intended_px, real_px):
    """Append one row of intended-vs-real exit telemetry + MAE (max adverse excursion) — forever-history to power a
    future flush-exit threshold AND to MEASURE its winner-clip cost (how deep a trade dipped before it recovered)."""
    try:
        entry = pos["entry"]
        os.makedirs(os.path.dirname(EXIT_TEL), exist_ok=True)
        new = not os.path.exists(EXIT_TEL)
        rp = real_px if real_px else ""
        i_ret = ((intended_px-entry)/entry*100) if leg == "long" else ((entry-intended_px)/entry*100)
        r_ret = (((real_px-entry)/entry*100) if leg == "long" else ((entry-real_px)/entry*100)) if real_px else ""
        drift = (r_ret - i_ret) if real_px else ""
        # MAE = worst excursion vs entry while open (long: deepest low; short: highest high). Negative = how far offside it went.
        ext = pos.get("minlow") if leg == "long" else pos.get("maxadv")
        mae = ((ext-entry)/entry*100) if (leg == "long" and ext) else (((entry-ext)/entry*100) if (leg == "short" and ext) else "")
        # R-MULTIPLE (audit north-star): initial risk = entry->ORIGINAL-stop distance % (pos["sl"]/["stop"] are the
        # INITIAL stops — trailing raises a LOCAL dstop, never mutates them). R = realized(or intended) return / initial risk.
        init_sl = pos.get("sl") if leg == "long" else pos.get("stop")
        irisk = ((entry-init_sl)/entry*100) if (leg == "long" and init_sl) else (((init_sl-entry)/entry*100) if (leg == "short" and init_sl) else None)
        eff_ret = r_ret if real_px else i_ret
        rmult = (eff_ret/irisk) if (irisk and irisk > 0) else ""
        with open(EXIT_TEL, "a") as f:
            if new: f.write("ts_iso,symbol,leg,tag,entry,intended_px,real_px,intended_ret_pct,real_ret_pct,drift_pct,mae_pct,init_risk_pct,r_multiple\n")
            f.write(f"{ts()},{sym},{leg},{tag},{entry},{intended_px},{rp},{i_ret:.4f},"
                    f"{(r_ret if real_px else '')},{(f'{drift:.4f}' if real_px else '')},{(f'{mae:.4f}' if mae != '' else '')},"
                    f"{(f'{irisk:.4f}' if irisk else '')},{(f'{rmult:.4f}' if rmult != '' else '')}\n")
    except Exception:
        pass
def live_balance():
    r = _signed("/fapi/v2/balance", {}, "GET")
    if isinstance(r, list):
        for b in r:
            if b["asset"] == "USDT": return float(b["balance"])
    return None
def live_positions():
    r = _signed("/fapi/v2/positionRisk", {}, "GET")
    if not isinstance(r, list): return None   # RISK-1: API error (429/418/5xx) -> None (DISTINCT from genuinely-empty {}) so callers skip mgmt this cycle, never phantom-close
    out = {}
    for p in r:
        k = (p["symbol"], p.get("positionSide", "BOTH")); out[k] = out.get(k, 0.0)+float(p.get("positionAmt", 0))
    return out

# ========================= MARKET DATA / SIGNALS =========================
def price_now(sym):
    try: d = ss.fetch_klines(sym, "1m", limit=1); return float(d[-1][4]) if d else None
    except Exception: return None

def trend_ref(sym):
    """LONG signal (maker dip-buy): (uptrend_and_rising?, ref_close, momentum%, illiq). Anti-chop+fresh+today gates."""
    need = max(L_TREND_LEN+L_SLOPE_BARS, L_RS_LOOKBACK) + 2
    k = ss.fetch_klines(sym, TF, limit=need) or []
    if len(k) < need: return False, None, 0.0, 0.0
    k = k[:-1]
    C = [float(x[4]) for x in k]
    ref = C[-1]; sma = sum(C[-L_TREND_LEN:])/L_TREND_LEN
    sma_prev = sum(C[-L_TREND_LEN-L_SLOPE_BARS:-L_SLOPE_BARS])/L_TREND_LEN
    slope = (sma - sma_prev)/sma_prev*100 if sma_prev else 0
    mom = (C[-1]/C[-1-L_RS_LOOKBACK]-1)*100 if len(C) > L_RS_LOOKBACK else 0.0
    fresh = (C[-1]/C[-1-L_FRESH_BARS]-1)*100 if len(C) > L_FRESH_BARS else 0.0
    last1 = (C[-1]/C[-2]-1)*100 if len(C) > 1 else 0.0   # just-closed hour return (LAST-HOUR gate — don't arm into an active flush)
    day_open = None
    for x in range(len(k)-1, -1, -1):
        if (int(k[x][0])//3600000) % 24 == 0: day_open = float(k[x][1]); break
    today = (C[-1]/day_open-1)*100 if day_open else 0.0
    illiq = 0.0
    if len(k) >= 49:
        acc = 0.0; n = 0
        for x in range(len(k)-48, len(k)):
            qv = float(k[x][7]) if len(k[x]) > 7 else float(k[x][5])*C[x]
            if qv > 0 and C[x-1] > 0: acc += abs(C[x]/C[x-1]-1)/qv; n += 1
        illiq = acc/n if n else 0.0
    up = (ref > sma and slope >= L_SLOPE_MIN and fresh >= L_FRESH_MIN and today >= L_TODAY_MIN and last1 >= L_LAST1_MIN)
    return up, ref, mom, illiq

def swing_low(sym, bars):
    k = ss.fetch_klines(sym, TF, limit=bars+2) or []
    if len(k) < bars+1: return None
    k = k[:-1]
    return min(float(x[3]) for x in k[-bars:])

def short_signal(sym, tf="1h", bpd=24):
    """SHORT signal (busted breakout): fires only when the RECLAIM is the most-recent closed candle.
    Returns (fire?, candle_ts, LEVEL_px, stop_px, pump%, coin7d%)."""
    trend_bars = S_TREND_DAYS*bpd
    need = S_N + S_K + trend_bars + 3
    k = ss.fetch_klines(sym, tf, limit=need) or []
    if len(k) < S_N + trend_bars + 2: return (False, None, None, None, 0.0, 0.0)
    k = k[:-1]
    H = [float(x[2]) for x in k]; C = [float(x[4]) for x in k]; Tms = [int(x[0]) for x in k]
    L = [float(x[3]) for x in k]; O = [float(x[1]) for x in k]; V = [float(x[5]) for x in k]
    last = len(k) - 1
    if last < trend_bars: return (False, None, None, None, 0.0, 0.0)
    coin7d = (C[last]/C[last-trend_bars]-1)*100
    if coin7d > S_COIN7D_CAP or coin7d < S_COIN7D_MIN: return (False, Tms[last], C[last], None, 0.0, coin7d)
    for i in range(last-1, max(S_N, last-S_K)-1, -1):
        lvl = max(H[i-S_N:i])
        if H[i] <= lvl: continue
        if S_VOL_MULT and i >= 20:                       # VOLUME-SPIKE filter on the busted-breakout bar
            vavg = sum(V[i-20:i])/20
            if not (vavg > 0 and V[i] >= S_VOL_MULT*vavg): continue
        if S_WICK_FRAC:                                  # REJECTION-WICK filter (squeeze-killer for the loosened gate)
            rng = H[i]-L[i]; uw = (H[i]-max(O[i], C[i]))/rng if rng > 0 else 0.0
            if uw < S_WICK_FRAC: continue
        first_reclaim = None; flush = H[i]
        for j in range(i+1, min(i+1+S_K, len(k))):
            flush = max(flush, H[j])
            if C[j] < lvl: first_reclaim = j; break
        if first_reclaim == last:
            pump = (flush-lvl)/lvl*100
            # FIX2 (15-07): pump CEILING — skip a fade whose triggering pump is too big (a rocket, not a bust). Off when S_PUMP_MAX<=0.
            if pump >= S_PUMP_MIN and (S_PUMP_MAX <= 0 or pump <= S_PUMP_MAX):
                return (True, Tms[last], lvl, flush, pump, coin7d)
            return (False, Tms[last], lvl, None, pump, coin7d)
    return (False, Tms[last] if k else None, C[last] if k else None, None, 0.0, coin7d)

_REG_TS = 0; _REG_ON = True
def btc_risk_on():
    global _REG_TS, _REG_ON
    if now()-_REG_TS < 3600: return _REG_ON
    try:
        k = ss.fetch_klines("BTCUSDT", "1d", limit=REGIME_SMA+3) or []
        k = k[:-1]; C = [float(x[4]) for x in k[-REGIME_SMA:]]
        if len(C) >= REGIME_SMA: _REG_ON = C[-1] > sum(C)/REGIME_SMA; _REG_TS = now()
    except Exception: pass
    return _REG_ON
def build_watchlist():
    try:
        tk = {t["symbol"]: t for t in (ss.fetch_tickers() or [])}
        meta = ss._build_symbol_meta(ss.fetch_exchange_info())
        pool = [s for s in tk if s.endswith("USDT") and s not in ss.STOCK_TICKERS and ss.is_crypto(s, meta)
                and float(tk[s].get("quoteVolume", 0)) >= WATCH_VOLMIN]
        pool.sort(key=lambda s: -float(tk[s].get("quoteVolume", 0)))
        return pool[:WATCH_SIZE] or ["BTCUSDT", "ETHUSDT"]
    except Exception as e:
        log(f"watchlist failed ({e})"); return ["BTCUSDT", "ETHUSDT"]

# ========================= 🔒 ULTRA SLOT ALLOCATION (strat-aware: dip / fade / mom) =========================
# Shared pool of TOTAL_SLOTS(5). Per-strat caps (mom<=M_MOM_CAP, dip<=M_DIP_CAP, fade<=S_FADE_CAP) + the LOAD-BEARING
# TOTAL-LONG cap (dip+mom combined <= M_TOTAL_LONG_CAP). A free slot goes to whoever FIRES — no fair-share turn (that was
# the OLD 2-leg design); the caps + the correlated-long guard do the shaping. leg: "long"=dip, "short"=fade, "mom"=momentum.
def held_long(st): return {p["coin"] for p in st["long"]}
def held_short(st): return {p["coin"] for p in st["short"]}
def held_mom(st): return {p["coin"] for p in st.get("mom", [])}
def held_snap(st): return {p["coin"] for p in st.get("snap", [])}
def held_trend(st): return {p["coin"] for p in st.get("trend", [])}
def held_squeeze(st): return {p["coin"] for p in st.get("squeeze", [])}
def held_all(st):
    """GREENER cross-leg same-coin exclusion: one symbol may live in ONE leg at a time (a same-coin L/S pair in hedge
    mode = pure fee bleed; two shorts on one coin = one doubled bet)."""
    return held_long(st) | held_short(st) | held_mom(st) | held_snap(st) | held_trend(st) | held_squeeze(st)
def n_open(st): return len(st["long"]) + len(st["short"]) + len(st.get("mom", [])) + len(st.get("snap", [])) + len(st.get("trend", [])) + len(st.get("squeeze", []))
def n_long_total(st): return len(st["long"]) + len(st.get("mom", [])) + len(st.get("snap", [])) + len(st.get("trend", []))   # dip+mom+snap+trend = correlated long beta
def can_open(st, leg):
    """True iff `leg` may take a slot: total < 5, this strat < its cap, and (for the long strats) dip+mom+snap+trend < ruin cap."""
    if n_open(st) >= TOTAL_SLOTS: return False                    # pool full
    if leg == "long":                                            # DIP
        if len(st["long"]) >= M_DIP_CAP: return False
        if n_long_total(st) >= M_TOTAL_LONG_CAP: return False     # 🔒 ruin guard
    elif leg == "mom":                                           # MOMENTUM
        if len(st.get("mom", [])) >= M_MOM_CAP: return False
        if n_long_total(st) >= M_TOTAL_LONG_CAP: return False     # 🔒 ruin guard
    elif leg == "snap":                                         # LIQ-SNAPBACK (A)
        if len(st.get("snap", [])) >= SNAP_CAP: return False
        if n_long_total(st) >= M_TOTAL_LONG_CAP: return False     # 🔒 ruin guard (snap = long beta)
    elif leg == "trend":                                        # TREND (D)
        if len(st.get("trend", [])) >= T_CAP: return False
        if n_long_total(st) >= M_TOTAL_LONG_CAP: return False     # 🔒 ruin guard (trend = long beta)
    elif leg == "short":                                        # FADE
        if len(st["short"]) >= S_FADE_CAP: return False
    elif leg == "squeeze":                                      # GREENER E5 SQUEEZE-FADE (short — no long ruin guard)
        if len(st.get("squeeze", [])) >= SQF_CAP: return False
    return True
def took_slot(st, leg):
    st["slot_turn"] = leg   # legacy field (kept for status/back-compat); allocation no longer uses a fair-share turn

def z_gate_filter(cands, z):
    """DISPERSION Z-GATE: keep only candidates whose momentum (cands[i][0]) z-score vs the pool is >= z.
    Needs >=3 candidates to judge dispersion; inert on zero dispersion. Mirrors maker_backtest z_gate 1:1."""
    if not z or len(cands) < 3: return cands
    moms = [c[0] for c in cands]; m = sum(moms)/len(moms)
    sd = (sum((v-m)**2 for v in moms)/len(moms))**0.5
    if sd <= 0: return cands
    return [c for c in cands if (c[0]-m)/sd >= z]   # may be empty -> arm nothing this cycle (wait for a clear leader; = validated backtest behavior)

def wick_ok(sym):
    """LOWER-WICK VETO: True to allow arming. Mean lower-wick fraction over the last L_WICK_BARS closed 1m bars must be
    >= L_WICK_VETO (buyer-defense present). A sellers-only knife (candles close on their lows, wick~0) FAILS -> skip.
    On any data glitch -> True (never veto on a fetch error). Logs the veto with the wick + limit for counterfactual review."""
    if not L_WICK_VETO: return True
    k = ss.fetch_klines(sym, "1m", limit=L_WICK_BARS+2) or []
    if len(k) < L_WICK_BARS+1: return True
    k = k[:-1][-L_WICK_BARS:]                              # drop the forming bar; take the last N CLOSED 1m bars
    ws = []
    for x in k:
        o, h, l, c = float(x[1]), float(x[2]), float(x[3]), float(x[4]); rng = h-l
        ws.append((min(o, c)-l)/rng if rng > 0 else 0.5)
    mw = sum(ws)/len(ws)
    if mw < L_WICK_VETO:
        log(f"L-WICK-VETO {sym}: lower-wick {mw:.2f} < {L_WICK_VETO} (sellers-only knife) — skip arm")
        return False
    return True

def oi_slope(sym):
    """30-min OI-in-CONTRACTS slope for the OI-CAPITULATION gate. Binance /futures/data/openInterestHist (5m, last 7
    = 30min). Returns (o_last-o_first)/o_first, or None on failure. NEGATIVE = OI falling = long-capitulation flush =
    the literature-backed better dip-buy (Glassnode LPOC); POSITIVE = crowded/short-stacking into the dip = worse.
    Uses CONTRACTS (sumOpenInterest), NOT notional (notional is price-contaminated — the mis-measurement we fixed)."""
    try:
        d = ss.http_get(f"{ss.BASE_URL}/futures/data/openInterestHist?symbol={sym}&period=5m&limit=7")
        if not isinstance(d, list) or len(d) < 2: return None
        o0 = float(d[0]["sumOpenInterest"]); o1 = float(d[-1]["sumOpenInterest"])
        return (o1-o0)/o0 if o0 else None
    except Exception:
        return None

def fund_pct(sym):
    """Trailing-30d PERCENTILE (0..1) of the CURRENT funding rate for the FUNDING-SIZE-UP lever. Binance
    /fapi/v1/fundingRate (8h, ~3/day) last 90 = ~30d. Returns fraction of the 30d window BELOW the latest rate,
    or None on failure. LOW percentile (bottom decile) = funding at a 30d low = crowded shorts = squeeze fuel."""
    try:
        d = ss.http_get(f"{ss.BASE_URL}/fapi/v1/fundingRate?symbol={sym}&limit=90")
        if not isinstance(d, list) or len(d) < 10: return None
        rates = [float(x["fundingRate"]) for x in d]; cur = rates[-1]
        return sum(1 for v in rates if v < cur) / len(rates)
    except Exception:
        return None

# ========================= FUNDING-PERCENTILE OVERLAY (B) — free two-sided SIZE tilt (validated bottom-decile long OOS) =========================
# Pure size MULTIPLIER, never a gate (adds zero trades, zero new fees). LONG side (snap/leg-A): bottom-decile funding
# (crowded shorts = squeeze fuel) -> boost. SHORT side (fade): top-decile funding (crowded longs = unwind fuel) -> boost.
# Fail-safe to 1.0 on any funding-API error (never penalize). The dip leg keeps its own inline L_FUND_SIZEUP (unchanged,
# no regression); this overlay extends the SAME validated signal to the fade + snap legs. Rollback: B_FUND_LO=B_FUND_HI=0.
B_FUND_LO    = float(os.environ.get("B_FUND_LO", "0.10"))   # LONG (snap) boost when funding pct <= this (crowded shorts). 0 = OFF.
B_FUND_HI    = float(os.environ.get("B_FUND_HI", "0.90"))   # SHORT (fade) boost when funding pct >= this (crowded longs). 0 = OFF.
B_FUND_BOOST = float(os.environ.get("B_FUND_BOOST", "1.5")) # size multiplier on a qualifying trade.
def funding_mult(sym, side):
    """Leg-B funding-percentile size multiplier for `side` in ('long','short'). Pure size tilt, fail-safe 1.0, never gates."""
    if side == "long" and not B_FUND_LO: return 1.0
    if side == "short" and not B_FUND_HI: return 1.0
    fp = fund_pct(sym)
    if fp is None: return 1.0
    if side == "long" and fp <= B_FUND_LO:
        log(f"B-FUND {sym}: funding pct {fp:.2f} <= {B_FUND_LO} (crowded-short squeeze fuel) -> long size x{B_FUND_BOOST}")
        return B_FUND_BOOST
    if side == "short" and fp >= B_FUND_HI:
        log(f"B-FUND {sym}: funding pct {fp:.2f} >= {B_FUND_HI} (crowded-long unwind fuel) -> fade size x{B_FUND_BOOST}")
        return B_FUND_BOOST
    return 1.0

def short_atr(sym):
    """1h ATR (absolute price units) over the last 14 CLOSED bars — the width unit for the leg-C chandelier. None on failure."""
    k = mkl(sym, "1h", 20)
    if not k or len(k) < 16: return None
    k = k[:-1]                                          # drop forming bar
    h = [float(x[2]) for x in k]; l = [float(x[3]) for x in k]; c = [float(x[4]) for x in k]
    tr = [h[0] - l[0]] + [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
    return sum(tr[-14:]) / 14 if len(tr) >= 14 else None

def liq_flush(sym):
    """Recent LONG-liquidation $ (last ~20min) for the LIQUIDATION SIZE-UP, via the FREE Coinalyze REST hist endpoint
    (works on this VPS; the Binance forceOrder WS is network-blocked, lesson #43). Returns the summed long-liq USD, or
    None on ANY failure / no key = the FALLBACK: no boost, trade proceeds normally. A MODERATE flush (0<sum<L_LIQ_HI)
    = forced selling exhausting = a better dip-buy (backtest on our fills; big flush is worse, no-flush is worse)."""
    if not COINALYZE_KEY: return None
    try:
        to = int(now()); frm = to - 20*60
        u = (f"https://api.coinalyze.net/v1/liquidation-history?symbols={sym}_PERP.A&interval=1min"
             f"&from={frm}&to={to}&convert_to_usd=true")
        req = urllib.request.Request(u, headers={"api_key": COINALYZE_KEY})
        d = json.loads(urllib.request.urlopen(req, timeout=4).read())
        if isinstance(d, list) and d:
            return sum(h.get("l", 0) for h in d[0].get("history", []))
        return 0.0
    except Exception:
        return None

_LIQ30_CACHE = {}
def liq30(sym):
    """GREENER S1 gate data: trailing-30min LONG-liquidation USD (Coinalyze), cached 55s per symbol (rate-limit safety:
    the PENDING re-check polls once a minute per pending coin, and pendings are capped by the slot pool). Returns the
    summed $ or None on ANY failure/no key (caller decides fail-CLOSED)."""
    hit = _LIQ30_CACHE.get(sym)
    if hit and now() - hit[0] < 55: return hit[1]
    if not COINALYZE_KEY: return None
    try:
        to = int(now()); frm = to - 30*60
        u = (f"https://api.coinalyze.net/v1/liquidation-history?symbols={sym}_PERP.A&interval=1min"
             f"&from={frm}&to={to}&convert_to_usd=true")
        req = urllib.request.Request(u, headers={"api_key": COINALYZE_KEY})
        d = json.loads(urllib.request.urlopen(req, timeout=4).read())
        v = sum(float(h.get("l", 0) or 0) for h in d[0].get("history", [])) if (isinstance(d, list) and d) else 0.0
        _LIQ30_CACHE[sym] = (now(), v)
        return v
    except Exception:
        return None

def short_liq_ok(sym):
    """SHORT-LIQ CATALYST GATE: True to allow arming a fade. Requires long-liq20 (Coinalyze `l` = trapped LONGS
    liquidating = a real down-catalyst) >= S_LIQ_MIN. Validated: catalyst fades 100% WR (10/10); pure fades (no
    catalyst) = the -$0.24 squeezes. FAIL-SAFE: Coinalyze down (None) -> True (fire anyway + log; a data outage must
    NOT silently kill the short leg). S_LIQ_MIN=0 -> always True (gate OFF, default)."""
    if not S_LIQ_MIN: return True
    ll = liq_flush(sym)
    if ll is None:
        log(f"S-LIQ-GATE {sym}: Coinalyze unavailable -> firing anyway (fail-safe)"); return True
    if ll < S_LIQ_MIN:
        log(f"S-LIQ-GATE {sym}: long-liq20 ${ll:,.0f} < ${S_LIQ_MIN:,.0f} (no flush catalyst = squeeze risk) — skip fade")
        return False
    return True

# ========================= LIQ-SNAPBACK LONG LEG (A) — validated cascade_v3 (SOL PF 1.45), liquid-only =========================
# Mirror of the short-fade: a LONG-liquidation flush overshoots price DOWN; once forced selling exhausts and the last
# closed 1m candle RECLAIMS (green + closes back above the flush low = absorption), BUY the snap-back. Liquidity-filtered
# universe (the fix that turned the 117-coin cascade run negative -> SOL-class positive) + BTC-veto (don't buy into a real
# macro distribution) + maker-in + hard stop below the flush low + time-stop. Params from cascade_v3_backtest.py
# (THR $250k / hold 30m / TP 0.8%). DEFAULT OFF (SNAP_ENABLED=0) until paper-validated live.
SNAP_ENABLED  = os.environ.get("SNAP_ENABLED", "0") not in ("0", "false", "False", "no")
SNAP_THR      = float(os.environ.get("SNAP_THR", "400000"))    # long-liq20 $ flush threshold. ⬆ 250k->400k: snap_legA_backtest.py (96 coins, real cascades) showed 250k NET-NEGATIVE (-$7.24/PF0.67, loses both halves); 400k = PF1.56 +$3.22 robust both halves (the $ threshold IS the liquidity filter — illiquid alts can't print 400k long-liq/20min). Carried by BTC/ETH/SOL.
SNAP_TP       = float(os.environ.get("SNAP_TP", "0.008"))      # snap-back take-profit (+0.8%)
SNAP_SL       = float(os.environ.get("SNAP_SL", "0.010"))      # hard stop below entry (1.0%; also capped at flush low)
SNAP_HOLD_MIN = int(os.environ.get("SNAP_HOLD_MIN", "30"))     # time-stop: exit if no snap-back within N minutes
SNAP_CAP      = int(os.environ.get("SNAP_CAP", "2"))           # concurrent snap-back longs
SNAP_UNIV     = int(os.environ.get("SNAP_UNIV", "40"))         # liquidity filter: only the top-N perps by 24h $-volume
SNAP_BTC_VETO = float(os.environ.get("SNAP_BTC_VETO", "0.015"))# skip if BTC last 1h <= -this (real distribution, not overshoot)
_SNAP_UNI = []; _SNAP_UNI_TS = 0

def snap_liquid_universe():
    """Top-N USDT-perps by 24h quote-volume = the liquidity filter. Cached ~15min. Empty list on failure (leg self-idles)."""
    global _SNAP_UNI, _SNAP_UNI_TS
    if _SNAP_UNI and now() - _SNAP_UNI_TS < 900: return _SNAP_UNI
    try:
        d = json.loads(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=8).read())
        rows = [(t["symbol"], float(t["quoteVolume"])) for t in d if t["symbol"].endswith("USDT")]
        rows.sort(key=lambda r: r[1], reverse=True)
        _SNAP_UNI = [s for s, _ in rows[:SNAP_UNIV]]; _SNAP_UNI_TS = now()
    except Exception:
        pass
    return _SNAP_UNI

def btc_distribution():
    """True if BTC printed a real down-distribution over the last closed 1h bar (<= -SNAP_BTC_VETO) — veto snap-back buys."""
    k = mkl("BTCUSDT", "1h", 3)
    if not k or len(k) < 3: return False
    c = [float(x[4]) for x in k]
    return (c[-2] / c[-3] - 1) <= -SNAP_BTC_VETO      # last CLOSED 1h bar (c[-1] is forming)

def snap_signal(sym):
    """LONG-liq flush + reclaim. Returns (entry_ref, flush_low) or None.
    Fires when long-liq20 >= SNAP_THR (forced longs flushing = overshoot down) AND the last CLOSED 1m candle reclaims:
    green (close>open) AND closes back above the prior bar's low (forced selling absorbed by passive bids)."""
    ll = liq_flush(sym)
    if ll is None or ll < SNAP_THR: return None
    k = mkl(sym, "1m", 7)
    if not k or len(k) < 5: return None
    k = k[:-1]                                         # drop the FORMING bar — signal only on CLOSED candles (no look-ahead)
    o = [float(x[1]) for x in k]; l = [float(x[3]) for x in k]; c = [float(x[4]) for x in k]
    j = len(c) - 1                                    # last CLOSED 1m bar
    flush_low = min(l[j-3:j+1])
    if c[j] > o[j] and c[j] > l[j-1] and c[j] > flush_low:   # reclaim: green + back above the flush low
        return c[j], flush_low
    return None

_LAST_SNAP_BAR = -1
def open_snapback(st):
    """Arm liq-snapback longs on the liquidity-filtered universe. Self-guards: leg off / no free slot / BTC distribution.
    THROTTLED to once per new 1m bar (signals only change on 1m close) — avoids hammering Coinalyze (rate-limit) and
    blocking the shared poll loop with 40 sequential REST calls every 2s (which would degrade the other legs' cadence)."""
    global _LAST_SNAP_BAR
    if not SNAP_ENABLED or not can_open(st, "snap"): return
    cur_bar = int(now() // 60)
    if cur_bar == _LAST_SNAP_BAR: return              # already scanned this 1m bar
    _LAST_SNAP_BAR = cur_bar
    if btc_distribution(): return                     # macro flush in progress — not an absorbable overshoot
    open_syms = held_all(st)   # AUDIT BUG-4: cross-leg exclusion incl. squeeze (a long on a squeeze-shorted coin = hedge-mode fee bleed)
    margin = (st["equity"] / TOTAL_SLOTS) if st["equity"] else 0
    for sym in snap_liquid_universe():
        if not can_open(st, "snap"): break
        if sym in open_syms: continue
        sig = snap_signal(sym)
        if not sig: continue
        ref, flush_low = sig
        entry = ref
        stop = min(flush_low, entry * (1 - SNAP_SL))  # hard stop = closest of flush low / -SNAP_SL
        if stop >= entry: continue
        qty, sp = calc_qty(sym, entry, margin * LEV * funding_mult(sym, "long") * E_SIZE_FRAC)   # B: funding boost, E: equity dial
        if not qty: continue
        if LIVE:
            set_lev(sym); r = limit_buy(sym, qty, round_tick(entry, sp))
            oid = str(r.get("orderId", "")) if isinstance(r, dict) else ""
            if not oid: log(f"A-REJECT {sym}: {r}"); continue
        else:
            oid = f"snap{int(now())}"
        st["snap"].append({"coin": sym, "state": "PENDING", "oid": oid, "entry": entry, "stop": stop,
                           "tp": entry * (1 + SNAP_TP), "qty": qty, "created": now(), "fill_ts": None})
        open_syms.add(sym); took_slot(st, "snap")
        log(f"A-ARM {sym}: snap BUY {qty} @ {fmt(entry)} liq-flush stop {fmt(stop)} tp {fmt(entry*(1+SNAP_TP))} | "
            f"{n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])}/A{len(st['snap'])})")
        _tg(f"🟢 <b>A-ARM {sym}</b> snap-back @ {fmt(entry)} | stop {fmt(stop)} tp +{SNAP_TP*100:.1f}%")

def manage_snapback(st):
    """Manage liq-snapback longs: fill detection, TP / hard-SL / time-stop. Paper simulates on 1m closes."""
    if not st.get("snap"): return
    for pos in list(st["snap"]):
        sym = pos["coin"]; px = price_now(sym)
        if px is None: continue
        if pos["state"] == "PENDING":
            if LIVE:
                held = live_positions()
                amt = held.get((sym, "LONG" if HEDGE else "BOTH"), 0.0) if isinstance(held, dict) else 0.0
                if amt > 0:
                    if pos.get("oid"): cancel(sym, pos["oid"])   # ORPHAN-FIX (GREENER): kill any unfilled remainder of the entry limit NOW (race-safe: errors harmlessly if fully filled)
                    pos["qty"] = amt                             # size the position at what the EXCHANGE says we hold, not the arm qty
                    pos["state"] = "OPEN"; pos["fill_ts"] = now(); stop_market_sell(sym, round_tick(pos["stop"], specs(sym)))
                    log(f"A-FILL {sym} @ {fmt(pos['entry'])} [SNAP OPEN]")
                    _tg(f"🟢🔔 <b>A-FILL {sym}</b> @ {fmt(pos['entry'])} | tp +{SNAP_TP*100:.1f}% | stop {fmt(pos['stop'])}")
                elif now() - pos["created"] > 5 * 60:
                    if pos["oid"]: cancel(sym, pos["oid"])
                    st["snap"].remove(pos); log(f"A-EXPIRE {sym} unfilled")
            else:
                if px <= pos["entry"]:                 # paper: maker limit fills when price trades down to it
                    pos["state"] = "OPEN"; pos["fill_ts"] = now(); log(f"A-FILL {sym} @ {fmt(pos['entry'])} [SNAP OPEN paper]")
                elif now() - pos["created"] > 5 * 60:
                    st["snap"].remove(pos); log(f"A-EXPIRE {sym} unfilled")
            continue
        # OPEN: exit on TP / hard-SL / time-stop
        tag = None
        if px >= pos["tp"]: tag = "TP"
        elif px <= pos["stop"]: tag = "SL"
        elif pos["fill_ts"] and now() - pos["fill_ts"] > SNAP_HOLD_MIN * 60: tag = "TIME"
        if tag:
            if LIVE:
                held = live_positions(); amt = held.get((sym, "LONG" if HEDGE else "BOTH"), 0.0) if isinstance(held, dict) else 0.0
                if amt > 0: mkt_sell(sym, abs(amt))
                for a in (_signed("/fapi/v1/openOrders", {}, "GET") or []):
                    if isinstance(a, dict) and a.get("symbol") == sym: cancel(sym, a.get("orderId"))
            _close_snap(st, pos, px, tag)

def _close_snap(st, pos, px, tag):
    ret = (px / pos["entry"] - 1) * 100
    book(st, "snap", pos, px, tag)
    try: st["snap"].remove(pos)
    except ValueError: pass

# ========================= GREENER E5 SQUEEZE-FADE (ported from clever-bot 21-07) =========================
# Taker market SELL into a SHORT-liq flush spike; exit = TIME (SQF_HOLD_MIN) or the WIDE catastrophe stop (+SQF_SL).
# NO TP / NO trail. Port deltas vs clever-bot: U1/ledger/allocator stripped; PUMP CHECK FIRST (free klines) so the
# Coinalyze call runs only for already-pumping coins (rate-limit: ~0-10 liq calls/min vs a 150-coin sweep's 429s).
_SQ_COOLDOWN = {}
def squeeze_liq(sym, buckets=False):
    """(S5, S15, L15) = short-liq $ summed over last 5/15 min + long-liq over 15, Coinalyze 1min bars.
    buckets=True appends the raw per-minute short-liq list (ARM telemetry: liq-shape features).
    None on ANY failure/no key -> the engine SELF-IDLES (absence of data here IS 'no signal')."""
    if not COINALYZE_KEY: return None
    try:
        to = int(now()); frm = to - 16*60
        u = (f"https://api.coinalyze.net/v1/liquidation-history?symbols={sym}_PERP.A&interval=1min"
             f"&from={frm}&to={to}&convert_to_usd=true")
        req = urllib.request.Request(u, headers={"api_key": COINALYZE_KEY})
        d = json.loads(urllib.request.urlopen(req, timeout=4).read())
        if not (isinstance(d, list) and d): return None
        hist = d[0].get("history", [])
        if not hist: return (0.0, 0.0, 0.0, []) if buckets else (0.0, 0.0, 0.0)
        s = [float(h.get("s", 0) or 0) for h in hist]; l = [float(h.get("l", 0) or 0) for h in hist]
        if buckets: return (sum(s[-5:]), sum(s[-15:]), sum(l[-15:]), s)
        return (sum(s[-5:]), sum(s[-15:]), sum(l[-15:]))
    except Exception:
        return None

def _sq_shape(sbk):
    """(s1max_s5, m1_share) from the per-minute short-liq buckets. CLOSED minutes only — the last bucket is the forming
    minute, mostly post-decision (the fill-bar trap's liq cousin). Pure ARM telemetry for the pre-registered entry
    filters from the 22-07 study (skip if s1max_s5<0.6 etc.) — logged, never gating."""
    sc = sbk[:-1] if len(sbk) > 1 else []
    s5c = sum(sc[-5:])
    if s5c <= 0: return 0.0, 0.0
    return max(sc[-5:]) / s5c, sc[-1] / s5c

def pump60(sym):
    """Pct change over the prior 60 CLOSED 1m bars (ARM telemetry only, not a gate)."""
    k = mkl(sym, "1m", 63)
    if not k or len(k) < 62: return None
    k = k[:-1]
    c = [float(x[4]) for x in k]
    return (c[-1] / c[-61] - 1) * 100

def pump15(sym):
    """Pct change over the prior 15 CLOSED 1m bars. None on failure. (Forming bar dropped — closed candles only.)"""
    k = mkl(sym, "1m", 18)
    if not k or len(k) < 17: return None
    k = k[:-1]
    c = [float(x[4]) for x in k]
    return (c[-1] / c[-16] - 1) * 100

def squeeze_signal(sym):
    """E5 trigger: pm15 >= SQF_PUMP_MIN (checked FIRST, free) AND S5 >= SQF_S5_MIN AND S15 > SQF_DOM * L15."""
    pm = pump15(sym)
    if pm is None or pm < SQF_PUMP_MIN: return None
    ls = squeeze_liq(sym)
    if ls is None: return None
    S5, S15, L15 = ls
    if S5 < SQF_S5_MIN: return None
    if not (S15 > SQF_DOM * L15): return None
    px = price_now(sym)
    if px is None: return None
    return {"px": px, "S5": S5, "S15": S15, "L15": L15, "pm15": pm}

_LAST_SQ_BAR = [-1]
def open_squeeze(st, watch):
    """E5 arm: scan the liquid universe once per new 1m bar. Taker market SELL. Respects the short-side pauses
    (streak + book-breaker) — a squeeze SL feeds the same counters in book()."""
    if not SQF_ENABLED or not can_open(st, "squeeze"): return
    if S_LOSS_STREAK_N > 0 and now() < st.get("fade_paused_until", 0): return       # short-side streak pause
    if S_BOOK_BREAKER_N > 0 and now() < st.get("book_paused_until", 0): return      # short-side book-wide pause
    cur_bar = int(now() // 60)
    if cur_bar == _LAST_SQ_BAR[0]: return
    _LAST_SQ_BAR[0] = cur_bar
    open_syms = held_all(st)
    margin = (st["equity"] / TOTAL_SLOTS) if st["equity"] else 0
    # AUDIT BUG-6: pump15 for the whole universe CONCURRENTLY (a serial 150-coin sweep froze the 2s manage loop ~30s),
    # then Coinalyze liq calls ONLY for the top-SQF_SCAN_MAX pumps (burst cap: a market-wide pump would otherwise fire
    # 30+ liq calls into the ~40/min shared free tier and blind the S1 fill-gate with 429s).
    elig = [s for s in watch if s not in open_syms
            and now() - _SQ_COOLDOWN.get(s, 0) >= SQF_COOLDOWN_MIN * 60
            and not (S_COIN_COOLDOWN_SEC > 0 and st.get("fade_sl_ts", {}).get(s, 0)
                     and now() - st["fade_sl_ts"][s] < S_COIN_COOLDOWN_SEC)]
    pumps = _pmap(pump15, elig)
    cands = sorted(((pm, s) for s, pm in zip(elig, pumps) if pm is not None and pm >= SQF_PUMP_MIN), reverse=True)
    for pm, sym in cands[:SQF_SCAN_MAX]:
        if not can_open(st, "squeeze"): break
        ls = squeeze_liq(sym, buckets=True)
        if ls is None: continue
        S5, S15, L15, sbk = ls
        if S5 < SQF_S5_MIN or not (S15 > SQF_DOM * L15): continue
        px = price_now(sym)
        if px is None: continue
        s1x, m1s = _sq_shape(sbk)                       # ARM telemetry (22-07 study): one-shot-ness + still-flowing share
        p60 = pump60(sym)                               # ARM telemetry: 60-min pump (ceiling ideas died, floor ideas live)
        sig = {"px": px, "S5": S5, "S15": S15, "L15": L15, "pm15": pm}
        entry = sig["px"]; sp = specs(sym)
        if not sp: continue
        qty, sp = calc_qty(sym, entry, margin * LEV * E_SIZE_FRAC)
        if not qty: continue
        log(f"SQF-ARM {sym}: squeeze SELL {qty} @ {fmt(entry)} S5 ${sig['S5']:,.0f} S15/L15 ${sig['S15']:,.0f}/${sig['L15']:,.0f} "
            f"pump{sig['pm15']:+.1f}% s1x{s1x:.2f} m1{m1s:.2f} p60{f'{p60:+.1f}' if p60 is not None else '?'}%")
        if SQF_MAKER:                                    # MAKER-ENTRY HYBRID: limit SELL above market, taker fallback on TTL
            lpx = round_tick(entry * (1 + SQF_MAKER_BUMP), sp)
            oid = None
            if LIVE:
                set_lev(sym)
                r = limit_short_open(sym, qty, lpx)
                oid = r.get("orderId") if isinstance(r, dict) and not r.get("_error") else None
                if not oid: log(f"SQF-MAKER-REJECT {sym}: {str(r)[:80]} — falling back to taker NOW")
            if oid or not LIVE:
                st["squeeze"].append({"coin": sym, "state": "PENDING", "oid": oid, "limit": lpx, "qty": qty, "ts": now(),
                                      "s1x": s1x, "m1s": m1s, "p60": p60, "S5": S5, "pm15": pm})
                open_syms.add(sym); _SQ_COOLDOWN[sym] = now(); took_slot(st, "squeeze")
                log(f"SQF-PEND {sym}: maker SELL {qty} @ {fmt(lpx)} (+{SQF_MAKER_BUMP*100:.1f}%) TTL {SQF_MAKER_TTL_SEC//60}m | "
                    f"{n_open(st)}/{TOTAL_SLOTS}")
                continue
            # LIVE limit placement rejected -> fall through to the immediate-taker path (the hybrid's own fallback)
        if LIVE:
            set_lev(sym); r = market_short(sym, qty)
            if not (isinstance(r, dict) and not r.get("_error")):
                log(f"SQF-REJECT {sym}: {str(r)[:90]}"); continue
            exq = float(r.get("executedQty", 0) or 0)
            if exq <= 0 and r.get("orderId"):                  # AUDIT BUG-3: a MARKET order that matched nothing must NOT become a
                stq = order_status(sym, r["orderId"])          # phantom position. One re-query: async fill -> take it; still 0 -> skip.
                if isinstance(stq, dict): exq = float(stq.get("executedQty", 0) or 0); r = stq if exq > 0 else r
            if exq <= 0:
                log(f"SQF-NOFILL {sym}: market order matched nothing (executedQty=0) — no position, skipping"); continue
            qty = exq                                          # AUDIT BUG-2a: size the position at what ACTUALLY filled (thin books
            fill = float(r.get("avgPrice") or 0)               # during cascades = partial taker fills are real)
            if fill > 0: entry = fill
        sl_px = round_tick(entry * (1 + SQF_SL), sp)
        sl_oid = None
        if LIVE:
            r2 = stop_market_buy(sym, sl_px)
            sl_oid = r2.get("algoId") if isinstance(r2, dict) and not r2.get("_error") else None
        st["squeeze"].append({"coin": sym, "state": "OPEN", "entry": entry, "qty": qty, "stop": sl_px, "sl_oid": sl_oid,
                              "ts": now(), "fill_ts": now(), "maxadv": entry})
        open_syms.add(sym); _SQ_COOLDOWN[sym] = now(); took_slot(st, "squeeze")
        log(f"SQF-FILL {sym} @ {fmt(entry)} SL {fmt(sl_px)} (+{SQF_SL*100:.0f}%) hold {SQF_HOLD_MIN}m [SQUEEZE OPEN] | "
            f"{n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])}/SQ{len(st['squeeze'])})")
        _tg(f"🟣🔔 <b>SQF-FILL {sym}</b> squeeze SELL @ {fmt(entry)} | SL +{SQF_SL*100:.0f}% | hold {SQF_HOLD_MIN}m | S5 ${sig['S5']:,.0f} pump {sig['pm15']:+.1f}%")

def _sq_promote(st, pos, entry, qty):
    """PENDING maker entry (or its taker fallback) FILLED -> real squeeze position with the catastrophe stop. Never naked."""
    sym = pos["coin"]; sp = specs(sym)
    sl_px = round_tick(entry * (1 + SQF_SL), sp) if sp else entry * (1 + SQF_SL)
    sl_oid = None
    if LIVE:
        r2 = stop_market_buy(sym, sl_px)
        sl_oid = r2.get("algoId") if isinstance(r2, dict) and not r2.get("_error") else None
    pos.update({"state": "OPEN", "oid": None, "entry": entry, "qty": qty, "stop": sl_px, "sl_oid": sl_oid,
                "ts": now(), "fill_ts": now(), "maxadv": entry, "minlow": entry})
    log(f"SQF-FILL {sym} @ {fmt(entry)} SL {fmt(sl_px)} (+{SQF_SL*100:.0f}%) hold {SQF_HOLD_MIN}m [SQUEEZE OPEN] | "
        f"{n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])}/SQ{len(st['squeeze'])})")
    _tg(f"🟣🔔 <b>SQF-FILL {sym}</b> squeeze SELL @ {fmt(entry)} (maker-hybrid) | SL +{SQF_SL*100:.0f}% | hold {SQF_HOLD_MIN}m")

def _sq_pending_tick(st, pos):
    """Maker-hybrid PENDING each 2s tick: fill -> promote; partial -> cancel remainder race-safely then promote at
    executedQty; TTL expiry -> cancel (race-safe) then taker fallback. Copies the fade leg's ORPHAN-FIX and
    fill-on-cancel patterns verbatim (the 20-07 ONDO/MET incident class). A rejected/zero-fill fallback frees the
    slot — never a phantom position (AUDIT BUG-3 pattern)."""
    sym = pos["coin"]
    if LIVE:
        stt = order_status(sym, pos["oid"]); s = stt.get("status") if isinstance(stt, dict) else None
        exq = float(stt.get("executedQty", 0) or 0) if isinstance(stt, dict) else 0.0
        if s in ("FILLED", "PARTIALLY_FILLED") and exq > 0:
            if s == "PARTIALLY_FILLED":                   # ORPHAN-FIX: kill the unfilled remainder NOW (race-safe re-query)
                cancel(sym, pos["oid"])
                stt2 = order_status(sym, pos["oid"])
                if isinstance(stt2, dict) and float(stt2.get("executedQty", 0) or 0) > 0: stt = stt2
            _sq_promote(st, pos, float(stt.get("avgPrice") or pos["limit"]), float(stt["executedQty"])); return
        if s in ("CANCELED", "EXPIRED", "REJECTED"):
            if exq > 0:                                   # AUDIT BUG-1 class: cancelled but FILLED first -> open WITH SL
                log(f"SQF-FILL-ON-CANCEL {sym}: executedQty={exq} — opening WITH SL")
                _sq_promote(st, pos, float(stt.get("avgPrice") or pos["limit"]), exq); return
            st["squeeze"].remove(pos); log(f"SQF-PEND-GONE {sym}: order {s}, nothing filled — slot freed"); return
        if now() - pos["ts"] >= SQF_MAKER_TTL_SEC:        # TTL: cancel, re-query the fill race, else taker fallback
            cancel(sym, pos["oid"])
            stt3 = order_status(sym, pos["oid"])
            if isinstance(stt3, dict) and float(stt3.get("executedQty", 0) or 0) > 0:
                _sq_promote(st, pos, float(stt3.get("avgPrice") or pos["limit"]), float(stt3["executedQty"])); return
            r = market_short(sym, pos["qty"])             # taker fallback — the hybrid never skips the trade
            if not (isinstance(r, dict) and not r.get("_error")):
                st["squeeze"].remove(pos); log(f"SQF-PEND-FALLBACK-REJECT {sym}: {str(r)[:80]} — slot freed"); return
            exq2 = float(r.get("executedQty", 0) or 0)
            if exq2 <= 0 and r.get("orderId"):            # AUDIT BUG-3: one re-query — no phantom positions
                stq = order_status(sym, r["orderId"])
                if isinstance(stq, dict): exq2 = float(stq.get("executedQty", 0) or 0); r = stq if exq2 > 0 else r
            if exq2 <= 0:
                st["squeeze"].remove(pos); log(f"SQF-PEND-NOFILL {sym}: fallback matched nothing — slot freed"); return
            _sq_promote(st, pos, float(r.get("avgPrice") or pos["limit"]), exq2)
        return                                            # still working the limit
    px = price_now(sym)                                   # paper: fill when price trades up INTO the limit; TTL -> taker sim
    if px is not None and px >= pos["limit"]: _sq_promote(st, pos, pos["limit"], pos["qty"]); return
    if now() - pos["ts"] >= SQF_MAKER_TTL_SEC:
        if px is None: st["squeeze"].remove(pos); return
        _sq_promote(st, pos, px, pos["qty"])

def manage_squeeze(st):
    """E5 exits: TIME (SQF_HOLD_MIN) or the wide catastrophe stop. Live also detects a native-stop close."""
    if not st.get("squeeze"): return
    realpos = live_positions() if LIVE else {}
    if realpos is None: return
    for pos in list(st["squeeze"]):
        if pos.get("state") == "PENDING":                 # maker-hybrid entry still working its limit
            _sq_pending_tick(st, pos); continue
        sym = pos["coin"]; px = price_now(sym)
        if px is None: continue
        pos["maxadv"] = max(pos.get("maxadv", pos["entry"]), px)
        pos["minlow"] = min(pos.get("minlow", pos["entry"]), px)         # best favorable since FILL (post-fill only — clean of the pre-fill-bar trap)
        amt = abs(realpos.get((sym, "SHORT"), 0.0)) if LIVE else pos["qty"]
        tag = None
        if px >= pos["stop"]: tag = "SL"
        elif now() - pos["fill_ts"] >= SQF_HOLD_MIN * 60: tag = "TIME"
        elif SQF_TRAIL_ACT > 0:                                          # E5 TRAIL (Artem 22-07): arm on first green, two-phase width
            if not pos.get("sq_armed") and (pos["entry"] - pos["minlow"]) / pos["entry"] >= SQF_TRAIL_ACT:
                pos["sq_armed"] = True
                log(f"SQF-TRAIL-ARM {sym} @ {fmt(px)} (fav {(pos['entry']-pos['minlow'])/pos['entry']*100:+.2f}%)")
            if pos.get("sq_armed"):
                w = SQF_TRAIL_EARLY if (now() - pos["fill_ts"]) < SQF_TRAIL_EARLY_MIN * 60 else SQF_TRAIL
                sp_t = specs(sym)
                tstop = round_tick(pos["minlow"] * (1 + w), sp_t) if sp_t else pos["minlow"] * (1 + w)
                if SQF_TRAIL_BE > 0:                                     # BE-FLOOR: armed => never lock worse than ~breakeven
                    be_px = round_tick(pos["entry"] * (1 - SQF_TRAIL_BE), sp_t) if sp_t else pos["entry"] * (1 - SQF_TRAIL_BE)
                    tstop = min(tstop, be_px)
                if LIVE and SQF_TRAIL_NATIVE:                            # FIX-A2 (Artem): armed => the trail RESTS on the exchange OR we are out. Now.
                    cur = pos.get("tr_stop") if pos.get("tr_oid") else None   # trust tr_stop ONLY if an order actually rests (A2: a failed placement no longer poisons the throttle)
                    if cur is None or abs(tstop - cur) / cur >= SQF_TRAIL_REPLACE_BP:
                        if pos.get("tr_oid"): cancel_algo(sym, pos["tr_oid"]); pos["tr_oid"] = None
                        rt = stop_market_buy_rq(sym, pos["qty"], tstop)  # FIX-A3: qty-based (coexists with the catastrophe closePosition stop)
                        if isinstance(rt, dict) and not rt.get("_error") and rt.get("algoId"):
                            pos["tr_oid"] = rt.get("algoId"); pos["tr_stop"] = tstop
                            log(f"SQF-TRAIL-SET {sym} @ {fmt(tstop)} — resting on the exchange")
                        elif px >= tstop:                                # rejected AND price already past the trigger (the 09:51 BANK case:
                            tag = "TRAIL"                                # bounce beat the placement) -> the trail HAS fired; close NOW at market
                            log(f"SQF-TRAIL-INSTANT {sym}: placement rejected with px past the trigger — closing NOW")
                        # else: transient reject with px still favorable -> tr_oid stays None -> RETRY next 2s tick
                if tag is None and px >= tstop: tag = "TRAIL"            # software fallback (paper mode / native-miss belt)
        if LIVE and tag is None and amt < pos["qty"] * 0.5:
            # position gone from the exchange: the NATIVE TRAIL stop, the catastrophe stop, OR a manual close/ADL.
            # Distinguish by which algo vanished — only real stops feed the counters; manual flatten books "closed".
            algos = algo_open_ids(sym) if (pos.get("sl_oid") or pos.get("tr_oid")) else set()
            if pos.get("tr_oid") and str(pos["tr_oid"]) not in algos: tag = "TRAIL"     # FIX-A: the native trail fired
            elif pos.get("sl_oid") and str(pos["sl_oid"]) not in algos: tag = "SL"      # catastrophe stop fired
            elif not pos.get("sl_oid") and not pos.get("tr_oid"): tag = "SL"            # no algos tracked -> conservative
            else: tag = "closed"                                                         # both algos still resting -> manual
        if not tag: continue
        exit_px = px
        if LIVE:
            if amt >= pos["qty"] * 0.5:                       # position still on the exchange -> close it FIRST, verify, then drop the algo
                r = mkt_buy_close(sym, amt)                   # AUDIT BUG-2b: close the EXCHANGE amt (not pos qty) and CHECK the result
                if isinstance(r, dict) and r.get("_error"):
                    log(f"SQF-CLOSE-RETRY {sym}: cover rejected ({str(r.get('_body',''))[:60]}) — keeping position, retry next tick")
                    continue                                  # stop algo still armed; nothing booked; retry next tick
            if pos.get("sl_oid"): cancel_algo(sym, pos["sl_oid"])
            if pos.get("tr_oid"): cancel_algo(sym, pos["tr_oid"])   # FIX-A: drop the native trail stop too
            real = real_close_fill(sym, "BUY", pos.get("ts", now()))
            if real: exit_px = real
        exit_tel(sym, "squeeze", tag, pos, px, (exit_px if LIVE else None))
        book(st, "squeeze", pos, exit_px, tag)
        try: st["squeeze"].remove(pos)
        except ValueError: pass
        log(f"SQF-CLOSE {sym} @ {fmt(exit_px)} ({tag})")

# ========================= TREND LEG (D) — daily Donchian breakout + regime + scale-out (validated +202%/5 windows) =========================
# Our strongest VALIDATED directional edge (trend_multi_backtest config D: 30/15 daily channel + SMA100, +202% / avgDD 19%
# over 5 windows). A DIFFERENT class from the event legs: LOW-FREQUENCY daily trend (fee-friendly), rides multi-day moves.
# SCALE-OUT thirds banks a small green partial on most winners = the fix for "tired of red days" (needs its OWN A/B backtest
# vs all-in). LONG-only, all long-beta -> shares the TOTAL-LONG ruin cap. DEFAULT OFF (T_ENABLED=0) until backtested.
T_ENABLED    = os.environ.get("T_ENABLED", "0") not in ("0", "false", "False", "no")
T_DON_N      = int(os.environ.get("T_DON_N", "40"))       # daily Donchian breakout (⬆30->40: trend_knob_sweep — wider = higher-quality, positive in the 2022 bear)
T_TRAIL_N    = int(os.environ.get("T_TRAIL_N", "20"))     # daily Donchian trailing low (⬆15->20: lets winners run, TRAIL20>15 every window)
T_SMA        = int(os.environ.get("T_SMA", "50"))         # per-coin regime SMA (⬇100->50: THE trend finding — ex-best-window +151% vs SMA100 −75%, lowest DD, wins recent chop; robust both-halves)
T_ATR_K      = float(os.environ.get("T_ATR_K", "2.0"))    # initial stop = entry - K * daily-ATR(14)
T_SCALE      = os.environ.get("T_SCALE", "1") not in ("0", "false", "False", "no")  # scale-out thirds (else all-in/all-out)
# ✅ BACKTEST VERDICT (grid-bot/trend_scaleout_ab.py, 5 windows + 4yr walk-forward): trend edge HOLDS (all-in +216%/5w,
# +482% WF; BTC-200d gate avoided all of 2026 chop = 0 trades/0 loss for 8mo). SCALE-OUT vs all-in = MARGINAL on raw
# return (~halves it, clips the fat tail) but a clear WIN for "fewer red days": green weeks 38%->63%, Sharpe 0.03->0.36,
# WR 33->40%, worst window -140%->-23%. → T_SCALE=1 is the right default for a $50 account (drawdown-quit is the real
# risk, not CAGR). ⚠️ BTC risk-off 246/246 days incl now -> leg-D is IDLE in the current tape (activates on BTC>200d).
T_CAP        = int(os.environ.get("T_CAP", "2"))          # concurrent trend longs
T_UNIV       = int(os.environ.get("T_UNIV", "60"))        # universe: top-N perps by 24h $vol (breadth = frequency)
T_BTC_REGIME = os.environ.get("T_BTC_REGIME", "1") not in ("0", "false", "False", "no")  # also gate on BTC > 200d SMA (risk-on)
_TREND_UNI = []; _TREND_UNI_TS = 0; _LAST_TREND_DAY = -1

def _sma(xs, n): return sum(xs[-n:]) / n if len(xs) >= n else None

def daily_atr(sym):
    """14-period daily ATR (absolute) on CLOSED daily bars. None on failure."""
    k = mkl(sym, "1d", 30)
    if not k or len(k) < 17: return None
    k = k[:-1]; h = [float(x[2]) for x in k]; l = [float(x[3]) for x in k]; c = [float(x[4]) for x in k]
    tr = [h[0]-l[0]] + [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
    return sum(tr[-14:]) / 14

def trend_regime_ok():
    """Leg-D regime gate: BTC daily close > 200d SMA (risk-on). Fail-OPEN on a data gap. Called once/day (open_trend) so
    it needs no cache — the base cached btc_risk_on() stays the hot-loop readout (this no longer shadows it)."""
    if not T_BTC_REGIME: return True
    k = mkl("BTCUSDT", "1d", 215)
    if not k or len(k) < 202: return True
    k = k[:-1]; c = [float(x[4]) for x in k]; sma = _sma(c, 200)
    return sma is None or c[-1] > sma

def trend_universe():
    global _TREND_UNI, _TREND_UNI_TS
    if _TREND_UNI and now() - _TREND_UNI_TS < 3600: return _TREND_UNI
    try:
        d = json.loads(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=8).read())
        rows = [(t["symbol"], float(t["quoteVolume"])) for t in d if t["symbol"].endswith("USDT")]
        rows.sort(key=lambda r: r[1], reverse=True)
        _TREND_UNI = [s for s, _ in rows[:T_UNIV]]; _TREND_UNI_TS = now()
    except Exception:
        pass
    return _TREND_UNI

def trend_signal(sym):
    """Daily Donchian-N breakout inside an uptrend regime. Returns (entry_ref, init_stop, atr) or None. Closed daily bars."""
    need = max(T_DON_N, T_SMA) + 3
    k = mkl(sym, "1d", need + 2)
    if not k or len(k) < need: return None
    k = k[:-1]                                            # drop the forming daily bar
    h = [float(x[2]) for x in k]; c = [float(x[4]) for x in k]
    j = len(c) - 1
    if j - T_DON_N < 0: return None
    don_hi = max(h[j-T_DON_N:j])                          # prior N-day high (excludes today's bar)
    sma_now = _sma(c, T_SMA); sma_prev = _sma(c[:-1], T_SMA)
    if sma_now is None or sma_prev is None: return None
    if not (c[j] > don_hi and c[j] > sma_now and sma_now > sma_prev): return None   # breakout + above RISING SMA
    atr = daily_atr(sym)
    if not atr: return None
    stop = c[j] - T_ATR_K * atr
    if stop >= c[j]: return None
    return c[j], stop, atr

def trend_trail_low(sym):
    """Prior T_TRAIL_N-day Donchian low (the runner's trailing stop). None on failure."""
    k = mkl(sym, "1d", T_TRAIL_N + 3)
    if not k or len(k) < T_TRAIL_N + 2: return None
    k = k[:-1]; l = [float(x[3]) for x in k]
    return min(l[-T_TRAIL_N:])

def open_trend(st):
    """Arm daily-Donchian trend longs. Throttled to once per UTC day (daily signals). Regime-gated (BTC risk-on)."""
    global _LAST_TREND_DAY
    if not T_ENABLED or not can_open(st, "trend"): return
    day = int(now() // 86400)
    if day == _LAST_TREND_DAY: return                     # scan once per day (daily bars only change at UTC midnight)
    _LAST_TREND_DAY = day
    if not trend_regime_ok(): return                      # risk-off: stand down (per-coin SMA still required below too)
    open_syms = held_all(st)   # AUDIT BUG-4: cross-leg exclusion incl. squeeze (a long on a squeeze-shorted coin = hedge-mode fee bleed)
    margin = (st["equity"] / TOTAL_SLOTS) if st["equity"] else 0
    for sym in trend_universe():
        if not can_open(st, "trend"): break
        if sym in open_syms: continue
        sig = trend_signal(sym)
        if not sig: continue
        entry, stop, atr = sig
        qty, sp = calc_qty(sym, entry, margin * LEV * E_SIZE_FRAC)   # E: fractional-Kelly equity dial
        if not qty: continue
        if LIVE:
            set_lev(sym); r = ioc_buy(sym, qty, entry * 1.003)   # trend breakout = take it (IOC marketable, small chase)
            if not (isinstance(r, dict) and float(r.get("executedQty", 0) or 0) > 0):
                log(f"D-NOFILL {sym}: {r}"); continue
            entry = float(r.get("avgPrice") or entry); qty = float(r["executedQty"])
            stop_market_sell(sym, round_tick(stop, sp))
        pos = {"coin": sym, "state": "OPEN", "entry": entry, "stop": stop, "istop": stop, "qty": qty, "qty0": qty,
               "Rdist": entry - stop, "atr": atr, "scaled1": False, "scaled2": False, "best": entry, "ts": now()}
        st["trend"].append(pos); open_syms.add(sym); took_slot(st, "trend")
        log(f"D-ARM {sym}: trend BUY {qty} @ {fmt(entry)} donchian{T_DON_N} stop {fmt(stop)} (R {(entry-stop)/entry*100:.1f}%) | "
            f"{n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])}/T{len(st['trend'])})")
        _tg(f"📈 <b>D-ARM {sym}</b> trend @ {fmt(entry)} | stop {fmt(stop)}")

def _sell_part(st, pos, qty_part, px, tag, count_wl=True):
    """Book a PARTIAL scale-out of a trend position: close qty_part live, book that slice, shrink the position.
    D#1: step-align the qty (qty0/3 is off-precision). D#2: only book/shrink on a confirmed non-error live fill."""
    sym = pos["coin"]
    if LIVE and qty_part > 0:
        sp = specs(sym)
        if sp: qty_part = round(math.floor(qty_part / sp["step"]) * sp["step"], sp["qp"])
        if qty_part <= 0: return False
        r = mkt_sell(sym, qty_part)
        if isinstance(r, dict) and r.get("_error"):
            log(f"D-SELL-FAIL {sym} [{tag}]: {r} — keep slice, retry next tick"); return False
    slice_pos = dict(pos, qty=qty_part)
    book(st, "trend", slice_pos, px, tag, count_wl=count_wl)
    pos["qty"] = max(0.0, pos["qty"] - qty_part)
    return True

def manage_trend(st):
    """Scale-out thirds: +1R sell 1/3, +2R sell 1/3 & stop->breakeven, final 1/3 rides the Donchian-N trailing low."""
    if not st.get("trend"): return
    for pos in list(st["trend"]):
        sym = pos["coin"]; px = price_now(sym)
        if px is None: continue
        pos["best"] = max(pos.get("best", pos["entry"]), px)
        R = (px - pos["entry"]) / pos["Rdist"] if pos["Rdist"] > 0 else 0
        third = pos["qty0"] / 3.0
        if T_SCALE and not pos["scaled1"] and R >= 1.0:
            if _sell_part(st, pos, third, px, "SCALE1", count_wl=False):   # partial: PnL booked, not a separate W/L
                pos["scaled1"] = True; log(f"D-SCALE1 {sym} @ {fmt(px)} (+1R) sold 1/3, ride the rest")
        if T_SCALE and not pos["scaled2"] and R >= 2.0:
            if _sell_part(st, pos, third, px, "SCALE2", count_wl=False):
                pos["scaled2"] = True; pos["stop"] = max(pos["stop"], pos["entry"])   # lock breakeven on the runner
                log(f"D-SCALE2 {sym} @ {fmt(px)} (+2R) sold 1/3, stop->breakeven")
        tl = trend_trail_low(sym)                          # runner trails the Donchian-N low (never loosens)
        if tl is not None: pos["stop"] = max(pos["stop"], tl)
        if px <= pos["stop"] and pos["qty"] > 0:
            tag = "TRAIL" if pos["stop"] > pos["istop"] else "SL"
            if LIVE:
                for a in (_signed("/fapi/v1/openOrders", {}, "GET") or []):
                    if isinstance(a, dict) and a.get("symbol") == sym: cancel(sym, a.get("orderId"))
            _sell_part(st, pos, pos["qty"], px, tag)
            log(f"D-EXIT {sym} @ {fmt(px)} [{tag}] trend closed")
        if pos["qty"] <= 1e-12:
            try: st["trend"].remove(pos)
            except ValueError: pass

# ========================= LONG LEG (maker dip-buy) =========================
def open_long(st, watch):
    # REFRESH-DROP (29-06): cancel a PENDING dip-limit whose coin no longer qualifies — don't hold a stale knife-catcher
    # for the full 12h TTL. Validated: HOLD vs DROP = ret/DD 0.59 vs 13.8 (H1) / 0.59 vs 11.5 (OOS), 6x fewer fastStops.
    for pos in list(st["long"]):
        if pos["state"] != "PENDING": continue
        up, ref, _mom, _il = trend_ref(pos["coin"])
        if not (up and ref):
            if LIVE: cancel(pos["coin"], pos["oid"])
            log(f"L-DROP {pos['coin']}: no longer qualifies — cancel resting dip limit | {n_open(st)-1}/{TOTAL_SLOTS}")
            _tg(f"⚪ <b>L-DROP {pos['coin']}</b> — disqualified, dip limit cancelled")
            st["long"].remove(pos); continue
        if L_RUNAWAY_DROP and ref > pos["limit"] * (1 + L_RUNAWAY_DROP/100.0):   # RUNAWAY-DROP: coin ran up, the -1.2% dip won't fill -> free the slot for a fresher candidate
            if LIVE: cancel(pos["coin"], pos["oid"])
            log(f"L-RUNAWAY {pos['coin']}: ran +{(ref/pos['limit']-1)*100:.1f}% above dip limit — cancel & free slot | {n_open(st)-1}/{TOTAL_SLOTS}")
            _tg(f"⚪ <b>L-RUNAWAY {pos['coin']}</b> — ran away +{(ref/pos['limit']-1)*100:.1f}%, dip limit cancelled")
            st["long"].remove(pos)
    if (L_BREAKER_RESET and st.get("paused_until", 0) and now() >= st["paused_until"]
            and st.get("loss_streak", 0) >= L_LOSS_STREAK_N):
        st["loss_streak"] = 0; st["paused_until"] = 0
    if now() < st.get("paused_until", 0): return
    if not can_open(st, "long"): return
    margin = (st["equity"] / TOTAL_SLOTS) if st["equity"] else 0
    have = held_all(st); cands = []; nqual = 0   # AUDIT BUG-4: dip exclusion sees ALL legs
    for sym in watch:
        up, ref, mom, illiq = trend_ref(sym)
        if up and ref and mom <= L_MOM_CAP:
            nqual += 1
            if sym not in have: cands.append((mom, sym, ref, illiq))
    st["lbreadth"] = nqual
    maxp_now = 3 if nqual >= L_BREADTH[0] else 2 if nqual >= L_BREADTH[1] else 1 if nqual >= L_BREADTH[2] else 0
    if len(st["long"]) >= maxp_now: return
    if L_ILLIQ_DROP and len(cands) >= 3:
        cands.sort(key=lambda c: c[3]); keep = max(1, len(cands)*2//3); cands = cands[:keep]
    if L_Z_GATE: cands = z_gate_filter(cands, L_Z_GATE)   # DISPERSION Z-GATE: arm only coins clearly above the pack (after illiq, before rank)
    cands.sort(reverse=True)
    for mom, sym, ref, illiq in cands:
        if not can_open(st, "long") or len(st["long"]) >= maxp_now: break
        sp = specs(sym)
        if not sp: continue
        if not wick_ok(sym): continue                       # LOWER-WICK VETO: skip a sellers-only falling knife (thin-book crash risk)
        m_mult = 1.0
        if L_OI_CAPIT or L_OI_CAPIT_SOFT:                    # OI-CAPITULATION: buy the FLUSH (OI falling = long capitulation), not the crowd
            oi_sl = oi_slope(sym)
            if oi_sl is None:                               # FAIL-SAFE: OI API unavailable -> full size, gate inert (never penalize on error)
                pass
            else:
                capit = oi_sl <= (L_OI_CAPIT if L_OI_CAPIT else -0.005)
                if L_OI_CAPIT and not capit:                # HARD gate: skip a dip whose OI is not capitulating
                    log(f"L-OI-GATE {sym}: OI30m {oi_sl*100:+.2f}% not capitulating (need <={L_OI_CAPIT*100:.1f}%) — skip arm"); continue
                if L_OI_CAPIT_SOFT:                          # SOFT: full size on capitulation, reduced on crowded — log the counterfactual
                    m_mult = 1.0 if capit else L_OI_CAPIT_SOFT
                    log(f"L-OI-SOFT {sym}: OI30m {oi_sl*100:+.2f}% {'CAPITULATION -> full' if capit else f'crowded -> size x{m_mult}'}")
        if L_FUND_SIZEUP:                                    # FUNDING-PERCENTILE SIZE-UP: bigger size on crowded-short (low-funding-pct) longs
            fp = fund_pct(sym)                               # fail-safe: API error (None) -> no boost, base size (never penalize on error)
            if fp is not None and fp <= L_FUND_SIZEUP:
                m_mult *= L_FUND_BOOST
                log(f"L-FUND-SIZEUP {sym}: funding pct {fp:.2f} <= {L_FUND_SIZEUP} (crowded-short squeeze fuel) -> size x{L_FUND_BOOST} (m_mult {m_mult:.2f})")
        if L_LIQ_SIZEUP:                                     # LIQUIDATION SIZE-UP: bigger on a MODERATE recent long-liq flush (exhausting forced-sell = bounce)
            lq = liq_flush(sym)                              # fail-safe: None (Coinalyze down/no-key) -> no boost, base size (never penalize)
            if lq is not None and 0 < lq < L_LIQ_HI:
                m_mult *= L_LIQ_BOOST
                log(f"L-LIQ-SIZEUP {sym}: long-liq20 ${lq:,.0f} (moderate flush <${L_LIQ_HI:,.0f}) -> size x{L_LIQ_BOOST} (m_mult {m_mult:.2f})")
        limit_px = round_tick(ref*(1-L_DIP/100.0), sp)
        qty, sp = calc_qty(sym, limit_px, margin*LEV*m_mult)
        if not qty: continue
        if LIVE:
            set_lev(sym); r = limit_buy(sym, qty, limit_px)
            if isinstance(r, dict) and r.get("_error"):
                oid = None
                if L_BID_REPRICE and "5022" in str(r.get("_body", "")):    # shallow post-only reject -> re-place ONCE at the bid (still maker)
                    cur = price_now(sym)
                    if cur and 0 <= (limit_px-cur)/cur*100 <= L_BID_REPRICE:  # market only just past the limit = shallow (NOT a deep knife -> those stay skipped)
                        bpx = round_tick(cur*(1-0.0001), sp)                  # a tick below current = passive maker buy
                        q2, sp = calc_qty(sym, bpx, margin*LEV*m_mult)
                        if q2:
                            r2 = limit_buy(sym, q2, bpx)
                            if isinstance(r2, dict) and not r2.get("_error") and r2.get("orderId"):
                                oid = r2["orderId"]; limit_px = bpx; qty = q2
                                log(f"L-BID-REPRICE {sym}: shallow reject (mkt {fmt(cur)}) -> re-placed maker @ {fmt(bpx)}")
                if not oid:
                    log(f"L LIMIT-BUY reject {sym}: {r.get('_body','')[:80]}"); continue
            else:
                oid = r.get("orderId")
        else: oid = f"paper-{int(now())}-{sym}"
        st["long"].append({"coin": sym, "state": "PENDING", "oid": oid, "limit": limit_px, "qty": qty,
                           "entry": None, "tp_oid": None, "sl_oid": None, "sl": None, "ts": now(), "maxhigh": None})
        have.add(sym); took_slot(st, "long")
        log(f"L-ARM {sym}: dip BUY {qty} @ {fmt(limit_px)} (-{L_DIP}%) RS {mom:+.0f}% | {n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])})")
        _tg(f"🟢 <b>L-ARM {sym}</b> dip buy @ {fmt(limit_px)} (-{L_DIP}%) | RS {mom:+.0f}%")

def manage_long(st):
    realpos = live_positions() if LIVE else {}
    if realpos is None: return   # RISK-1: positionRisk query failed (429/418/5xx) -> don't trust; skip mgmt this cycle (open positions stay protected by their native TP/backstop algos), retry next tick
    for pos in list(st["long"]):
        sym = pos["coin"]
        if pos["state"] == "PENDING":
            filled = False; entry = pos["limit"]; qty = pos["qty"]
            if LIVE:
                stt = order_status(sym, pos["oid"]); s = stt.get("status") if isinstance(stt, dict) else None
                if s in ("FILLED", "PARTIALLY_FILLED") and float(stt.get("executedQty", 0)) > 0:
                    if s == "PARTIALLY_FILLED":   # ORPHAN-FIX (20-07): same as the fade leg — cancel the unfilled remainder so it can
                        cancel(sym, pos["oid"])   # never fill later as an untracked position (race-safe via the re-query below).
                        stt2 = order_status(sym, pos["oid"])
                        if isinstance(stt2, dict) and float(stt2.get("executedQty", 0) or 0) > 0:
                            stt = stt2
                    filled = True; entry = float(stt.get("avgPrice") or pos["limit"]); qty = float(stt["executedQty"])
                elif s in ("CANCELED", "EXPIRED", "REJECTED"): st["long"].remove(pos); continue
            else:
                px = price_now(sym)
                if px and px <= pos["limit"]: filled = True
            if filled:
                sl_px = entry*(1-L_SL/100.0)
                if L_STRUCT_SL_BARS:
                    swl = swing_low(sym, L_STRUCT_SL_BARS)
                    if swl: sl_px = min(max(swl*0.9985, entry*(1-L_SL/100.0)), entry*(1-0.1/100.0))
                pos.update({"state": "OPEN", "entry": entry, "qty": qty, "sl": sl_px, "maxhigh": entry, "ft": now()})
                if LIVE:
                    sp = specs(sym); tp_px = round_tick(entry*(1+L_TP/100.0), sp)
                    r = limit_sell_tp(sym, qty, tp_px); pos["tp_oid"] = r.get("orderId") if isinstance(r, dict) and not r.get("_error") else None
                    if L_MAKER_SL:                                   # MAKER-SL OCO: stop-LIMIT at SL (primary, fill floored at SL) + STOP_MARKET backstop below
                        slr = round_tick(sl_px, sp)
                        lim = round(slr + L_MSL_TICKS*sp["tick"], sp["pp"]) if L_MSL_PASSIVE else slr   # PASSIVE: limit a hair ABOVE trigger -> fills MAKER on a bounce (not marketable/taker)
                        rs = stop_limit_sell(sym, qty, slr, lim); pos["sl_oid"] = rs.get("algoId") if isinstance(rs, dict) and not rs.get("_error") else None
                        rb = stop_market_sell(sym, round_tick(sl_px*(1-L_MAKER_SL_GAP/100.0), sp)); pos["bs_oid"] = rb.get("algoId") if isinstance(rb, dict) and not rb.get("_error") else None
                        if pos["sl_oid"] is None and pos["bs_oid"] is None:   # both algo placements failed -> fall back to plain taker stop
                            rs2 = stop_market_sell(sym, slr); pos["sl_oid"] = rs2.get("algoId") if isinstance(rs2, dict) and not rs2.get("_error") else None
                    else:
                        rs = stop_market_sell(sym, round_tick(sl_px, sp)); pos["sl_oid"] = rs.get("algoId") if isinstance(rs, dict) and not rs.get("_error") else None
                log(f"L-FILL {sym} @ {fmt(entry)} TP {fmt(entry*(1+L_TP/100))} SL {fmt(sl_px)} [LONG OPEN]")
                _tg(f"🟢🔔 <b>L-FILL {sym}</b> @ {fmt(entry)} | TP +{L_TP}% | SL {fmt(sl_px)}")
            elif now()-pos["ts"] > L_TTL_BARS*BAR_SEC:
                if LIVE: cancel(sym, pos["oid"])
                log(f"L-EXPIRE {sym} unfilled"); st["long"].remove(pos)
            elif L_GRIND_VETO:
                # G-VETO (fix #8): sample price each tick; if price APPROACHES the limit slowly (no >=L_GV_DROP% fall
                # from the L_GV_WIN-sec high) it's a grind, not a flush -> cancel before it fills. Flushes blast through
                # the band in seconds and fill first (by design). Cancel is a REQUEST: on error keep the pos (fill may
                # have won the race; next tick's order_status picks it up) — freqtrade #9273 lesson.
                gpx = price_now(sym) if LIVE else px
                if gpx:
                    h = pos.setdefault("pxh", [])
                    h.append([now(), gpx])
                    cutoff = now() - (L_GV_WIN + 30)
                    while h and h[0][0] < cutoff: h.pop(0)
                    if (gpx <= pos["limit"]*(1 + L_GV_BAND/100.0) and len(h) >= 2
                            and h[-1][0] - h[0][0] >= 60):                 # need >=60s of history before judging
                        hi = max(p for _, p in h if _ >= now() - L_GV_WIN)
                        if (hi/pos["limit"] - 1)*100 < L_GV_DROP:          # window-high not far enough above limit = slow grind
                            ok_cancel = True
                            if LIVE:
                                r = cancel(sym, pos["oid"])
                                if isinstance(r, dict) and r.get("_error"): ok_cancel = False   # maybe filled — keep, reconcile next tick
                            if ok_cancel:
                                log(f"G-VETO {sym}: slow approach (win-high +{(hi/pos['limit']-1)*100:.2f}% < {L_GV_DROP}%) — cancel grind fill | {n_open(st)-1}/{TOTAL_SLOTS}")
                                _tg(f"⚪ <b>G-VETO {sym}</b> — slow grind approach, dip limit cancelled")
                                st["long"].remove(pos)
            continue
        # OPEN long: trailing + TP/SL/manual-close
        px = price_now(sym); e = pos["entry"]; mh = pos.get("maxhigh", e) or e
        if pos.get("mt_pending"):        # MAKER-TRAIL A/B: a post-only reduce-only exit is resting — wait for fill, else taker fallback
            gone = LIVE and abs(realpos.get((sym, "LONG"), 0.0)) < (pos["qty"]*0.5)
            if gone or now() >= pos.get("mt_deadline", 0):
                if LIVE:
                    if not gone and pos.get("mt_oid"): cancel(sym, pos["mt_oid"])     # maker didn't fill in time -> cancel it
                    if pos.get("bs_oid"): cancel_algo(sym, pos["bs_oid"])             # drop the backstop net either way
                    if not gone: mkt_sell(sym, pos["qty"])                            # taker fallback
                real = real_close_fill(sym, "SELL", pos.get("ft", now()-1800*1000))
                exit_px = real if real else (pos.get("mt_px") if gone else (px or pos.get("mt_px") or e))
                exit_tel(sym, "long", "TRAIL", pos, pos.get("mt_px", e), real)
                log(f"L-{'MTFILL(maker)' if gone else 'MTFALL(taker)'} {sym} @ {fmt(exit_px)} | {'saved taker fee' if gone else 'maker missed'}")
                book(st, "long", pos, exit_px, "TRAIL"); st["long"].remove(pos); continue
            continue                     # still waiting for the maker exit to fill
        # dynamic trailing stop (raises as price makes new highs after activation)
        dstop = pos["sl"]
        if L_TRAIL and mh >= e*(1+L_TRAIL_ACT): dstop = max(dstop, mh*(1-L_TRAIL))
        if LIVE and abs(realpos.get((sym, "LONG"), 0.0)) < (pos["qty"]*0.5):   # position gone
            won = bool(pos.get("tp_oid")) and isinstance(order_status(sym, pos["tp_oid"]), dict) and order_status(sym, pos["tp_oid"]).get("status") == "FILLED"
            algos = algo_open_ids(sym) if (pos.get("sl_oid") or pos.get("bs_oid")) else set()
            sl_fired = (not won) and bool(pos.get("sl_oid")) and str(pos["sl_oid"]) not in algos
            bs_fired = (not won) and bool(pos.get("bs_oid")) and str(pos["bs_oid"]) not in algos
            stopped = sl_fired or bs_fired
            if pos.get("tp_oid") and not won: cancel(sym, pos["tp_oid"])
            if pos.get("sl_oid") and not sl_fired: cancel_algo(sym, pos["sl_oid"])
            if pos.get("bs_oid") and not bs_fired: cancel_algo(sym, pos["bs_oid"])
            if sl_fired and bs_fired:                        # stop-LIMIT triggered but its child rested; backstop closed -> sweep the orphan child limit
                oo = _signed("/fapi/v1/openOrders", {"symbol": sym}, "GET")
                for o in (oo if isinstance(oo, list) else []):
                    if str(o.get("orderId")) != str(pos.get("tp_oid")): cancel(sym, o["orderId"])
            tag_l = "TP" if won else ("SL" if stopped else "closed")
            intended = pos["entry"]*(1+L_TP/100.0) if won else (pos["sl"] if (sl_fired and not bs_fired) else (px or pos["sl"]))
            real = real_close_fill(sym, "SELL", pos.get("ft", now()-1800*1000))   # BOOKING-FIX: book the ACTUAL fill, not the bounced mark
            exit_px = real if real else intended
            exit_tel(sym, "long", tag_l, pos, intended, real)
            book(st, "long", pos, exit_px, tag_l); st["long"].remove(pos); continue
        soft_stop = dstop                                   # software backstop threshold (MAKER-SL: pre-trail, poll only BELOW the taker backstop
        if L_MAKER_SL and pos.get("bs_oid") and dstop <= pos["sl"]:   # so the resting maker stop-limit gets its fill window)
            soft_stop = pos["sl"]*(1-L_MAKER_SL_GAP/100.0)
        if px and px <= soft_stop:                          # trailing/base stop hit
            tag = "TRAIL" if dstop > pos["sl"] else "SL"
            if LIVE and L_MAKER_TRAIL and tag == "TRAIL" and int(pos.get("ft", 0)) % 2 == 0:   # A/B maker arm (50/50 by fill-time parity)
                sp = specs(sym); mpx = round_tick(px*(1+L_MT_EDGE), sp)
                r = limit_sell_tp(sym, pos["qty"], mpx)      # post-only reduce-only GTX maker sell (reject => taker fallthrough)
                if isinstance(r, dict) and not r.get("_error") and r.get("orderId"):
                    if pos.get("tp_oid"): cancel(sym, pos["tp_oid"]); pos["tp_oid"] = None
                    if pos.get("sl_oid"): cancel_algo(sym, pos["sl_oid"]); pos["sl_oid"] = None
                    # KEEP bs_oid (native STOP_MARKET backstop) armed as the catastrophe net during the short wait
                    pos["mt_pending"] = True; pos["mt_oid"] = r.get("orderId"); pos["mt_px"] = mpx; pos["mt_deadline"] = now()+L_MT_WAIT
                    log(f"L-MAKER-TRAIL {sym}: resting maker exit @ {fmt(mpx)} (A/B), taker fallback in {L_MT_WAIT:.0f}s")
                    continue                                 # wait for the maker fill (handled at OPEN-top next ticks)
                # placement rejected/failed -> fall through to the normal taker close below
            if LIVE:
                if pos.get("tp_oid"): cancel(sym, pos["tp_oid"])
                if pos.get("sl_oid"): cancel_algo(sym, pos["sl_oid"])
                if pos.get("bs_oid"): cancel_algo(sym, pos["bs_oid"])
                mkt_sell(sym, pos["qty"])
            real = real_close_fill(sym, "SELL", pos.get("ft", now()-1800*1000))   # BOOKING-FIX: actual fill of the market close, not the poll px
            exit_px = real if real else px
            exit_tel(sym, "long", tag, pos, px, real)
            book(st, "long", pos, exit_px, tag); st["long"].remove(pos); continue
        if not LIVE and px and px >= e*(1+L_TP/100.0):      # paper TP
            book(st, "long", pos, e*(1+L_TP/100.0), "TP"); st["long"].remove(pos); continue
        if px: pos["maxhigh"] = max(mh, px); pos["minlow"] = min(pos.get("minlow") or e, px)   # MAE tracking (deepest dip)

# ========================= SHORT LEG (short-fade) =========================
def recent_dollar_vol(sym):
    k = ss.fetch_klines(sym, "1h", limit=50) or []
    if len(k) < 49: return 0.0
    k = k[:-1]; dv = sorted(float(x[5])*float(x[4]) for x in k[-48:])
    return dv[len(dv)//2] if dv else 0.0

def short_notional(equity, risk, mult=1.0):
    """HYBRID short sizing: min(equal-slot, equity*S_RISK_FRAC/stop_dist). Tight stop -> equal-slot (no balloon);
    wide stop -> risk-capped so a stop-out loses ~S_RISK_FRAC of the account (not up to 12% under plain equal-slot).
    `mult` (leg-B funding boost) scales the EQUAL-SLOT term BEFORE the min() so it NEVER breaches the risk cap:
    tight-stop fades scale up toward the cap; wide-stop fades (cap binds) stay at the 1% loss ceiling (boost inert)."""
    eq_slot = (equity/TOTAL_SLOTS)*LEV*mult*E_SIZE_FRAC   # E: fractional-Kelly equity dial
    return min(eq_slot, (equity*S_RISK_FRAC)/risk) if risk > 0 else eq_slot

def coin7d(sym, tf="1h", bpd=24):
    """Current 7d return % of a coin on its setup TF (for the short refresh-drop). None if not enough data."""
    trend_bars = S_TREND_DAYS*bpd
    k = ss.fetch_klines(sym, tf, limit=trend_bars+3) or []
    if len(k) < trend_bars+2: return None
    k = k[:-1]; C = [float(x[4]) for x in k]
    return (C[-1]/C[-1-trend_bars]-1)*100 if len(C) > trend_bars else None

def open_short(st, watch):
    # REFRESH-DROP (29-06): cancel a PENDING retest whose coin is NO LONGER WEAK (c7d rose above the cap) — the short
    # thesis (weak coin keeps falling) is dead = squeeze risk. Validated H1 ret/DD 0.20->0.30, OOS -0.02->+0.05,
    # May -5.2->-1.2%, Jun -10.5->-8.6% (realistic 1h-mtf engine). Mirrors the LONG refresh-drop.
    for pos in list(st["short"]):
        if pos["state"] != "PENDING": continue
        bpd = dict(S_SCAN_TFS).get(pos.get("tf", "1h"), 24)
        c7 = coin7d(pos["coin"], pos.get("tf", "1h"), bpd)
        if c7 is not None and c7 > S_COIN7D_CAP:
            if LIVE: cancel(pos["coin"], pos["oid"])
            log(f"S-DROP {pos['coin']}: c7d {c7:+.0f}% > {S_COIN7D_CAP:.0f}% — no longer weak, cancel retest | {n_open(st)-1}/{TOTAL_SLOTS}")
            _tg(f"⚪ <b>S-DROP {pos['coin']}</b> — un-weakened (c7d {c7:+.0f}%), retest cancelled")
            st["short"].remove(pos)
    if not can_open(st, "short"): return
    # FIX3 (15-07): FADE LOSS-STREAK BREAKER — after S_LOSS_STREAK_N consecutive real stops, pause arming ANY new fade until the cooldown.
    if S_LOSS_STREAK_N > 0 and now() < st.get("fade_paused_until", 0):
        if st.get("fade_pause_logged") != st["fade_paused_until"]:   # log S-STREAK-PAUSE once per pause window (not every cycle)
            log(f"S-STREAK-PAUSE — {st.get('fade_streak', 0)} consecutive fade stops, no new fades for {int((st['fade_paused_until']-now())/60)}m | {n_open(st)}/{TOTAL_SLOTS}")
            st["fade_pause_logged"] = st["fade_paused_until"]
        return
    # FIX5 BOOK-WIDE breaker: if N stops hit within the rolling window, pause arming ANY new fade (independent of the consecutive-streak pause).
    if S_BOOK_BREAKER_N > 0 and now() < st.get("book_paused_until", 0):
        if st.get("book_pause_logged") != st["book_paused_until"]:   # log S-BOOK-PAUSE once per pause window
            recent = len([t for t in st.get("fade_stop_times", []) if t >= now() - S_BOOK_WINDOW_SEC])
            log(f"S-BOOK-PAUSE — {recent} fade stops in {int(S_BOOK_WINDOW_SEC/60)}m (book-wide squeeze guard), all fades paused {int((st['book_paused_until']-now())/60)}m | {n_open(st)}/{TOTAL_SLOTS}")
            st["book_pause_logged"] = st["book_paused_until"]
        return
    have = held_all(st); fires = []                            # GREENER: cross-leg same-coin exclusion (was held_short only)
    for sym in watch:
        for tf, bpd in S_SCAN_TFS:
            fire, cts, lvl, stop, pump, c7 = short_signal(sym, tf, bpd)
            if not fire: continue
            bar_min = 1440.0/bpd
            if cts and (now()*1000 - cts)/60000 > bar_min + S_ENTRY_MAX_AGE_MIN: continue
            fires.append((pump, sym, cts, lvl, stop, c7, tf, bar_min))
    st["sfires"] = len(fires)
    if not fires: return
    fires.sort(reverse=True)
    for pump, sym, cts, lvl, stop, c7, tf, bar_min in fires:
        if not can_open(st, "short"): break
        if sym in have: continue
        # FIX4 (15-07): PER-COIN FADE COOLDOWN — don't re-fade a coin that just stopped out (it's trending up). Off when S_COIN_COOLDOWN_SEC<=0.
        if S_COIN_COOLDOWN_SEC > 0:
            last_sl = st.get("fade_sl_ts", {}).get(sym, 0)
            if last_sl and now() - last_sl < S_COIN_COOLDOWN_SEC:
                cdl = st.setdefault("fade_cd_logged", {})
                if cdl.get(sym) != last_sl:   # log S-COIN-CD once per distinct stop (not every cycle)
                    log(f"S-COIN-CD {sym} — last fade stop {int((now()-last_sl)/60)}m ago (< {int(S_COIN_COOLDOWN_SEC/60)}m cooldown), skip re-fade")
                    cdl[sym] = last_sl
                continue
        if st["last_sig"].get(sym) == cts: continue
        if not lvl or stop <= lvl: continue
        if not short_liq_ok(sym): continue                     # SHORT-LIQ CATALYST GATE (only fade when trapped LONGS liquidate)
        if GR_FILL_LIQ_MIN > 0:                                # GREENER S1: liq-at-fill gate, ARM side. FAIL-CLOSED on missing data.
            v = liq30(sym)
            if v is None:
                log(f"S-LIQGATE {sym}: liq data unavailable — FAIL-CLOSED, no arm"); continue
            if v < GR_FILL_LIQ_MIN:
                continue                                       # catalyst not alive -> don't even rest the limit
        if S_SL_FIXED > 0: stop = lvl*(1 + S_SL_FIXED)         # fixed-% fade stop (Artem 14-07): override the structural failed-pump high
        risk = (stop - lvl)/lvl                                 # stop distance = (failed-pump high - entry)/entry
        notional = short_notional(st["equity"], risk, funding_mult(sym, "short"))   # HYBRID sizing + B: boost scales equal-slot BEFORE the risk cap
        sp = specs(sym)
        if not sp: continue
        entry_px = round_tick(lvl, sp)
        qty, sp = calc_qty(sym, entry_px, notional)
        if not qty: continue
        cur = price_now(sym)
        if cur and cur >= entry_px: continue
        if cur and (entry_px-cur)/cur > S_MAX_RETEST_DIST: continue
        if LIVE:
            set_lev(sym); r = limit_short_open(sym, qty, entry_px)
            if isinstance(r, dict) and r.get("_error"):
                log(f"S RETEST reject {sym}: {r.get('_body','')[:80]}"); continue
            oid = r.get("orderId")
        else: oid = f"paper-{int(now())}-{sym}"
        fill_ttl = (bar_min * S_FILL_WIN_BARS) * 60
        st["short"].append({"coin": sym, "state": "PENDING", "oid": oid, "entry": entry_px, "qty": qty,
                            "stop": round_tick(stop, sp), "tp": None, "tp_oid": None, "sl_oid": None, "ts": now(),
                            "fill_deadline": now()+fill_ttl, "cts": cts, "tf": tf, "risk_frac": risk, "minlow": None, "added": False})
        have.add(sym); st["last_sig"][sym] = cts; took_slot(st, "short")
        log(f"S-ARM {sym} [{tf}] retest SELL {qty} @ {fmt(entry_px)} pump{pump:+.1f}% c7d{c7:+.0f}% SL+{risk*100:.1f}% | {n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])})")
        _tg(f"🟠 <b>S-ARM {sym}</b> [{tf}] retest short @ {fmt(entry_px)} | pump {pump:+.1f}% | weak7d {c7:+.0f}%")

def book_be_tighten(st):
    """FIX5b (16-07): book-wide breaker just tripped -> de-risk every OPEN fade to breakeven (once).
    RED (px>=entry): close now at market ~breakeven. GREEN (px<entry): keep the TP, rest the stop at breakeven (a reverting winner can still run).
    Booked with tag 'BE' so these squeeze-guard exits do NOT feed the streak/book-breaker counters (no cascade)."""
    for pos in list(st["short"]):
        if pos.get("state") != "OPEN": continue
        sym = pos["coin"]; entry = pos.get("entry")
        if not entry: continue
        sp = specs(sym); be = round_tick(entry, sp) if sp else entry
        if pos.get("stop", 0) <= be: continue                  # already at/below breakeven — nothing to tighten
        px = price_now(sym)
        if px is not None and px >= entry:                     # RED: exit now at ~breakeven
            if LIVE:
                if pos.get("tp_oid"): cancel(sym, pos["tp_oid"])
                if pos.get("sl_oid"): cancel_algo(sym, pos["sl_oid"])
                mkt_buy_close(sym, pos["qty"])
            real = real_close_fill(sym, "BUY", pos.get("ts", now()-1800*1000)) if LIVE else None
            exit_px = real if real else (px or be)
            log(f"S-BOOK-BE {sym} — squeeze guard: RED, closed ~breakeven @ {fmt(exit_px)}")
            exit_tel(sym, "short", "BE", pos, px or be, real)
            book(st, "short", pos, exit_px, "BE"); st["short"].remove(pos)
        else:                                                  # GREEN: keep TP, tighten stop to breakeven (software dstop closes it if it squeezes back; native = backstop)
            pos["stop"] = be
            if LIVE and pos.get("sl_oid"):
                cancel_algo(sym, pos["sl_oid"]); r = stop_market_buy(sym, be)
                pos["sl_oid"] = r.get("algoId") if isinstance(r, dict) and not r.get("_error") else None
            log(f"S-BOOK-BE {sym} — squeeze guard: GREEN, stop -> breakeven {fmt(be)}")

def manage_short(st):
    realpos = live_positions() if LIVE else {}
    if realpos is None: return   # RISK-1: positionRisk query failed (429/418/5xx) -> don't trust; skip mgmt this cycle (open positions stay protected by their native TP/backstop algos), retry next tick
    if st.get("book_be_pending"):   # FIX5b: a book-breaker trip flagged a one-shot breakeven-tighten of the open fades (run BEFORE the manage loop to avoid double-processing)
        st["book_be_pending"] = False
        book_be_tighten(st)
    for pos in list(st["short"]):
        sym = pos["coin"]; px = price_now(sym)
        if pos["state"] == "PENDING":
            if GR_FILL_LIQ_MIN > 0:                            # GREENER S1: the resting limit may only live while the catalyst is alive
                v = liq30(sym)                                 # (cached 55s -> ~1 API call/min per pending coin)
                if v is not None and v < GR_FILL_LIQ_MIN:
                    log(f"S-LIQDROP {sym}: liq30 ${v:,.0f} < ${GR_FILL_LIQ_MIN:,.0f} — catalyst died, cancelling retest | {n_open(st)}/{TOTAL_SLOTS}")
                    if LIVE and pos.get("oid"):
                        cancel(sym, pos["oid"])                # AUDIT BUG-1 FIX: do NOT remove blindly — the retest fills exactly when the
                        # flush ends (correlated race). Fall through: the order_status re-query below sees either CANCELED
                        # with executedQty=0 (-> removed) or executedQty>0 (-> opened WITH its SL). Never a naked short.
                    else:
                        st["short"].remove(pos); continue
            filled = False; entry = pos["entry"]
            if LIVE:
                stt = order_status(sym, pos["oid"]); s = stt.get("status") if isinstance(stt, dict) else None
                if s in ("FILLED", "PARTIALLY_FILLED") and float(stt.get("executedQty", 0)) > 0:
                    if s == "PARTIALLY_FILLED":   # ORPHAN-FIX (20-07): kill the UNFILLED REMAINDER of the entry limit NOW — else it rests on
                        cancel(sym, pos["oid"])   # the exchange, fills AFTER we close the tracked part, and becomes an untracked position with
                        stt2 = order_status(sym, pos["oid"])   # no TP/SL (live incident: ONDO 92.8 + MET 314 orphans, 20-07). Race-safe: if it
                        if isinstance(stt2, dict) and float(stt2.get("executedQty", 0) or 0) > 0:   # just fully filled, cancel errors and the
                            stt = stt2                                                              # re-query sees FILLED with the full qty.
                    filled = True; entry = float(stt.get("avgPrice") or pos["entry"]); pos["qty"] = float(stt["executedQty"])
                elif s in ("CANCELED", "EXPIRED", "REJECTED"):
                    if float(stt.get("executedQty", 0) or 0) > 0:   # AUDIT BUG-1: a cancelled order that FILLED first (S-LIQDROP race,
                        filled = True                               # expire race) = a REAL position -> open it with its SL, never orphan it
                        entry = float(stt.get("avgPrice") or pos["entry"]); pos["qty"] = float(stt["executedQty"])
                        log(f"S-FILL-ON-CANCEL {sym}: order cancelled but executedQty={pos['qty']} — opening WITH SL")
                    else:
                        st["short"].remove(pos); continue
            else:
                if px and px >= pos["entry"]: filled = True
            if filled:
                sp = specs(sym); risk = pos["risk_frac"]
                sl_px = round_tick(pos["stop"], sp)
                tp_px = None if S_CHAND else round_tick(entry*(1 - (S_TP_FIXED if S_TP_FIXED > 0 else S_R*risk)), sp)   # fixed-% TP (Artem 14-07) or R-target; chandelier=no fixed TP
                pos.update({"state": "OPEN", "entry": entry, "tp": tp_px, "stop": sl_px, "ts": now(), "minlow": entry})
                if LIVE:
                    if tp_px is not None:
                        r1 = limit_buy_tp(sym, pos["qty"], tp_px); pos["tp_oid"] = r1.get("orderId") if isinstance(r1, dict) and not r1.get("_error") else None
                    r2 = stop_market_buy(sym, sl_px); pos["sl_oid"] = r2.get("algoId") if isinstance(r2, dict) and not r2.get("_error") else None
                log(f"S-FILL {sym} [{pos['tf']}] @ {fmt(entry)} TP {fmt(tp_px)} SL {fmt(sl_px)} [SHORT OPEN]")
                _tg(f"🔻🔔 <b>S-FILL {sym}</b> [{pos['tf']}] @ {fmt(entry)} | TP {fmt(tp_px)} | SL {fmt(sl_px)}")
            elif S_RUNAWAY_DROP and px and abs(px/pos["entry"] - 1) > S_RUNAWAY_DROP/100.0:   # FADE runaway: price ran far from the retest -> won't fill usefully, free the slot
                if LIVE: cancel(sym, pos["oid"])
                log(f"S-RUNAWAY {sym}: ran {(px/pos['entry']-1)*100:+.1f}% from retest {fmt(pos['entry'])} — cancel & free slot | {n_open(st)-1}/{TOTAL_SLOTS}")
                _tg(f"⚪ <b>S-RUNAWAY {sym}</b> — ran {(px/pos['entry']-1)*100:+.1f}% from retest, cancelled")
                st["short"].remove(pos)
            elif now() > pos["fill_deadline"]:
                if LIVE: cancel(sym, pos["oid"])
                log(f"S-EXPIRE {sym} unfilled"); st["short"].remove(pos)
            continue
        # OPEN short: trailing + TP/SL/manual-close
        e = pos["entry"]; ml = pos.get("minlow", e) or e
        dstop = pos["stop"]
        if S_CHAND:                                       # C: fat-tail chandelier — wide 1h-ATR trail; PERSIST the ratchet so an ATR-fetch
            if ml <= e*(1-S_CHAND_ARM):                   # outage can never LOOSEN the stop back to the wide initial (validator-C fix)
                atr = short_atr(sym)
                if atr: pos["chand_stop"] = min(pos.get("chand_stop", pos["stop"]), ml + S_CHAND_K*atr)
            dstop = min(pos["stop"], pos.get("chand_stop", pos["stop"]))
        elif S_TRAIL and ml <= e*(1-S_TRAIL_ACT): dstop = min(dstop, ml*(1+S_TRAIL))
        if S_PYRAMID and not pos.get("added") and px and pos.get("risk_frac", 0) > 0:   # E: anti-martingale pyramid on a WINNING fade
            R_profit = (pos["entry"] - px) / (pos["entry"] * pos["risk_frac"])           # fade profit in R (price fell R*risk below entry)
            if R_profit >= S_PYR_R:
                add_qty = pos["qty"] * S_PYR_FRAC
                ok = True
                if LIVE:
                    sp = specs(sym)
                    if sp: add_qty = round(math.floor(add_qty / sp["step"]) * sp["step"], sp["qp"])
                    if add_qty <= 0: ok = False
                    else:
                        set_lev(sym); r = limit_short_open(sym, add_qty, round_tick(px, sp))
                        ok = isinstance(r, dict) and not r.get("_error")
                if ok and add_qty > 0:
                    blend = (pos["entry"]*pos["qty"] + px*add_qty) / (pos["qty"] + add_qty)
                    pos["qty"] += add_qty; pos["entry"] = blend; pos["stop"] = min(pos["stop"], blend)  # aggregate stop -> breakeven
                    pos["added"] = True; dstop = min(dstop, blend)
                    if LIVE and pos.get("sl_oid"):
                        cancel_algo(sym, pos["sl_oid"]); r2 = stop_market_buy(sym, round_tick(blend, specs(sym)))
                        pos["sl_oid"] = r2.get("algoId") if isinstance(r2, dict) and not r2.get("_error") else None
                    log(f"S-PYRAMID {sym}: +{S_PYR_FRAC}x @ {fmt(px)} (+{S_PYR_R}R) agg entry {fmt(blend)} stop->BE {fmt(blend)}")
                    _tg(f"🔻🔺 <b>S-PYRAMID {sym}</b> +{S_PYR_FRAC}x @ {fmt(px)} — stop to breakeven")
        if LIVE and abs(realpos.get((sym, "SHORT"), 0.0)) < (pos["qty"]*0.5):
            won = bool(pos.get("tp_oid")) and isinstance(order_status(sym, pos["tp_oid"]), dict) and order_status(sym, pos["tp_oid"]).get("status") == "FILLED"
            algos = algo_open_ids(sym) if pos.get("sl_oid") else set()
            stopped = (not won) and bool(pos.get("sl_oid")) and str(pos["sl_oid"]) not in algos
            if pos.get("tp_oid") and not won: cancel(sym, pos["tp_oid"])
            if pos.get("sl_oid") and not stopped: cancel_algo(sym, pos["sl_oid"])
            tag_s = "TP" if won else ("SL" if stopped else "closed")
            intended = pos["tp"] if won else (px or pos["stop"])
            real = real_close_fill(sym, "BUY", pos.get("ts", now()-1800*1000))   # BOOKING-FIX: actual close fill, not the mark
            exit_px = real if real else intended
            exit_tel(sym, "short", tag_s, pos, intended, real)
            book(st, "short", pos, exit_px, tag_s); st["short"].remove(pos); continue
        if px and px >= dstop:
            if LIVE:
                if pos.get("tp_oid"): cancel(sym, pos["tp_oid"])
                if pos.get("sl_oid"): cancel_algo(sym, pos["sl_oid"])
                mkt_buy_close(sym, pos["qty"])
            tag = "TRAIL" if dstop < pos["stop"] else "SL"
            real = real_close_fill(sym, "BUY", pos.get("ts", now()-1800*1000))   # BOOKING-FIX: actual fill of the market close, not the poll px
            exit_px = real if real else px
            exit_tel(sym, "short", tag, pos, px, real)
            book(st, "short", pos, exit_px, tag); st["short"].remove(pos); continue
        if not LIVE and pos.get("tp") and px and px <= pos["tp"]:   # C: chandelier has tp=None -> no fixed-TP exit, rides to trail/SL
            book(st, "short", pos, pos["tp"], "TP"); st["short"].remove(pos); continue
        if px: pos["minlow"] = min(ml, px); pos["maxadv"] = max(pos.get("maxadv") or e, px)   # MAE tracking (highest = worst for a short)

# ========================= MOMENTUM LEG (v4 "Drive") — long-only multi-setup on the movers universe =========================
# Ported from momentum_engine.py, REUSING ultra_bot plumbing (specs/calc_qty/round_tick/price_now/set_lev/order helpers/
# live_positions/real_close_fill/exit_tel/book). Positions live in st["mom"] tagged strat="mom" (uses "coin" key like the
# other legs). Sized equal-slot like every other slot (margin = equity/TOTAL_SLOTS, notional = margin*LEV) — one shared pool.
def mkl(sym, tf, n):
    try: return ss.fetch_klines(sym, tf, limit=n)
    except Exception: return None
def _pmap(fn, items, workers=None):
    """Run I/O-bound fn over items CONCURRENTLY, results aligned to input order (deterministic -> callers still sort/rank
    the same). Per-item exception -> None. Used to parallelize the movers kline fetches (super_scanner is stateless urllib
    per call = thread-safe). workers=1 forces serial."""
    workers = workers or M_FETCH_WORKERS
    if not items: return []
    if workers <= 1:
        out = []
        for it in items:
            try: out.append(fn(it))
            except Exception: out.append(None)
        return out
    def _one(it):
        try: return fn(it)
        except Exception: return None
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(_one, items))   # ex.map preserves input order
def _ema(v, n):
    a = 2/(n+1); e = [v[0]]*len(v)
    for i in range(1, len(v)): e[i] = v[i]*a + e[i-1]*(1-a)
    return e

_MUNI = []; _MUNI_TS = 0; _MRS = {}; _LAST_MOM_BAR = -1
_MSCAN = {"gate": 0, "trig": 0}     # per-15m-bar diagnostics: how many movers cleared the quality gate / had a fresh trigger
def movers_universe():
    """LIVE movers: rank the top-M_POOL perps by 24h vol, then by recency-weighted RS vs BTC (7/14/30d daily), with a
    $M_LIQ_FLOOR median-7d-$vol floor. Returns [(sym, rs_pct 0..100)] top M_N_UNI (rs_pct = percentile within the pool)."""
    try:
        t = ss.fetch_tickers() or []
        rows = [x for x in t if x["symbol"].endswith("USDT") and "_" not in x["symbol"]
                and x["symbol"] not in ("BTCUSDT", "ETHUSDT") and "USDC" not in x["symbol"]]
        rows.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        cands = [x["symbol"] for x in rows[:M_POOL]]
    except Exception as e:
        log(f"M-universe fetch error: {e}"); return []
    bd = mkl("BTCUSDT", "1d", 40)
    if not bd or len(bd) < 32: return []
    bc = [float(x[4]) for x in bd]
    def _ret(cc, lag): return cc[-1]/cc[-1-lag]-1 if len(cc) > lag else 0.0
    b7, b14, b30 = _ret(bc, 7), _ret(bc, 14), _ret(bc, 30)
    daily = _pmap(lambda s: mkl(s, "1d", 40), cands)   # SCAN-PERF: fetch all pool daily klines CONCURRENTLY (was ~144s serial)
    scored = []
    for sym, d in zip(cands, daily):
        if not d or len(d) < 31: continue
        cc = [float(x[4]) for x in d]; vv = [float(x[5]) for x in d]
        qv7 = sorted(vv[x]*cc[x] for x in range(len(cc)-7, len(cc)))
        if qv7[len(qv7)//2] < M_LIQ_FLOOR: continue
        rs = 0.5*(_ret(cc, 7)-b7) + 0.3*(_ret(cc, 14)-b14) + 0.2*(_ret(cc, 30)-b30)
        scored.append((rs, sym))
    scored.sort(reverse=True)
    top = scored[:M_N_UNI]; n = len(top)
    if n == 0: return []
    if n == 1: return [(top[0][1], 100.0)]
    rs_pct = {sym: 100.0*i/(n-1) for i, (_, sym) in enumerate(sorted(top, key=lambda x: x[0]))}
    return [(sym, rs_pct[sym]) for _, sym in top]

_FCACHE = {}                        # {sym: (15m-bar-id, features)} — fetch each coin's klines ONCE per 15m bar, not every poll
def mom_features(sym):
    """15m + 1h features for the momentum signal. Returns dict or None."""
    k15 = mkl(sym, "15m", 130)
    if not k15 or len(k15) < 60: return None
    o = [float(x[1]) for x in k15]; h = [float(x[2]) for x in k15]; l = [float(x[3]) for x in k15]
    c = [float(x[4]) for x in k15]; v = [float(x[5]) for x in k15]
    k1 = mkl(sym, "1h", 130)
    if not k1 or len(k1) < 100: return None
    c1 = [float(x[4]) for x in k1]; h1 = [float(x[2]) for x in k1]; l1 = [float(x[3]) for x in k1]
    e7, e25, e99 = _ema(c1, 7), _ema(c1, 25), _ema(c1, 99)
    tr = [h1[0]-l1[0]] + [max(h1[i]-l1[i], abs(h1[i]-c1[i-1]), abs(l1[i]-c1[i-1])) for i in range(1, len(c1))]
    atr1 = sum(tr[-14:])/14
    denom = sum(abs(c1[i]-c1[i-1]) for i in range(len(c1)-20, len(c1)))
    er = abs(c1[-1]-c1[-21])/denom if denom > 0 else 0.0
    spread = (e7[-1]-e99[-1])/e99[-1] if e99[-1] else 0.0
    ext = (c1[-1]-e25[-1])/atr1 if atr1 else 0.0
    ema50 = _ema(c, 50)[-1]
    return dict(o=o, h=h, l=l, c=c, v=v, up=(e7[-1] > e25[-1] > e99[-1]), spread=spread, cgt7=(c1[-1] > e7[-1]),
                er=er, ext=ext, atr1h=atr1, ema50=ema50)
def get_feat(sym):
    bar = int(now()//900)           # 15m bar id
    hit = _FCACHE.get(sym)
    if hit and hit[0] == bar: return hit[1]
    f = mom_features(sym)
    if f: _FCACHE[sym] = (bar, f)
    return f

def rpbase(rp): return rp if rp is not None else 50.0
def mom_signal(sym, rs_pct):
    """Return (setup, entry_ref, stop_px) for the best-firing LONG momentum setup on the CLOSED 15m bar, else None."""
    f = get_feat(sym)
    if not f: return None
    c, h, l, o, v = f["c"], f["h"], f["l"], f["o"], f["v"]
    j = len(c)-1                                     # last CLOSED 15m bar
    base_h = max(h[j-24:j]); base_l = min(l[j-24:j])
    tight = (base_h-base_l)/(sum(c[j-24:j])/24) <= M_BASE_MAX
    rpos = (c[j]-l[j])/(h[j]-l[j]) if h[j] > l[j] else 0.5
    bar_rng = h[j]-l[j]; avg_rng = sum(h[k]-l[k] for k in range(j-20, j))/20
    climax_up = bar_rng > 3*avg_rng and rpos < 0.33
    av = sum(v[j-96:j])/96 if j >= 96 else (sum(v[:j])/j if j else 0)
    vok = av > 0 and v[j] >= M_VMULT*av
    ema = f["ema50"]; atrp = f["atr1h"]
    # shared long quality gate (top-decile RS uptrend, established spread, directional, not climaxed)
    if not (rs_pct >= M_RS_LONG and f["up"] and f["spread"] >= M_SPREAD_MIN and f["cgt7"] and f["er"] >= M_ER_MIN
            and c[j] > ema and f["ext"] <= M_EXT_CAP and not climax_up):
        return None
    _MSCAN["gate"] += 1                                  # DIAG: cleared the shared quality gate (may still lack a trigger)
    def cap(px, floor): return max(px*(1-M_STOP_CAP), px-M_SL_ATR*atrp, floor)   # tight R = closest of {2%, 2xATR, struct}
    cands = []
    if c[j] > base_h and tight and vok:                                    # (a) base-BREAKOUT
        stp = cap(c[j], base_l)
        if stp < c[j]: cands.append((rpbase(rs_pct)+120, "break", c[j], stp))
    if j >= 4:                                                             # (b) IGNITION (+3.5% over 2 bars on vol)
        retN = c[j]/c[j-2]-1
        if retN >= M_IG_MOM and vok and rpos >= 0.6:
            stp = cap(c[j], min(l[j-2:j+1]))
            if stp < c[j]: cands.append((rpbase(rs_pct)+30+retN*200, "ignite", c[j], stp))
    if j >= 31:                                                            # (c) RETEST (break -> pullback -> reclaim)
        broke = any(c[k] > max(h[k-24:k]) for k in range(j-6, j))
        held = min(l[j-3:j]) > ema*0.99
        if broke and held and c[j] > h[j-1] and rpos >= 0.55 and c[j] > ema:
            stp = cap(c[j], min(l[j-3:j+1]))
            if stp < c[j]: cands.append((rpbase(rs_pct)*0.9, "retest", h[j-1], stp))
    if not cands: return None
    _MSCAN["trig"] += 1                                  # DIAG: cleared the gate AND has a fresh break/ignite/retest trigger
    cands.sort(reverse=True)
    _, setup, ref, stop = cands[0]
    return setup, ref, stop

def place_entry_mom(sym, setup, ref, stop, notional):
    """Place the per-setup momentum entry. Returns (order, entry_px_est, qty) or (None, None, None). not-LIVE = simulate."""
    px = price_now(sym) or ref
    if setup == "retest":                                                  # passive GTX maker limit at the flipped level
        entry_px = ref
        qty, sp = calc_qty(sym, entry_px, notional)
        if not qty: return None, None, None
        if not LIVE: return {"paper": True, "orderId": f"mp{int(now())}"}, entry_px, qty
        return limit_buy(sym, qty, round_tick(entry_px, sp)), entry_px, qty
    cap = M_CHASE.get(setup, 0.0015)                                       # break/ignite: IOC marketable-limit chase-capped
    entry_px = px*(1+cap)
    qty, sp = calc_qty(sym, entry_px, notional)
    if not qty: return None, None, None
    if not LIVE: return {"paper": True, "orderId": f"mp{int(now())}"}, px, qty
    return ioc_buy(sym, qty, round_tick(entry_px, sp)), px, qty

def open_momentum(st):
    """Fire new momentum entries for free slots from the movers universe. HEAVY scan runs ONCE per new 15m bar (signals
    only change on bar close). Governed by the shared slot allocator (can_open 'mom' enforces mom<=2 AND dip+mom<=3)."""
    global _MUNI, _MUNI_TS, _MRS, _LAST_MOM_BAR
    if not M_ENABLED or not can_open(st, "mom"): return
    cur_bar = int(now()//900)
    if cur_bar == _LAST_MOM_BAR: return                  # already scanned this 15m bar
    _LAST_MOM_BAR = cur_bar
    if now()-_MUNI_TS > M_UNIVERSE_SEC or not _MUNI:
        u = movers_universe()
        if u: _MUNI = u; _MUNI_TS = now(); _MRS = dict(u); log(f"M-UNIVERSE re-ranked: {len(u)} movers, top={_MUNI[0][0]}")
    if st.get("day_eq") and st["equity"] > 0 and (st["equity"]/st["day_eq"]-1) <= -0.08:
        return                                           # -8% day: halt NEW momentum entries (armed runners keep riding)
    open_syms = held_all(st)   # AUDIT BUG-4: cross-leg exclusion incl. squeeze (a long on a squeeze-shorted coin = hedge-mode fee bleed)
    margin = (st["equity"]/TOTAL_SLOTS) if st["equity"] else 0
    _MSCAN["gate"] = 0; _MSCAN["trig"] = 0                         # DIAG: reset per-bar scan counters
    armed_before = len(st.get("mom", []))
    _pmap(get_feat, [s for s, _ in _MUNI if s not in open_syms])   # SCAN-PERF: warm the per-15m-bar feature cache CONCURRENTLY
    for sym, rp in _MUNI:                                          # (the serial mom_signal loop below then hits the cache = fast)
        if not can_open(st, "mom"): break
        if sym in open_syms: continue
        sig = mom_signal(sym, rp)
        if not sig: continue
        setup, ref, stop = sig
        notional = margin*LEV
        if LIVE: set_lev(sym)
        r_ord, entry_px, qty = place_entry_mom(sym, setup, ref, stop, notional)
        if not qty or not r_ord or (isinstance(r_ord, dict) and r_ord.get("_error")):
            log(f"M-REJECT {sym} [{setup}]: {r_ord}"); continue
        state = "PENDING"
        if LIVE and setup in ("break", "ignite"):        # IOC returns FILLED/EXPIRED immediately
            stt = r_ord.get("status", ""); exq = float(r_ord.get("executedQty", 0) or 0)
            if stt == "EXPIRED" or exq <= 0:
                log(f"M-IOC-NOFILL {sym} [{setup}] (chase cap) — skip"); continue
            ap = float(r_ord.get("avgPrice", 0) or 0)
            if ap > 0: entry_px = ap                     # book the REAL fill price
            qty = exq; state = "OPEN"
        R = abs(entry_px-stop)
        pos = dict(coin=sym, strat="mom", setup=setup, entry=entry_px, stop=stop, sl=stop, R=R, brk=ref, qty=qty,
                   oid=str(r_ord.get("orderId", "")), state=state, armed=False, best=entry_px, be=entry_px,
                   fill_ts=now(), created=now(), rs=rp, minlow=entry_px, last_hi_ts=now(),
                   added=False, add_entry=None, add_qty=0.0)
        st["mom"].append(pos); open_syms.add(sym); took_slot(st, "mom")
        if state == "OPEN" and LIVE:
            _place_maker_sl_mom(sym, pos)
            _tg(f"🚀🔔 <b>M-FILL {sym}</b> [{setup}] @ {fmt(entry_px)} | stop {fmt(stop)}")
        log(f"M-ARM {sym} [{setup}] @ {fmt(entry_px)} stop {fmt(stop)} (R {R/entry_px*100:.1f}%) RS{rp:.0f} | "
            f"{n_open(st)}/{TOTAL_SLOTS} (L{len(st['long'])}/S{len(st['short'])}/M{len(st['mom'])})")
        _tg(f"🚀 <b>M-ARM {sym}</b> [{setup}] @ {fmt(entry_px)} | stop {fmt(stop)} (R {R/entry_px*100:.1f}%) | RS {rp:.0f}")
    armed_now = len(st.get("mom", [])) - armed_before
    log(f"M-SCAN: {len(_MUNI)} movers | {_MSCAN['gate']} cleared quality-gate | {_MSCAN['trig']} had a fresh trigger | "
        f"{armed_now} armed this bar | held {len(st.get('mom', []))}/{M_MOM_CAP} | slot_free={can_open(st, 'mom')}")

def _place_maker_sl_mom(sym, pos):
    """MAKER-SL OCO on a momentum fill: native stop-LIMIT at the SL + STOP_MARKET backstop M_MAKER_SL_GAP below."""
    sp = specs(sym)
    if not sp: return
    lp = live_positions()
    if not isinstance(lp, dict): return
    held = lp.get((sym, "LONG" if HEDGE else "BOTH"), 0.0)
    if held <= 0: return
    qty = round(math.floor(held/sp["step"])*sp["step"], sp["qp"])
    trig = round_tick(pos["stop"], sp)
    r1 = stop_limit_sell(sym, qty, trig, trig)
    r2 = stop_market_sell(sym, round_tick(pos["stop"]*(1-M_MAKER_SL_GAP), sp))
    if isinstance(r1, dict) and r1.get("_error"): log(f"M maker-SL reject {sym}: {r1}")
    if isinstance(r2, dict) and r2.get("_error"): log(f"M backstop reject {sym}: {r2}")

def _close_mom(st, pos, px, tag):
    """Close a momentum position: LIVE taker-close any residual + cancel algos, book the REAL fill, telemetry, remove."""
    sym = pos["coin"]; real = None
    if LIVE:
        lp = live_positions()
        held_amt = lp.get((sym, "LONG" if HEDGE else "BOTH"), 0.0) if isinstance(lp, dict) else 0.0
        if held_amt > 0: mkt_sell(sym, abs(held_amt))
        for a in (_signed("/fapi/v1/openAlgoOrders", {}, "GET") or []):
            if isinstance(a, dict) and a.get("symbol") == sym: cancel_algo(sym, a.get("algoId"))
        real = real_close_fill(sym, "SELL", int((pos.get("fill_ts", now())-5)*1000))
    exit_px = real if real else px
    try: exit_tel(sym, "long", tag, pos, px, real)      # momentum is long-direction; reuses the long telemetry columns (base entry)
    except Exception: pass
    book_pos = pos
    if pos.get("added") and pos.get("add_qty"):         # PYRAMID: book the blended (base + add) position for correct total PnL
        base_q = pos["qty"]; add_q = pos["add_qty"]; tot = base_q + add_q
        blend = (pos["entry"]*base_q + pos["add_entry"]*add_q)/tot
        book_pos = dict(pos, entry=blend, qty=tot)
    book(st, "mom", book_pos, exit_px, tag)
    try: st["mom"].remove(pos)
    except ValueError: pass

def manage_momentum(st):
    """2s management of open momentum legs: detect fills, arm at +1R, velocity chandelier trail, fast-fakeout, MAKER-SL."""
    if not M_ENABLED or not st.get("mom"): return
    livep = {}
    if LIVE:
        livep = live_positions()
        if livep is None: return                         # RISK-1: API error -> skip cycle (positions protected by native algos)
    for pos in list(st["mom"]):
        sym = pos["coin"]
        f = get_feat(sym)
        if not f: continue
        c = f["c"][-1]; atrp = f["atr1h"]; R = pos["R"]; ent = pos["entry"]
        px = price_now(sym) or c
        held_amt = livep.get((sym, "LONG" if HEDGE else "BOTH"), 0.0) if LIVE else (0.0 if pos["state"] == "PENDING" else 1.0)
        # ---- PENDING: detect fill or stale/runaway cancel ----
        if pos["state"] == "PENDING":
            filled = (held_amt > 0) if LIVE else (pos["setup"] != "retest")   # paper: IOC fills instantly, retest waits
            if filled:
                pos["state"] = "OPEN"; pos["fill_ts"] = now()
                if LIVE: _place_maker_sl_mom(sym, pos)
                log(f"M-FILL {sym} [{pos['setup']}] @ {fmt(ent)} stop {fmt(pos['stop'])} [MOM OPEN]")
                _tg(f"🚀🔔 <b>M-FILL {sym}</b> [{pos['setup']}] @ {fmt(ent)} | stop {fmt(pos['stop'])}")
                continue
            pxn = price_now(sym) or c
            age_min = (now()-pos["created"])/60
            runaway = pxn > pos["entry"]*(1+M_PEND_RUNAWAY)
            stale = age_min > M_PEND_TTL_MIN.get(pos["setup"], 30)
            if runaway or stale:
                if LIVE and pos.get("oid"): cancel(sym, pos["oid"])
                log(f"M-DROP {sym} [{pos['setup']}] pending cancelled ({'runaway' if runaway else 'stale'})")
                _tg(f"⚪ <b>M-DROP {sym}</b> [{pos['setup']}] — {'runaway' if runaway else 'stale'}")
                st["mom"].remove(pos)
            continue
        # ---- OPEN: manage the runner ----
        if LIVE and held_amt <= 0:                       # position gone (native stop/trail filled) -> book it
            _close_mom(st, pos, c, "STOP/EXIT"); continue
        pos["best"] = max(pos.get("best", ent), px)
        pos["minlow"] = min(pos.get("minlow", ent), px)  # MAE (deepest excursion)
        if px <= pos["stop"] and not pos["armed"]:       # hard stop (software backstop; native maker-SL primary)
            _close_mom(st, pos, pos["stop"], "SL"); continue
        if not pos["armed"]:                             # fast-fakeout (pre-arm): cut small, fast
            rng = max(f["h"][-1]-f["l"][-1], 1e-9); body = abs(c-f["o"][-1])
            if c < pos["brk"]: _close_mom(st, pos, px, "FAKEOUT-in"); continue
            if c < ent and body >= 0.7*rng: _close_mom(st, pos, px, "FLIP"); continue
            age_bars = (now()-pos["fill_ts"])/900
            if age_bars >= M_FT_WIN and pos["best"] < ent + 0.5*R: _close_mom(st, pos, px, "no-follow"); continue
            if (now()-pos["fill_ts"]) > M_MAX_HOLD_H*3600: _close_mom(st, pos, px, "time"); continue
        if not pos["armed"] and px >= ent + M_ARM_R*R:   # arm at +1R -> breakeven
            pos["armed"] = True; pos["be"] = ent + (2*M_FEE_SIDE)*ent; pos["last_hi_ts"] = now()
            log(f"M-ARM+1R {sym} @ {fmt(px)} — trailing")
            _tg(f"⏫ <b>M-ARM+1R {sym}</b> @ {fmt(px)} — trailing")
        if M_PYRAMID and pos["armed"] and not pos.get("added") and px >= ent + M_PYRAMID_R*R:   # PHASE-2 pyramid: add to a proven runner
            pos["added"] = True; pos["add_entry"] = px; pos["add_qty"] = pos["qty"]*M_PYRAMID_FRAC
            pos["stop"] = max(pos["stop"], pos["be"])    # aggregate stop -> breakeven: the enlarged stack can no longer lose
            if LIVE:
                sp = specs(sym)
                if sp:
                    aq = round(math.floor((pos["qty"]*M_PYRAMID_FRAC)/sp["step"])*sp["step"], sp["qp"])
                    if aq > 0:
                        set_lev(sym); ioc_buy(sym, aq, round_tick(px*(1+0.002), sp)); pos["add_qty"] = aq
                        for a in (_signed("/fapi/v1/openAlgoOrders", {}, "GET") or []):   # re-arm maker-SL for the ENLARGED position at BE
                            if isinstance(a, dict) and a.get("symbol") == sym: cancel_algo(sym, a.get("algoId"))
                        _place_maker_sl_mom(sym, pos)
            log(f"M-PYRAMID {sym}: +{M_PYRAMID_FRAC}x @ {fmt(px)} (>= +{M_PYRAMID_R}R) — agg stop -> BE {fmt(pos['stop'])}")
            _tg(f"🔺 <b>M-PYRAMID {sym}</b> +{M_PYRAMID_FRAC}x @ {fmt(px)} — stop to breakeven")
        if pos["armed"]:                                 # WIDE chandelier trail (NO fixed TP — full runner; the fat tail IS the edge)
            if px >= pos["best"]: pos["last_hi_ts"] = now()
            since_hi = (now()-pos.get("last_hi_ts", pos["fill_ts"]))/900
            TW = M_TRAIL_K                                # base width (~4× 1h-ATR, tuned on real opps — wide keeps runners)
            if since_hi <= 3: TW = M_TRAIL_K*1.15         # widen while extending fast (don't get shaken off the parabola)
            # NB: NEVER tighten when stalled — the old TW->1.0 rule clipped winners that later resumed (biggest EV leak, removed).
            floor = max(pos["be"], pos["best"]-TW*atrp, pos["stop"])
            if px <= floor: _close_mom(st, pos, floor, "TRAIL"); continue
    # daily hard-flatten (crash net): flatten non-armed momentum legs on a -12% day
    if st.get("day_eq") and st["equity"] > 0 and (st["equity"]/st["day_eq"]-1) <= -M_DAILY_HARD:
        for pos in list(st["mom"]):
            if not pos.get("armed"):
                _close_mom(st, pos, price_now(pos["coin"]) or pos["entry"], "DAILY-HARD")

# ========================= BOOK =========================
def book(st, leg, pos, exit_px, tag, count_wl=True):
    entry = pos["entry"] or pos.get("limit")
    is_taker_entry = (leg in ("trend", "squeeze"))       # D#7: trend enters IOC; GREENER E5 squeeze enters taker market
    fee = (FEE_TK if is_taker_entry else FEE_MK) + (FEE_MK if tag == "TP" else FEE_TK)
    if leg in ("short", "squeeze"): ret = (entry-exit_px)/entry*100 - fee   # both are SHORT-direction
    else: ret = (exit_px-entry)/entry*100 - fee          # LONG and MOM are both long-direction
    pnl = (entry*pos["qty"])*ret/100
    st["realized"] += pnl
    if count_wl: st["wins" if ret > 0 else "losses"] += 1   # D#4: scale-out partials don't each count as a W/L (once per trade)
    if not LIVE: st["equity"] += pnl
    if leg == "long":   # loss-streak breaker is driven by the DIP leg only (its regime dependence); mom/fade don't pause dip
        if ret > 0: st["loss_streak"] = 0; st["paused_until"] = 0
        else:
            st["loss_streak"] = st.get("loss_streak", 0) + 1
            if st["loss_streak"] >= L_LOSS_STREAK_N: st["paused_until"] = now() + L_COOLDOWN_SEC
    elif leg in ("short", "squeeze"):   # FADE-QUALITY (15-07): FIX3 loss-streak breaker + FIX4 per-coin SL cooldown. GREENER: squeeze stops feed the SAME short-side counters (side-aware breaker — a squeeze SL is a short-side stop; the pause blocks BOTH fade and squeeze arming). BE-tighten stays fade-only by design (a squeeze's thesis IS holding through the wiggle).
        if tag == "SL" and ret < 0:   # ONLY a REAL negative-PnL auto-stop counts (wins/trails/manual mislabels excluded)
            st["fade_sl_ts"] = st.get("fade_sl_ts", {}); st["fade_sl_ts"][pos["coin"]] = now()   # FIX4: remember this coin just stopped out
            st["fade_streak"] = st.get("fade_streak", 0) + 1                                      # FIX3: consecutive-stop counter
            if S_LOSS_STREAK_N > 0 and st["fade_streak"] >= S_LOSS_STREAK_N:
                st["fade_paused_until"] = now() + S_STREAK_COOLDOWN_SEC
            if S_BOOK_BREAKER_N > 0:   # FIX5 BOOK-WIDE: N stops within a rolling window (time-based, wins don't reset) -> pause ALL fades
                lst = [t for t in st.get("fade_stop_times", []) if t >= now() - S_BOOK_WINDOW_SEC]
                lst.append(now()); st["fade_stop_times"] = lst
                if len(lst) >= S_BOOK_BREAKER_N:
                    st["book_paused_until"] = now() + S_BOOK_COOLDOWN_SEC
                    if S_BOOK_BE_TIGHTEN:   # FIX5b: flag the open fades for a one-shot breakeven-tighten (done in manage_short next tick, no recursion)
                        st["book_be_pending"] = True
        elif tag == "BE":   # FIX5b squeeze-guard breakeven exit — neither a real stop nor a trade-quality win; do NOT touch the cluster counters
            pass
        elif ret > 0 or tag == "TRAIL":   # a fade WIN / trail-lock breaks the CONSECUTIVE streak (FIX3) — but NOT the book-wide time-window (FIX5)
            st["fade_streak"] = 0; st["fade_paused_until"] = 0
    icon = {"long": "🟢", "mom": "🚀", "snap": "🟩", "trend": "📈", "short": "🔻"}.get(leg, "🔻")
    log(f"{leg.upper()}-CLOSE {pos['coin']} @ {fmt(exit_px)} ({tag} {ret:+.2f}% = ${pnl:+.3f}) | {st['wins']}W/{st['losses']}L realized ${st['realized']:+.2f}")
    _tg(f"{'💚' if ret>0 else '🛑'} {icon} <b>{tag} {pos['coin']}</b> @ {fmt(exit_px)} ({ret:+.2f}%)\neq ${st['equity']:.2f} | {st['wins']}W/{st['losses']}L")

def reconcile_orders(st):
    """STARTUP RECONCILIATION (LIVE only): the bot's state is the source of truth. Cancel any EXCHANGE order the bot
    does NOT track (orphans/duplicates left when a restart lands between an arm and the next state-save). Warn — but do
    NOT auto-close — on untracked positions. Fixes the 'phantom slots' bug (exchange had 5 long limits, bot tracked 2)."""
    if not LIVE: return 0
    tracked = set()
    for leg in ("long", "short", "mom", "snap", "trend", "squeeze"):   # AUDIT BUG-5: squeeze incl.
        for p in st.get(leg, []):
            for kf in ("oid", "tp_oid", "sl_oid"):
                v = p.get(kf)
                if v:
                    try: tracked.add(int(v))
                    except (TypeError, ValueError): pass
    try:
        oo = _signed("/fapi/v1/openOrders", {}, "GET")
    except Exception as e:
        log(f"reconcile: openOrders query failed ({e}) — skipping"); return 0
    if not isinstance(oo, list): return 0
    cancelled = 0
    for o in oo:
        try: oid = int(o.get("orderId", 0))
        except (TypeError, ValueError): continue
        if oid and oid not in tracked:
            sym = o["symbol"]; r = cancel(sym, oid)
            ok = isinstance(r, dict) and (r.get("status") == "CANCELED" or r.get("orderId"))
            log(f"reconcile: CANCEL orphan {sym} {o.get('side')} {o.get('type')} oid={oid} px={o.get('price')} -> {'OK' if ok else r}")
            if ok: cancelled += 1
    tracked_pos = ({(p["coin"], "LONG") for p in st.get("long", []) if p.get("state") == "OPEN"} |
                   {(p["coin"], "LONG") for p in st.get("mom", []) if p.get("state") == "OPEN"} |
                   {(p["coin"], "LONG") for p in st.get("snap", []) if p.get("state") == "OPEN"} |
                   {(p["coin"], "LONG") for p in st.get("trend", []) if p.get("state") == "OPEN"} |
                   {(p["coin"], "SHORT") for p in st.get("short", []) if p.get("state") == "OPEN"} |
                   {(p["coin"], "SHORT") for p in st.get("squeeze", []) if p.get("state") == "OPEN"})   # AUDIT BUG-5
    try:
        for (sym, side), amt in live_positions().items():
            if abs(amt) > 0 and (sym, side) not in tracked_pos:
                log(f"reconcile: ⚠️ UNTRACKED POSITION {sym} {side} amt={amt} — NOT auto-closing, review")
                _tg(f"⚠️ <b>reconcile</b>: untracked position {sym} {side} amt={amt} — review")
    except Exception: pass
    if cancelled: log(f"reconcile: cancelled {cancelled} orphan order(s) — exchange now matches bot state")
    return cancelled

# ========================= MAIN =========================
def main():
    if STATUS: print(json.dumps(load_state(), indent=2)); return
    if LIVE: detect_hedge()
    st = load_state()
    if LIVE:
        bal = live_balance()
        if bal: st["equity"] = bal
    elif not st["equity"]: st["equity"] = 35.0
    save_state(st)
    stfs = "+".join(tf for tf, _ in S_SCAN_TFS)
    mband = (f"MOM=v4 movers 3-setup (cap {M_MOM_CAP}, chandelier, no-TP)" if M_ENABLED else "MOM=OFF")
    aband = (f"SNAP=liq-snapback (A: THR${SNAP_THR/1000:.0f}k TP+{SNAP_TP*100:.1f}% cap{SNAP_CAP} liq-top{SNAP_UNIV})" if SNAP_ENABLED else "SNAP=OFF")
    bband = f"FUND-B=snap<{B_FUND_LO}/fade>{B_FUND_HI} x{B_FUND_BOOST}" if (B_FUND_LO or B_FUND_HI) else "FUND-B=OFF"
    dband = (f"TREND=D:donchian{T_DON_N}/{T_TRAIL_N} SMA{T_SMA} ATR{T_ATR_K}x{' scale-out' if T_SCALE else ''} cap{T_CAP}{' BTC-regime' if T_BTC_REGIME else ''}" if T_ENABLED else "TREND=OFF")
    eband = f"E={'pyramid+' if S_PYRAMID else ''}Kelly{E_SIZE_FRAC}" + (f" (fade +{S_PYR_R}R x{S_PYR_FRAC}->BE)" if S_PYRAMID else "")
    log(f"GREENER BOT ({'🔴 LIVE' if LIVE else '📝 PAPER'}) start — eq ${st['equity']:.2f} | {TOTAL_SLOTS} slots "
        f"(dip≤{M_DIP_CAP}/fade≤{S_FADE_CAP}/sqf≤{SQF_CAP}/mom≤{M_MOM_CAP}/snap≤{SNAP_CAP}, 🔒total-long≤{M_TOTAL_LONG_CAP}) {LEV}x | "
        f"{'S1-LIQGATE@fill≥$'+format(GR_FILL_LIQ_MIN,',.0f') if GR_FILL_LIQ_MIN else 'S1-liqgate OFF'} | "
        f"{'E5-SQF ON (S5≥$'+format(SQF_S5_MIN,',.0f')+' pump≥'+format(SQF_PUMP_MIN,'.1f')+'% hold'+str(SQF_HOLD_MIN)+'m SL+'+format(SQF_SL*100,'.1f')+'%'+(' maker+'+format(SQF_MAKER_BUMP*100,'.1f')+'%/'+str(SQF_MAKER_TTL_SEC//60)+'m' if SQF_MAKER else '')+')' if SQF_ENABLED else 'E5-SQF off'} | "
        f"DIP=maker dip-buy (TP+{L_TP}% trail@{L_TRAIL_ACT*100:.1f}%/{L_TRAIL*100:.1f}%{' OI-HARD' if L_OI_CAPIT else (f' OI-SOFT{L_OI_CAPIT_SOFT}' if L_OI_CAPIT_SOFT else '')}) | "
        f"FADE={stfs} (R{S_R} {f'CHANDELIER{S_CHAND_K}xATR@{S_CHAND_ARM*100:.0f}%' if S_CHAND else f'trail@{S_TRAIL_ACT*100:.0f}%/{S_TRAIL*100:.1f}%'}{f' liq-gate≥${S_LIQ_MIN:.0f}' if S_LIQ_MIN else ''}) | {mband} | {aband} | {bband} | {dband} | {eband} | hedge={HEDGE}")
    if (GR_FILL_LIQ_MIN > 0 or SQF_ENABLED) and not COINALYZE_KEY:   # AUDIT BUG-7: a missing key silently kills BOTH short engines
        log("🔴🔴 COINALYZE_KEY MISSING but GR_FILL_LIQ_MIN/SQF_ENABLED are ON — the S1 fade gate FAIL-CLOSES every arm and E5 self-idles. THE SHORT SIDE WILL NOT TRADE. Fix the systemd env.")
        _tg("🔴🔴 <b>GREENER: COINALYZE_KEY missing</b> — short side (fade gate + E5) will NOT trade until the key is set!")
    reconcile_orders(st)        # cancel orphan/duplicate exchange orders left by a mid-arm restart (prevents phantom-slot bug)
    watch = []; last_watch = 0
    while True:
        try:
            st = load_state()
            if LIVE:
                bal = live_balance()
                if bal: st["equity"] = bal
            d = time.strftime("%Y-%m-%d", time.gmtime())
            if st.get("day") != d: st["day"] = d; st["day_eq"] = st.get("equity", 0.0) or 0.0   # new UTC day -> reset the daily-loss anchor
            manage_long(st); manage_short(st); manage_momentum(st); manage_snapback(st); manage_trend(st); manage_squeeze(st)
            if now()-last_watch > REFRESH_SEC:
                watch = build_watchlist()
                open_long(st, watch); open_short(st, watch)
                last_watch = now()
            open_momentum(st)   # self-guards: heavy movers scan runs once per new 15m bar; cheap no-op otherwise
            open_snapback(st)   # A: liq-snapback longs (self-guards: leg off / no slot / BTC distribution)
            open_trend(st)      # D: daily Donchian trend longs (self-guards: leg off / no slot / once-per-day / risk-off)
            open_squeeze(st, watch)   # GREENER E5: squeeze-fade shorts (self-guards: off / no slot / 1m-bar throttle / short-side pauses)
            regime = "risk-ON" if btc_risk_on() else "risk-OFF"
            open(STATUS_FILE, "w").write(
                f"{ts()} | {'LIVE' if LIVE else 'PAPER'} | BTC(info)={regime} | Lqual={st.get('lbreadth','?')} Sfires={st.get('sfires','?')} | "
                f"L={[(p['coin'],p['state']) for p in st['long']]} S={[(p['coin'],p['state']) for p in st['short']]} "
                f"SQ={[(p['coin'],p['state']) for p in st.get('squeeze',[])]} "
                f"M={[(p['coin'],p['setup'],p['state']) for p in st.get('mom',[])]} "
                f"eq=${st['equity']:.2f} {st['wins']}W/{st['losses']}L realized=${st['realized']:+.2f}")
            save_state(st)
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__": main()
