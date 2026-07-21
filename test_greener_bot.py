#!/usr/bin/env python3
"""Unit tests for combo_bot — the NEW logic: dynamic slot allocation, trailing on both legs, book() signs.
Run: python3 test_combo_bot.py  (all must pass before deploy)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import gladiator_bot from THIS dir -> standalone-safe (no hardcoded repo path)
import greener_bot as c

P = F = 0
def ok(cond, name):
    global P, F
    if cond: P += 1; print(f"  ✅ {name}")
    else: F += 1; print(f"  ❌ {name}")

def stt(long=0, short=0, mom=0):
    return {"long": [{"coin": f"L{i}"} for i in range(long)], "short": [{"coin": f"S{i}"} for i in range(short)],
            "mom": [{"coin": f"M{i}"} for i in range(mom)], "slot_turn": "short"}

print("=== ULTRA slot allocation (5 pool; caps dip≤2/fade≤2/mom≤2; 🔒 TOTAL-LONG=3 ruin guard) ===")
ok(c.M_DIP_CAP == 2 and c.S_FADE_CAP == 2 and c.M_MOM_CAP == 2 and c.M_TOTAL_LONG_CAP == 3, "caps confirmed: dip2/fade2/mom2, total-long 3")
ok(c.can_open(stt(0, 0, 0), "long") and c.can_open(stt(0, 0, 0), "short") and c.can_open(stt(0, 0, 0), "mom"), "empty: all three strats can open")
ok(not c.can_open(stt(2, 0, 0), "long"), "dip capped at 2")
ok(not c.can_open(stt(0, 2, 0), "short"), "fade capped at 2")
ok(not c.can_open(stt(0, 0, 2), "mom"), "mom capped at 2")
# 🔒 TOTAL-LONG = 3 (dip+mom): the load-bearing ruin guard — no 4th correlated long can stack
ok(not c.can_open(stt(1, 0, 2), "long"), "dip1+mom2 (total-long 3): a DIP long is BLOCKED (🔒 guard, even though dip<2)")
ok(not c.can_open(stt(1, 0, 2), "mom"), "dip1+mom2 (total-long 3): a MOM long is BLOCKED (🔒 guard)")
ok(c.can_open(stt(1, 0, 2), "short"), "dip1+mom2 (total-long 3): a FADE (short) can STILL take the 5th slot (not a long)")
ok(c.can_open(stt(2, 0, 0), "mom") and not c.can_open(stt(2, 0, 0), "long"), "dip2 (total-long 2): dip at cap, but MOM can add the 3rd long")
# a free slot goes to whoever FIRES (emergent regime-awareness)
ok(c.can_open(stt(2, 2, 0), "mom"), "dip2+fade2 (4 open, total-long 2): the 5th slot is free for MOMENTUM")
ok(not c.can_open(stt(2, 2, 1), "mom") and not c.can_open(stt(2, 2, 1), "short") and not c.can_open(stt(2, 2, 1), "long"), "pool full (2/2/1=5): nothing opens")
ok(c.n_open(stt(2, 1, 2)) == 5 and c.n_long_total(stt(2, 1, 2)) == 4, "n_open counts all 3 legs; n_long_total = dip+mom only")

print("\n=== LONG trailing (lock green): rise +1% -> trail -> reverse exits in profit ===")
c.LIVE = False
c.specs = lambda sym: {"step": 1.0, "minq": 1.0, "minn": 5.0, "tick": 0.0001, "qp": 0, "pp": 4}
_p = {"v": 0.0}; c.price_now = lambda s: _p["v"]
st = {"equity": 35.0, "long": [], "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0, "last_sig": {}}
# OPEN long entry 100, base SL 99 (-1%), TP 103
lp = {"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0, "tp_oid": None, "sl_oid": None, "ts": c.now(), "maxhigh": 100.0}
st["long"].append(dict(lp))
_p["v"] = 100.5; c.manage_long(st)   # +0.5% -> no trail yet (act +1%)
ok(st["long"] and st["long"][0]["state"] == "OPEN", "long stays open at +0.5% (below trail activation)")
_p["v"] = 101.5; c.manage_long(st)   # +1.5% green -> maxhigh updates, trail active
_p["v"] = 100.9; c.manage_long(st)   # reversal: B1 trail stop = 101.5*(1-0.3%)=101.195 -> 100.9 <= 101.195 -> TRAIL exit in profit
ok(not st["long"] and st["wins"] == 1, f"long trails + exits in PROFIT on reversal (wins={st['wins']}, eq={st['equity']:.2f})")
ok(st["equity"] > 35.0, "long trailing exit credited a profit (green locked)")
ok(c.L_TRAIL == 0.005 and c.S_TRAIL == 0.005, "GOLDEN sync: LONG trail width 0.5%, SHORT 0.5%")

print("\n=== SHORT trailing: fall +1% -> trail -> reverse exits in profit ===")
st2 = {"equity": 35.0, "long": [], "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0, "last_sig": {}}
sp = {"coin": "Y", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 102.0, "tp": 94.0, "tp_oid": None, "sl_oid": None, "ts": c.now(), "minlow": 100.0}
st2["short"].append(dict(sp))
_p["v"] = 99.5; c.manage_short(st2)   # +0.5% green -> no trail yet
ok(st2["short"] and st2["short"][0]["state"] == "OPEN", "short stays open at +0.5% green")
_p["v"] = 98.5; c.manage_short(st2)   # +1.5% green -> minlow updates, trail active
_p["v"] = 99.1; c.manage_short(st2)   # reversal: trail stop = 98.5*(1+0.5%)=98.99 -> 99.1 >= 98.99 -> TRAIL exit in profit
ok(not st2["short"] and st2["wins"] == 1, f"short trails + exits in PROFIT on reversal (wins={st2['wins']}, eq={st2['equity']:.2f})")
ok(st2["equity"] > 35.0, "short trailing exit credited a profit (green locked)")

print("\n=== book() PnL signs ===")
stb = {"equity": 35.0, "long": [], "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0}
c.book(stb, "long", {"coin": "A", "entry": 100.0, "qty": 1.0}, 103.0, "TP")   # long win
ok(stb["wins"] == 1 and stb["equity"] > 35.0, "long TP win credits equity")
c.book(stb, "short", {"coin": "B", "entry": 100.0, "qty": 1.0}, 95.0, "TP")   # short win (price fell)
ok(stb["wins"] == 2 and stb["equity"] > 35.0, "short TP win (exit<entry) credits equity")
eqp = stb["equity"]
c.book(stb, "long", {"coin": "C", "entry": 100.0, "qty": 1.0}, 99.0, "SL")    # long loss
ok(stb["losses"] == 1 and stb["equity"] < eqp, "long SL loss debits equity")

print("\n=== LAST-HOUR gate (last1): reject arming into an active flush (29-06 research) ===")
def klines_uptrend(last_ret_pct):
    """170 bars, +0.6%/bar uptrend (passes anti-chop/slope/fresh/today), with the LAST CLOSED bar's return controllable.
    Timestamps start 10:00 so the most-recent 00:00-UTC bar is ~10 bars back (today-gate has real accumulated gain)."""
    n = 170
    closes = [100.0*(1.006**i) for i in range(n)]
    closes[168] = closes[167]*(1+last_ret_pct/100)            # index 168 = the just-closed bar after trend_ref drops k[-1]
    rows = []
    for i in range(n):
        ot = (10 + i)*3600000
        op = closes[i-1] if i > 0 else closes[i]/1.006
        cl = closes[i]
        rows.append([ot, op, max(op,cl)*1.001, min(op,cl)*0.999, cl, 1000.0, ot+3599999, 100000.0])
    return rows
_orig_fk = c.ss.fetch_klines
c.ss.fetch_klines = lambda sym, tf, limit=0: klines_uptrend(c._test_last_ret)
c._test_last_ret = 0.0
up0, ref0, _, _ = c.trend_ref("X");  ok(up0, "flat last hour (0%): qualifies (up=True)")
c._test_last_ret = -1.5
up1, _, _, _ = c.trend_ref("X");     ok(not up1, "last hour -1.5% (< -1.0%): REJECTED by last1 gate (up=False)")
_save = c.L_LAST1_MIN; c.L_LAST1_MIN = -99.0
up2, _, _, _ = c.trend_ref("X");     ok(up2, "same -1.5% bar but gate disabled: qualifies again (proves last1 is the cause)")
c.L_LAST1_MIN = _save; c.ss.fetch_klines = _orig_fk

print("\n=== reconcile_orders: cancel orphan/duplicate exchange orders, keep tracked (phantom-slot fix) ===")
_save_live = c.LIVE; c.LIVE = True
st_r = {"long": [{"coin": "AA", "state": "PENDING", "oid": 111, "tp_oid": None, "sl_oid": None},
                 {"coin": "BB", "state": "OPEN", "oid": 555, "tp_oid": 222, "sl_oid": 999}],
        "short": []}
fake_oo = [{"orderId": 111, "symbol": "AAUSDT", "side": "BUY",  "type": "LIMIT", "price": "1.0"},   # tracked entry
           {"orderId": 222, "symbol": "BBUSDT", "side": "SELL", "type": "LIMIT", "price": "2.0"},   # tracked TP
           {"orderId": 333, "symbol": "CCUSDT", "side": "BUY",  "type": "LIMIT", "price": "3.0"},   # ORPHAN
           {"orderId": 444, "symbol": "AAUSDT", "side": "BUY",  "type": "LIMIT", "price": "1.0"}]   # DUPLICATE of AA
_os, _oc, _op = c._signed, c.cancel, c.live_positions
cancelled_ids = []
c._signed = lambda path, params, method="GET": fake_oo if "openOrders" in path else []
c.cancel = lambda sym, oid: (cancelled_ids.append(oid) or {"status": "CANCELED"})
c.live_positions = lambda: {}
_n = c.reconcile_orders(st_r)
ok(_n == 2, f"cancelled exactly 2 orphan/duplicate orders (got {_n})")
ok(set(cancelled_ids) == {333, 444}, f"cancelled the orphans 333+444, not the tracked (got {sorted(cancelled_ids)})")
ok(111 not in cancelled_ids and 222 not in cancelled_ids, "tracked entry(111) + TP(222) preserved")
c.LIVE = _save_live; c._signed, c.cancel, c.live_positions = _os, _oc, _op

print("\n=== open_long REFRESH-DROP: cancel a PENDING dip-limit that no longer qualifies ===")
_save_live2 = c.LIVE; _otr = c.trend_ref
c.trend_ref = lambda sym: (False, None, 0.0, 0.0) if sym == "STALEUSDT" else (True, 1.01, 5.0, 0.0)  # ref=1.01 ~ just above the 1.0 limit (realistic, NOT run away — isolates refresh-drop from runaway-drop)
c.LIVE = False
st_d = {"equity": 30.0, "slot_turn": "short", "paused_until": 0, "loss_streak": 0, "short": [],
        "long": [{"coin": "STALEUSDT", "state": "PENDING", "oid": "o1", "limit": 1.0, "qty": 1.0},
                 {"coin": "GOODUSDT",  "state": "PENDING", "oid": "o2", "limit": 1.0, "qty": 1.0}]}
c.open_long(st_d, [])                       # empty watch -> only the refresh-drop runs, no new arms
left = [p["coin"] for p in st_d["long"]]
ok("STALEUSDT" not in left, "disqualified pending dropped")
ok("GOODUSDT" in left, "still-qualifying pending kept")
c.LIVE = True                                # live path must call cancel() on the dropped order
_oc2 = c.cancel; cancelled2 = []
c.cancel = lambda sym, oid: (cancelled2.append((sym, oid)) or {"status": "CANCELED"})
st_d2 = {"equity": 30.0, "slot_turn": "short", "paused_until": 0, "loss_streak": 0, "short": [],
         "long": [{"coin": "STALEUSDT", "state": "PENDING", "oid": "oX", "limit": 1.0, "qty": 1.0}]}
c.open_long(st_d2, [])
ok(("STALEUSDT", "oX") in cancelled2, "LIVE: cancel() called for the dropped pending order")
c.cancel = _oc2; c.trend_ref = _otr; c.LIVE = _save_live2

print("\n=== open_long RUNAWAY-DROP: cancel a PENDING dip-limit whose coin ran away UP (02-07) ===")
_save_live4 = c.LIVE; _otr4 = c.trend_ref
# RUN coin: qualifies (up=True) but ref ran to 1.10 = +10% above its 1.0 limit (> L_RUNAWAY_DROP 5%) -> drop
# NEAR coin: qualifies, ref 1.03 = +3% above limit (< 5%) -> kept (a -1.2% dip is still reachable)
c.trend_ref = lambda sym: (True, 1.10, 5.0, 0.0) if sym == "RUNUSDT" else (True, 1.03, 5.0, 0.0)
c.LIVE = False
st_r = {"equity": 30.0, "slot_turn": "short", "paused_until": 0, "loss_streak": 0, "short": [],
        "long": [{"coin": "RUNUSDT",  "state": "PENDING", "oid": "r1", "limit": 1.0, "qty": 1.0},
                 {"coin": "NEARUSDT", "state": "PENDING", "oid": "r2", "limit": 1.0, "qty": 1.0}]}
c.open_long(st_r, [])
leftr = [p["coin"] for p in st_r["long"]]
ok("RUNUSDT" not in leftr, "ran-away pending (+10% > 5%) dropped by runaway-drop")
ok("NEARUSDT" in leftr, "near pending (+3% < 5%) kept (dip still reachable)")
_srd = c.L_RUNAWAY_DROP; c.L_RUNAWAY_DROP = 0                # disable proves causation
st_r2 = {"equity": 30.0, "slot_turn": "short", "paused_until": 0, "loss_streak": 0, "short": [],
         "long": [{"coin": "RUNUSDT", "state": "PENDING", "oid": "r3", "limit": 1.0, "qty": 1.0}]}
c.open_long(st_r2, [])
ok("RUNUSDT" in [p["coin"] for p in st_r2["long"]], "runaway-drop disabled (0): ran-away pending kept (proves runaway is the cause)")
c.L_RUNAWAY_DROP = _srd; c.trend_ref = _otr4; c.LIVE = _save_live4

print("\n=== open_short REFRESH-DROP: cancel a PENDING retest whose coin un-weakened (c7d > cap) ===")
_save_live3 = c.LIVE; _oc7 = c.coin7d
c.coin7d = lambda sym, tf="1h", bpd=24: 3.0 if sym == "UNWEAKUSDT" else -20.0   # UNWEAK(+3) > 0 cap (07-07); WEAK(-20) < cap
c.LIVE = False
st_s = {"equity": 30.0, "slot_turn": "long", "long": [],
        "short": [{"coin": "UNWEAKUSDT", "state": "PENDING", "oid": "s1", "tf": "1h"},
                  {"coin": "WEAKUSDT",   "state": "PENDING", "oid": "s2", "tf": "1h"}]}
c.open_short(st_s, [])                       # empty watch -> only the refresh-drop runs
sleft = [p["coin"] for p in st_s["short"]]
ok("UNWEAKUSDT" not in sleft, "un-weakened short (c7d +3% > 0 cap) dropped")
ok("WEAKUSDT" in sleft, "still-weak short (c7d -20%) kept")
c.LIVE = True
_oc8 = c.cancel; cancS = []
c.cancel = lambda sym, oid: (cancS.append((sym, oid)) or {"status": "CANCELED"})
st_s2 = {"equity": 30.0, "slot_turn": "long", "long": [],
         "short": [{"coin": "UNWEAKUSDT", "state": "PENDING", "oid": "sX", "tf": "1h"}]}
c.open_short(st_s2, [])
ok(("UNWEAKUSDT", "sX") in cancS, "LIVE: cancel() called for the dropped short")
c.cancel = _oc8; c.coin7d = _oc7; c.LIVE = _save_live3

print("\n=== short_notional HYBRID sizing: cap per-trade $-risk at ~S_RISK_FRAC, no balloon ===")
EQ = 35.0
eq_slot = (EQ/c.TOTAL_SLOTS)*c.LEV
n_wide = c.short_notional(EQ, 0.078)     # CLO-like +7.8% structural stop
n_tight = c.short_notional(EQ, 0.011)    # APT-like +1.1% stop
n_vtight = c.short_notional(EQ, 0.004)   # very tight (would balloon under pure risk-sizing)
ok(n_wide < eq_slot, f"wide stop (7.8%) risk-capped below equal-slot ({n_wide:.2f} < {eq_slot:.2f})")
ok(abs(0.078*n_wide - EQ*c.S_RISK_FRAC) < 1e-6, f"wide stop $-loss == equity*S_RISK_FRAC (${0.078*n_wide:.3f} = ${EQ*c.S_RISK_FRAC:.3f})")
ok(abs(n_tight - eq_slot) < 1e-9, f"tight stop (1.1%) uses full equal-slot ({n_tight:.2f})")
ok(abs(n_vtight - eq_slot) < 1e-9, f"very tight stop capped at equal-slot, NO balloon ({n_vtight:.2f})")
# the invariant: a stop-out never loses more than ~S_RISK_FRAC of account, across all stop distances
worst = max(rsk*c.short_notional(EQ, rsk)/EQ*100 for rsk in (0.004,0.011,0.03,0.05,0.078,0.10))
ok(worst <= c.S_RISK_FRAC*100 + 1e-6, f"worst single-trade loss bounded at {worst:.2f}% (<= {c.S_RISK_FRAC*100:.1f}%)")

print("\n=== MAKER-SL (04-07): 3-order OCO on fill, clean/gap stop booking, soft threshold, trail cancels 3 ===")
_sv = dict(LIVE=c.LIVE, order_status=c.order_status, algo_open_ids=c.algo_open_ids, cancel=c.cancel,
           cancel_algo=c.cancel_algo, mkt_sell=c.mkt_sell, price_now=c.price_now, limit_sell_tp=c.limit_sell_tp,
           stop_limit_sell=c.stop_limit_sell, stop_market_sell=c.stop_market_sell, specs=c.specs,
           swing_low=c.swing_low, live_positions=c.live_positions, _signed=c._signed, _msl=c.L_MAKER_SL)
c.LIVE = True; c.L_MAKER_SL = True
c.specs = lambda sym: {"step": 1.0, "minq": 1.0, "minn": 5.0, "tick": 0.0001, "qp": 0, "pp": 4}
c.swing_low = lambda sym, n: None
calls = {"tp": [], "sl_lim": [], "sl_mkt": [], "canc": [], "canc_algo": [], "mkt": [], "sweep": []}
c.limit_sell_tp   = lambda s,q,p: (calls["tp"].append((s,q,p)) or {"orderId": "TP1"})
c.stop_limit_sell = lambda s,q,t,l: (calls["sl_lim"].append((s,q,t,l)) or {"algoId": "SLIM1"})
c.stop_market_sell= lambda s,t: (calls["sl_mkt"].append((s,t)) or {"algoId": "BSTP1"})
c.order_status    = lambda s,o: {"status": "FILLED", "executedQty": "1", "avgPrice": "100.0"}
c.live_positions  = lambda: {("X","LONG"): 1.0}
c.price_now       = lambda s: 100.0
# 1) fill places 3 orders (TP + stop-LIMIT primary + backstop), stop-limit trig==limit==SL
stf = {"equity": 35.0, "long": [{"coin": "X", "state": "PENDING", "oid": "o", "limit": 100.0, "qty": 1.0,
        "entry": None, "tp_oid": None, "sl_oid": None, "sl": None, "ts": c.now(), "maxhigh": None}],
       "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0}
c.manage_long(stf)
p = stf["long"][0]
ok(p["state"]=="OPEN" and p["tp_oid"]=="TP1" and p["sl_oid"]=="SLIM1" and p.get("bs_oid")=="BSTP1", "fill places TP + stop-LIMIT + backstop (3 orders)")
ok(calls["sl_lim"] and abs(calls["sl_lim"][0][2]-99.0)<1e-6 and calls["sl_lim"][0][3] > calls["sl_lim"][0][2] + 1e-9, "stop-LIMIT trigger=SL(-1%), limit PASSIVE (a hair ABOVE trigger -> maker fill on bounce)")
ok(calls["sl_mkt"] and abs(calls["sl_mkt"][0][1]-99.0*(1-c.L_MAKER_SL_GAP/100))<1e-4, "backstop at SL-0.3%")
# 2) soft threshold pre-trail: px between backstop and SL -> NO market exit (maker window); below backstop -> exits
c.cancel      = lambda s,o: calls["canc"].append((s,o)) or {"status":"CANCELED"}
c.cancel_algo = lambda s,a: calls["canc_algo"].append((s,a)) or {}
c.mkt_sell    = lambda s,q: calls["mkt"].append((s,q)) or {}
c.price_now   = lambda s: 98.9        # below SL 99, above backstop 98.703
c.manage_long(stf)
ok(stf["long"] and not calls["mkt"], "pre-trail px in [backstop, SL) -> software HOLDS (gives maker stop its window)")
c.price_now   = lambda s: 98.60       # below backstop -> software fires, cancels all 3
c.manage_long(stf)
ok(not stf["long"] and calls["mkt"] and len(calls["canc_algo"])==2 and ("X","TP1") in calls["canc"], "below backstop -> exits + cancels TP + BOTH algos")
# 3) clean maker-stop booking: position gone, stop-LIMIT algo missing (fired), backstop still open -> book at SL px, cancel bs+tp
for k in calls: calls[k].clear()
st2 = {"equity": 35.0, "long": [{"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0,
        "tp_oid": "TPX", "sl_oid": "SLX", "bs_oid": "BSX", "ts": c.now(), "maxhigh": 100.0}],
       "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0}
c.live_positions = lambda: {}
c.order_status   = lambda s,o: {"status": "NEW"}
c.algo_open_ids  = lambda s: {"BSX"}                 # SLX gone = stop-limit fired clean
c.price_now      = lambda s: 98.2                    # market already lower — booking must still use SL px (the floor)
eq0 = st2["equity"]; c.manage_long(st2)
ok(not st2["long"] and ("X","BSX") in calls["canc_algo"] and ("X","TPX") in calls["canc"], "clean maker stop: cancels backstop + TP")
ok(abs(eq0 - st2["equity"]) < 1.3, f"clean maker stop BOOKED AT SL price -1% (drop ${eq0-st2['equity']:.2f} ~= $1.07, NOT $1.87 of the market px)")
# 4) gap path: BOTH algos gone -> backstop closed; orphan child limit swept
for k in calls: calls[k].clear()
st3 = {"equity": 35.0, "long": [{"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0,
        "tp_oid": "TPX", "sl_oid": "SLX", "bs_oid": "BSX", "ts": c.now(), "maxhigh": 100.0}],
       "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0}
c.algo_open_ids = lambda s: set()
c._signed = lambda path, prm, m="POST": ([{"orderId": "CHILD1"}] if "openOrders" in path else {})
c.manage_long(st3)
ok(not st3["long"] and ("X","CHILD1") in calls["canc"], "gap path (both algos fired): orphan child limit SWEPT")
# 5) BOOKING-FIX (07-07): position gone via a wick that fired the backstop; the REAL fill (-1.18%) must be booked,
#    NOT the bounced poll/mark px (the EDGE bug: bot booked -0.34% while Binance actually filled -1.18%).
for k in calls: calls[k].clear()
st5 = {"equity": 35.0, "long": [{"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0,
        "tp_oid": "TPX", "sl_oid": "SLX", "bs_oid": "BSX", "ts": c.now(), "ft": c.now(), "maxhigh": 100.0}],
       "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0}
c.live_positions = lambda: {}                      # position gone
c.order_status   = lambda s,o: {"status": "NEW"}   # TP not filled -> won=False
c.algo_open_ids  = lambda s: set()                 # both algos gone -> bs_fired -> OLD code booked the bounced mark
c.price_now      = lambda s: 100.40                # a BOUNCED mark ABOVE entry (the EDGE mislog source)
c._signed = lambda path, prm, m="GET": (
    [{"side": "SELL", "realizedPnl": "-0.40", "price": "98.82", "qty": "1.0", "commission": "0.01"}] if "userTrades" in path
    else ([{"orderId": "CH"}] if "openOrders" in path else {}))
c.manage_long(st5)   # LIVE book() updates st["realized"] (equity comes from the real account, not the internal counter)
ok(not st5["long"] and st5["realized"] < -1.0 and st5["losses"] == 1,
   f"BOOKING-FIX: booked the REAL -1.18% fill (realized ${st5['realized']:.2f}), NOT the +0.4% bounced mark (old bug = a fake +gain)")
import os as _os5
_telf = c.EXIT_TEL                                  # 09-07: relocated to reset-safe TEL_DIR (was DAEMON_DIR/logs)
_teltxt = open(_telf).read() if _os5.path.exists(_telf) else ""
ok(_os5.path.exists(_telf) and "98.82" in _teltxt, "exit-telemetry CSV records the real fill + drift")
_telhdr = _teltxt.splitlines()[0] if _teltxt else ""
_tellast = _teltxt.strip().splitlines()[-1] if _teltxt else ""
ok("r_multiple" in _telhdr and "init_risk_pct" in _telhdr, "exit-telemetry header has R-multiple + init-risk columns")
# entry 100 / sl 99 -> init_risk 1.00% ; real fill 98.82 -> real_ret -1.18% -> R = -1.18
ok(_tellast.endswith("1.0000,-1.1800") or ",1.0000,-1.18" in _tellast, "exit-telemetry R-multiple computed (-1.18R at 1% risk)")
for k,v in _sv.items():
    setattr(c, k if k!="_msl" else "L_MAKER_SL", v)

print("\n=== BOOKING-FIX retry (userTrades index lag on a FAST close — the LDO case) ===")
_svr = dict(LIVE=c.LIVE, _signed=c._signed, gap=c.RCF_GAP)
c.LIVE = True; c.RCF_GAP = 0.0     # no real sleep in tests
_rc = {"n": 0}
def _lag_signed(path, prm, m="GET"):
    _rc["n"] += 1
    if "userTrades" in path:       # 1st query empty (Binance hasn't indexed the fill), 2nd returns it
        return [] if _rc["n"] == 1 else [{"side": "SELL", "realizedPnl": "-0.4", "price": "98.82", "qty": "1.0", "commission": "0.01"}]
    return {}
c._signed = _lag_signed
_px = c.real_close_fill("X", "SELL", c.now()-1000)
ok(_px is not None and abs(_px-98.82) < 1e-6 and _rc["n"] == 2, f"retry catches the lagged fill on the 2nd query (px={_px}, calls={_rc['n']})")
_rc["n"] = 0
c._signed = lambda path, prm, m="GET": [] if "userTrades" in path else {}
ok(c.real_close_fill("X", "SELL", c.now()-1000) is None and _rc["n"] == 0, "gives up cleanly (None -> fallback) if the fill never indexes")
for k, v in _svr.items():
    setattr(c, k if k != "gap" else "RCF_GAP", v)

print("\n=== MAKER-TRAIL-EXIT A/B (07-07): rest a maker exit on a TRAIL, taker fallback, never on SL ===")
_svm = dict(LIVE=c.LIVE, mt=c.L_MAKER_TRAIL, specs=c.specs, limit_sell_tp=c.limit_sell_tp, cancel=c.cancel,
            cancel_algo=c.cancel_algo, mkt_sell=c.mkt_sell, price_now=c.price_now, live_positions=c.live_positions,
            _signed=c._signed, wait=c.L_MT_WAIT)
c.LIVE = True; c.L_MAKER_TRAIL = True; c.L_MT_WAIT = 3.0
c.specs = lambda s: {"step": 0.001, "minq": 0.001, "minn": 5.0, "tick": 0.0001, "qp": 3, "pp": 4}
mc = {"tp": [], "canc": [], "cancA": [], "mkt": []}
c.limit_sell_tp = lambda s, q, p: (mc["tp"].append((s, q, p)) or {"orderId": "MT1"})
c.cancel = lambda s, o: mc["canc"].append((s, o)) or {}
c.cancel_algo = lambda s, a: mc["cancA"].append((s, a)) or {}
c.mkt_sell = lambda s, q: mc["mkt"].append((s, q)) or {}
# a green OPEN long already in profit with the trail active (maxhigh +1.2%), even ft => maker A/B arm
def mkpos(ft):
    return {"equity": 35.0, "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0,
            "long": [{"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0, "maxhigh": 101.2,
                      "tp_oid": "TP", "sl_oid": "SL", "bs_oid": "BS", "ts": c.now(), "ft": ft}]}
# 1) trail hit on the maker arm -> rests a maker sell, sets mt_pending, KEEPS bs_oid, does NOT market-sell
st = mkpos(1000.0)                       # even parity
c.price_now = lambda s: 100.6            # <= trail stop (101.2*0.995=100.694) -> trail triggers
c.live_positions = lambda: {("X", "LONG"): 1.0}
c.manage_long(st)
p = st["long"][0]
ok(p.get("mt_pending") and mc["tp"] and not mc["mkt"] and p.get("bs_oid") == "BS", "maker arm: rests maker exit, NO market-sell, backstop KEPT")
# 2) next tick, position gone (maker filled) -> books TRAIL, cancels backstop
c._signed = lambda path, prm, m="GET": ([{"side": "SELL", "realizedPnl": "+0.6", "price": "100.66", "qty": "1.0", "commission": "0.01"}] if "userTrades" in path else {})
c.live_positions = lambda: {}
r0 = st["realized"]; c.manage_long(st)
ok(not st["long"] and st["realized"] > r0 and ("X", "BS") in mc["cancA"], "maker FILLED -> books TRAIL win + cancels backstop")
# 3) maker did NOT fill by deadline -> cancel maker + backstop, taker fallback
mc["mkt"].clear(); mc["canc"].clear(); mc["cancA"].clear()
st2 = mkpos(1000.0); c.price_now = lambda s: 100.6; c.live_positions = lambda: {("X", "LONG"): 1.0}
c.manage_long(st2)                        # arms the maker
st2["long"][0]["mt_deadline"] = c.now() - 1   # force deadline passed
c.manage_long(st2)
ok(not st2["long"] and mc["mkt"] and ("X", "MT1") in mc["canc"], "maker MISSED -> cancels maker + taker fallback closes")
# 4) SL exit (below entry) NEVER uses the maker arm even with the flag on
mc["tp"].clear(); mc["mkt"].clear()
st3 = {"equity": 35.0, "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0,
       "long": [{"coin": "X", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0, "maxhigh": 100.0,
                 "tp_oid": "TP", "sl_oid": "SL", "bs_oid": None, "ts": c.now(), "ft": 1000.0}]}
c.price_now = lambda s: 98.9; c.live_positions = lambda: {("X", "LONG"): 1.0}
c.manage_long(st3)
ok(not st3["long"] and not mc["tp"] and mc["mkt"], "SL exit stays TAKER (never rests a maker on a loss)")
# 5) odd-parity ft = taker baseline arm (no maker)
mc["tp"].clear(); mc["mkt"].clear()
st4 = mkpos(1001.0)                        # odd parity
c.price_now = lambda s: 100.6; c.live_positions = lambda: {("X", "LONG"): 1.0}
c.manage_long(st4)
ok(not st4["long"] and not mc["tp"] and mc["mkt"], "odd-parity ft -> taker baseline arm (A/B 50/50)")
for k, v in _svm.items():
    setattr(c, k if k not in ("mt", "wait") else ("L_MAKER_TRAIL" if k == "mt" else "L_MT_WAIT"), v)

print("\n=== LOWER-WICK VETO (07-07, watched entry filter) ===")
_svw = dict(fk=c.ss.fetch_klines, wv=c.L_WICK_VETO)
c.L_WICK_VETO = 0.15
c.ss.fetch_klines = lambda s, tf, limit=0: [[0, "100", "100.2", "99", "99", "1"]]*6      # close on low -> lower-wick 0
ok(c.wick_ok("X") is False, "sellers-only knife (lower-wick 0 < 0.15) -> VETO")
c.ss.fetch_klines = lambda s, tf, limit=0: [[0, "100", "100.2", "99", "100.1", "1"]]*6   # long lower wick 0.83
ok(c.wick_ok("X") is True, "buyer-defense wick (0.83 >= 0.15) -> allow arm")
c.ss.fetch_klines = lambda s, tf, limit=0: []                                            # data glitch
ok(c.wick_ok("X") is True, "fetch glitch -> allow (never veto on a data error)")
c.L_WICK_VETO = 0.0
ok(c.wick_ok("X") is True, "L_WICK_VETO=0 -> filter OFF")
c.ss.fetch_klines = _svw["fk"]; c.L_WICK_VETO = _svw["wv"]

print("\n=== G-VETO (fix #8): cancel slow-grind approach, pass flushes, cancel-race safe ===")
_sv8 = dict(LIVE=c.LIVE, price_now=c.price_now, order_status=c.order_status, cancel=c.cancel,
            live_positions=c.live_positions, _gv=c.L_GRIND_VETO)
c.LIVE = False; c.L_GRIND_VETO = True
def mkpend(pxh):
    return {"equity": 35.0, "short": [], "realized": 0.0, "wins": 0, "losses": 0, "loss_streak": 0, "paused_until": 0,
            "long": [{"coin": "X", "state": "PENDING", "oid": "o1", "limit": 100.0, "qty": 1.0,
                      "entry": None, "tp_oid": None, "sl_oid": None, "sl": None, "ts": c.now(), "maxhigh": None,
                      "pxh": pxh}]}
# 1) GRIND: window-high only +0.6% above limit, price in band -> veto cancels
c.price_now = lambda s: 100.25
stg = mkpend([[c.now()-120, 100.6], [c.now()-70, 100.5]])
c.manage_long(stg)
ok(not stg["long"], "grind approach (win-high +0.6% < 1.2%) -> pending VETOED")
# 2) FLUSH: window-high +1.5% above limit -> NO veto (limit stays to catch the flush)
stf = mkpend([[c.now()-120, 101.5], [c.now()-70, 101.2]])
c.manage_long(stf)
ok(stf["long"] and stf["long"][0]["state"] == "PENDING", "flush approach (win-high +1.5%) -> pending KEPT")
# 3) short history (<60s) -> no veto yet
sth = mkpend([[c.now()-20, 100.5]])
c.manage_long(sth)
ok(sth["long"], "insufficient history (<60s) -> no veto")
# 4) flag off -> no veto
c.L_GRIND_VETO = False
sto = mkpend([[c.now()-120, 100.6], [c.now()-70, 100.5]])
c.manage_long(sto)
ok(sto["long"], "L_GRIND_VETO=False -> grind approach kept (flag works)")
c.L_GRIND_VETO = True
# 5) LIVE cancel-race: cancel errors (order may have filled) -> pos KEPT for next-tick reconcile
c.LIVE = True
c.live_positions = lambda: {}
c.order_status = lambda s, o: {"status": "NEW"}
c.price_now = lambda s: 100.25
c.cancel = lambda s, o: {"_error": True, "_body": "already filled"}
str_ = mkpend([[c.now()-120, 100.6], [c.now()-70, 100.5]])
c.manage_long(str_)
ok(str_["long"], "LIVE cancel-race: cancel error -> pending KEPT (fill wins, reconciled next tick)")
# 6) LIVE clean cancel -> removed
c.cancel = lambda s, o: {"status": "CANCELED"}
stc = mkpend([[c.now()-120, 100.6], [c.now()-70, 100.5]])
c.manage_long(stc)
ok(not stc["long"], "LIVE clean cancel -> pending removed, slot freed")
for k, v in _sv8.items():
    setattr(c, k if k != "_gv" else "L_GRIND_VETO", v)

print("\n=== DISPERSION Z-GATE (z_gate_filter: arm only coins clearly above the pack) ===")
mk = lambda m: (m, f"C{m}", 1.0, 0.0)                       # (mom, sym, ref, illiq) — filter keys on mom (index 0)
c3 = [mk(10), mk(20), mk(30)]
ok([x[0] for x in c.z_gate_filter(c3, 0.5)] == [30], "z0.5 on [10,20,30] keeps only the leader (30)")
ok(c.z_gate_filter(c3, 0.0) == c3, "z=0 -> gate OFF (unchanged)")
ok(c.z_gate_filter([mk(10), mk(30)], 0.5) == [mk(10), mk(30)], "<3 candidates -> inert (can't judge dispersion)")
ok(c.z_gate_filter([mk(50), mk(50), mk(50)], 0.5) == [mk(50)]*3, "zero dispersion -> unchanged (sd=0 guard)")
ok([x[0] for x in c.z_gate_filter([mk(5), mk(6), mk(7), mk(40)], 0.5)] == [40], "outlier leader kept, tight low-cluster dropped")
ok(c.z_gate_filter([mk(1), mk(2), mk(3)], 3.0) == [], "z=3 too strict -> empty (arm nothing this cycle, wait for a clear leader)")

print("\n=== SHORT volume+wick filter on the busted-breakout bar (07-07) ===")
_sfk = c.ss.fetch_klines
def _mkbars(bvol, bopen, bclose):
    bars = [[t*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0] for t in range(194)]  # flat 100, N20-high=100
    bars[192] = [192*3600000, bopen, 102.0, 100.0, bclose, bvol]                  # breakout bar (last-1): high 102 > lvl 100
    bars[193] = [193*3600000, 100.0, 100.5, 99.0, 99.5, 1000.0]                   # reclaim (last): close 99.5 < lvl -> fire
    bars.append([194*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0])               # dropped by k[:-1]
    return bars
c.ss.fetch_klines = lambda sym, tf, limit=0: _mkbars(*{"good":(2000,100.0,100.3),"lowvol":(1200,100.0,100.3),
                                                        "lowwick":(2000,100.0,101.8)}[sym])
# self-contained: the mock coin is flat-7d, so force the fix#11 config (cap=0 + filter ON) for THIS block only,
# regardless of the live defaults (08-07: live reverted to cap=-10 / filter OFF — the filter LOGIC still must work).
_svm, _swf, _scap = c.S_VOL_MULT, c.S_WICK_FRAC, c.S_COIN7D_CAP
c.S_VOL_MULT, c.S_WICK_FRAC, c.S_COIN7D_CAP = 1.5, 0.5, 0.0
ok(c.short_signal("good")[0] is True, "fires: vol-spike(2x) + wick(0.85) on breakout bar")
ok(c.short_signal("lowvol")[0] is False, "no-fire: volume 1.2x < 1.5x (vol filter blocks)")
ok(c.short_signal("lowwick")[0] is False, "no-fire: wick 0.10 < 0.5 (wick filter blocks)")
c.S_VOL_MULT = 0; c.S_WICK_FRAC = 0
ok(c.short_signal("lowvol")[0] is True, "filter OFF (0/0) -> same setup fires (proves the filter is the blocker)")
c.S_VOL_MULT, c.S_WICK_FRAC, c.S_COIN7D_CAP = _svm, _swf, _scap; c.ss.fetch_klines = _sfk

print("\n=== GREEN-LOCK (per-leg trail activation, 09-07) ===")
ok(c.L_TRAIL_ACT == 0.010, "GOLDEN sync: LONG trail arms at +1.0% (JUL-5 exits)")
ok(c.S_TRAIL_ACT == 0.0075, "SHORT trail arms +0.75% (12-07 plateau, synced with combo)")
# LONG trail activates at +0.5%: a long that peaked +0.6% then reverses should trail (stop moves up), not fall to SL
e = 100.0; mh = 100.6                                  # +0.6% peak
long_trailed = mh >= e*(1+c.L_TRAIL_ACT)
ok(not long_trailed, "GOLDEN sync: +0.6% does NOT arm the trail (needs +1.0%)")
short_trailed = 99.4 <= e*(1-c.S_TRAIL_ACT)            # short at +0.6% favorable
ok(not short_trailed, "SHORT trail does NOT activate at +0.6% (still needs +1%)")

print("\n=== OI-CAPITULATION gate (09-07, watched) ===")
_hg = c.ss.http_get
c.ss.http_get = lambda url, **k: [{"sumOpenInterest": "1000"}, {"sumOpenInterest": "990"}]   # -1% (falling)
ok(abs(c.oi_slope("X") - (-0.01)) < 1e-9, "oi_slope computes contracts slope (-1% = capitulation)")
c.ss.http_get = lambda url, **k: [{"sumOpenInterest": "1000"}, {"sumOpenInterest": "1020"}]  # +2% (rising)
ok(c.oi_slope("X") > 0, "oi_slope positive when OI rising (crowded)")
c.ss.http_get = lambda url, **k: {"_error": 404}
ok(c.oi_slope("X") is None, "oi_slope None on API error (fail-safe -> gate inert)")
c.ss.http_get = _hg
ok(c.L_OI_CAPIT == 0.0, "OI HARD gate OFF (would cut 80% of longs on n=16)")
ok(c.L_OI_CAPIT_SOFT == 0.0, "GOLDEN sync: OI-SOFT OFF (it half-sized 93% of golden-day fills)")
# fail-safe: oi_slope None -> full size (never penalize on API error)
c.ss.http_get = lambda url, **k: {"_error": "net"}
ok(c.oi_slope("X") is None, "OI API error -> None -> gate inert (full size), never a forced size-down")
c.ss.http_get = _hg

print("\n=== FUNDING-PERCENTILE size-up (09-07, audit#3, watched ON) ===")
# latest funding is the 30d MINIMUM -> percentile 0.0 = bottom-decile = crowded-short = qualifies for x1.5
c.ss.http_get = lambda url, **k: [{"fundingRate": "0.0005"}]*9 + [{"fundingRate": "-0.0005"}]
ok(abs(c.fund_pct("X") - 0.0) < 1e-9, "fund_pct 0.0 when latest funding is the 30d low (bottom decile)")
# latest funding is the 30d MAXIMUM -> percentile 0.9 = top = does NOT qualify
c.ss.http_get = lambda url, **k: [{"fundingRate": "-0.0005"}]*9 + [{"fundingRate": "0.0005"}]
ok(abs(c.fund_pct("X") - 0.9) < 1e-9, "fund_pct 0.9 when latest funding is the 30d high (top)")
# fail-safe: too few points / API error -> None -> no boost (base size)
c.ss.http_get = lambda url, **k: [{"fundingRate": "0.0"}]*5
ok(c.fund_pct("X") is None, "fund_pct None on thin/short history (fail-safe -> no boost)")
c.ss.http_get = lambda url, **k: {"_error": 404}
ok(c.fund_pct("X") is None, "fund_pct None on API error (fail-safe -> base size, never penalize)")
c.ss.http_get = _hg
ok(c.L_FUND_SIZEUP == 0.10 and c.L_FUND_BOOST == 1.5, "FUND size-up watched ON: bottom-decile(<=0.10) long sized x1.5 (rollback L_FUND_SIZEUP=0)")

print("\n=== RISK-1: live_positions() None on API error -> skip mgmt, never phantom-close ===")
_svp = c.live_positions; _svl = c.LIVE
c.LIVE = True
c.live_positions = lambda: None                     # simulate Binance HTTP-error (429/418/5xx) -> None (not {})
st_r1 = {"long": [{"coin": "ZZ", "state": "OPEN", "entry": 100.0, "qty": 1.0, "sl": 99.0, "tp_oid": "T", "sl_oid": "S",
                   "bs_oid": "B", "ts": c.now(), "ft": c.now(), "maxhigh": 100.0}],
         "short": [], "realized": 0.0, "wins": 0, "losses": 0}
c.manage_long(st_r1)
ok(len(st_r1["long"]) == 1 and st_r1["losses"] == 0, "API error (live_positions None) -> open long KEPT, no phantom SL book, slot not freed")
st_r2 = {"short": [{"coin": "YY", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 101.0, "tp": 96.0, "tp_oid": "T",
                    "sl_oid": "S", "bs_oid": "B", "ts": c.now(), "ft": c.now(), "minlow": 100.0}],
         "long": [], "realized": 0.0, "wins": 0, "losses": 0}
c.manage_short(st_r2)
ok(len(st_r2["short"]) == 1 and st_r2["losses"] == 0, "API error -> open short KEPT too (both legs guarded)")
c.live_positions = _svp; c.LIVE = _svl

print("\n=== #1 LIQUIDATION size-up + #2 BID-REPRICE (edges, watched) ===")
_sk = c.COINALYZE_KEY; c.COINALYZE_KEY = ""
ok(c.liq_flush("BTCUSDT") is None, "liq_flush None when no Coinalyze key (FALLBACK -> no boost, trade proceeds normally)")
c.COINALYZE_KEY = _sk
ok(c.L_LIQ_BOOST == 1.5 and c.L_LIQ_HI == 800.0, "#1 liq size-up: MODERATE flush (0<liq20<$800) -> x1.5 (rollback L_LIQ_SIZEUP=0)")
ok(c.L_BID_REPRICE == 0.3, "#2 bid-reprice: shallow reject (<=0.3% past limit) re-places maker at bid (rollback L_BID_REPRICE=0)")

print("\n=== MOMENTUM signal detection (mom_signal: 3 setups on a crafted movers feature set) ===")
def momfeat(cclose=101.0, o=100.5, hi=101.5, lo=100.0, atr=0.5, up=True, spread=0.03, er=0.5, ext=1.0, cgt7=True):
    n = 100
    f = dict(o=[o]*n, h=[100.05]*n, l=[99.95]*n, c=[100.0]*n, v=[1000.0]*n,
             up=up, spread=spread, cgt7=cgt7, er=er, ext=ext, atr1h=atr, ema50=99.0)
    f["c"][-1] = cclose; f["h"][-1] = hi; f["l"][-1] = lo; f["o"][-1] = o; f["v"][-1] = 5000.0   # breakout bar: +1%, vol 5x
    return f
_svgf = c.get_feat
c.get_feat = lambda s: momfeat()
sig = c.mom_signal("MX", 95)                                   # top-decile RS + breakout of the 100.05 base on 5x vol
ok(sig is not None and sig[0] == "break", f"base-breakout fires 'break' setup (got {sig})")
ok(sig and sig[1] == 101.0 and sig[2] < 101.0, "break: entry ref = breakout close, stop below (tight R)")
c.get_feat = lambda s: momfeat(up=False)                      # not an uptrend
ok(c.mom_signal("MX", 95) is None, "no-fire when EMA stack not up (quality gate)")
c.get_feat = lambda s: momfeat()
ok(c.mom_signal("MX", 50) is None, "no-fire when RS below top-decile (50 < M_RS_LONG 90)")
c.get_feat = lambda s: None
ok(c.mom_signal("MX", 95) is None, "no features -> no signal (safe)")
c.get_feat = _svgf

c.M_ENABLED = True    # soldier defaults M_ENABLED OFF (momentum refuted); enable it to test the leg's logic in isolation
print("\n=== manage_momentum: strat-tag routing + arm@+1R + velocity chandelier trail (paper) ===")
_svm2 = dict(get_feat=c.get_feat, price_now=c.price_now, LIVE=c.LIVE)
c.LIVE = False
c.get_feat = lambda s: momfeat(atr=0.2)   # small ATR so the WIDE k=4 chandelier floor sits above breakeven (tests the trail, not the be floor)
_mp = {"v": 100.5}; c.price_now = lambda s: _mp["v"]
# a) PENDING ignition fills instantly in paper (IOC), retest waits
stfill = {"equity": 56.0, "long": [], "short": [], "mom": [
    {"coin": "IG", "strat": "mom", "setup": "ignite", "entry": 100.0, "stop": 99.0, "sl": 99.0, "R": 1.0, "brk": 100.0,
     "qty": 1.0, "oid": "mp", "state": "PENDING", "armed": False, "best": 100.0, "be": 100.0, "fill_ts": c.now(),
     "created": c.now(), "rs": 95, "minlow": 100.0, "last_hi_ts": c.now()}],
    "realized": 0.0, "wins": 0, "losses": 0, "day": "", "day_eq": 56.0}
c.manage_momentum(stfill)
ok(stfill["mom"] and stfill["mom"][0]["state"] == "OPEN", "PENDING ignition -> OPEN (paper IOC fills instantly)")
# b) OPEN runner: arm at +1R then chandelier-trail out in PROFIT
def mkmom():
    return {"equity": 56.0, "long": [], "short": [], "mom": [
        {"coin": "RUN", "strat": "mom", "setup": "break", "entry": 100.0, "stop": 99.0, "sl": 99.0, "R": 1.0, "brk": 100.0,
         "qty": 1.0, "oid": "o", "state": "OPEN", "armed": False, "best": 100.0, "be": 100.0, "fill_ts": c.now(),
         "created": c.now(), "rs": 95, "minlow": 100.0, "last_hi_ts": c.now()}],
        "realized": 0.0, "wins": 0, "losses": 0, "day": "", "day_eq": 56.0}
stm = mkmom(); eq0 = stm["equity"]
_mp["v"] = 100.5; c.manage_momentum(stm)
ok(stm["mom"] and not stm["mom"][0]["armed"], "at +0.5% (< +1R): not armed yet, stays open")
_mp["v"] = 101.2; c.manage_momentum(stm)
ok(stm["mom"] and stm["mom"][0]["armed"], "at +1.2% (>= +1R): ARMED (breakeven + chandelier active)")
_mp["v"] = 101.6; c.manage_momentum(stm)   # new high < +2R (no pyramid) -> best=101.6, chandelier floor ~101.6-4.6*0.2=100.68
_mp["v"] = 100.5; c.manage_momentum(stm)   # reverse into the wide chandelier -> TRAIL exit well above breakeven
ok(not stm["mom"] and stm["wins"] == 1, f"WIDE chandelier trails + exits in PROFIT (wins={stm['wins']})")
ok(stm["equity"] > eq0, "momentum trail exit credited a profit (long-direction booking via strat=mom)")
ok(c.M_TRAIL_K >= 3.5, f"momentum trail widened to keep the fat tail (M_TRAIL_K={c.M_TRAIL_K}, was ~2.5)")

print("\n=== PHASE-2 PYRAMID: add +0.5x at +2R, aggregate stop -> breakeven, blended booking ===")
_svpy = dict(get_feat=c.get_feat, price_now=c.price_now, LIVE=c.LIVE, pyr=c.M_PYRAMID)
c.LIVE = False; c.M_PYRAMID = True
c.get_feat = lambda s: momfeat(atr=0.2); _pp = {"v": 100.0}; c.price_now = lambda s: _pp["v"]
def mkmom2():
    return {"equity": 56.0, "long": [], "short": [], "mom": [
        {"coin": "PY", "strat": "mom", "setup": "break", "entry": 100.0, "stop": 99.0, "sl": 99.0, "R": 1.0, "brk": 100.0,
         "qty": 1.0, "oid": "o", "state": "OPEN", "armed": False, "best": 100.0, "be": 100.0, "fill_ts": c.now(),
         "created": c.now(), "rs": 95, "minlow": 100.0, "last_hi_ts": c.now(), "added": False, "add_entry": None, "add_qty": 0.0}],
        "realized": 0.0, "wins": 0, "losses": 0, "day": "", "day_eq": 56.0}
stp = mkmom2()
_pp["v"] = 101.2; c.manage_momentum(stp)                     # arm at +1R
_pp["v"] = 102.0; c.manage_momentum(stp)                     # reach +2R -> PYRAMID
p = stp["mom"][0]
ok(p["added"] and abs(p["add_qty"]-0.5) < 1e-9 and p["add_entry"] == 102.0, "pyramid adds +0.5x at +2R")
ok(p["stop"] >= p["be"], "aggregate stop raised to breakeven (enlarged stack can't lose)")
_pp["v"] = 108.0; c.manage_momentum(stp)                     # runner extends
_pp["v"] = 103.0; c.manage_momentum(stp)                     # trail out; blended entry = (100*1 + 102*0.5)/1.5 = 100.67
ok(not stp["mom"] and stp["wins"] == 1 and stp["equity"] > 56.0, "pyramided runner exits in profit (blended base+add booking)")
c.M_PYRAMID = False; stnp = mkmom2()
_pp["v"] = 101.2; c.manage_momentum(stnp); _pp["v"] = 102.5; c.manage_momentum(stnp)
ok(not stnp["mom"][0]["added"], "M_PYRAMID=0 -> no add (toggle works)")
for k, v in _svpy.items(): setattr(c, k if k != "pyr" else "M_PYRAMID", v)
# c) hard stop pre-arm cuts a loser
stl = mkmom(); _mp["v"] = 98.5; c.manage_momentum(stl)
ok(not stl["mom"] and stl["losses"] == 1, "pre-arm price <= stop -> SL exit booked as a loss")
for k, v in _svm2.items(): setattr(c, k, v)

print("\n=== book() strat=mom: long-direction PnL, NO dip loss-streak coupling ===")
stbm = {"equity": 56.0, "long": [], "short": [], "mom": [], "realized": 0.0, "wins": 0, "losses": 0,
        "loss_streak": 0, "paused_until": 0}
c.book(stbm, "mom", {"coin": "Z", "entry": 100.0, "qty": 1.0}, 103.0, "TRAIL")   # mom win (price ROSE -> long-direction)
ok(stbm["wins"] == 1 and stbm["equity"] > 56.0, "mom win (exit>entry) credits equity (long-direction, not short)")
c.book(stbm, "mom", {"coin": "Z2", "entry": 100.0, "qty": 1.0}, 98.0, "SL")      # mom loss
ok(stbm["losses"] == 1 and stbm["loss_streak"] == 0 and stbm["paused_until"] == 0,
   "mom loss does NOT drive the dip loss-streak breaker (only the dip leg pauses the dip leg)")

print("\n=== SHORT-LIQ CATALYST GATE (short_liq_ok: fade only on a real long-liq flush) ===")
_svlq = dict(lf=c.liq_flush, mn=c.S_LIQ_MIN)
c.S_LIQ_MIN = 0.0
ok(c.short_liq_ok("X") is True, "S_LIQ_MIN=0 -> gate OFF (always allow, default)")
c.S_LIQ_MIN = 552.0
c.liq_flush = lambda s: 600.0
ok(c.short_liq_ok("X") is True, "long-liq20 $600 >= $552 catalyst -> ALLOW fade (100% WR bucket)")
c.liq_flush = lambda s: 100.0
ok(c.short_liq_ok("X") is False, "long-liq20 $100 < $552 (no catalyst = squeeze risk) -> SKIP fade")
c.liq_flush = lambda s: 0.0
ok(c.short_liq_ok("X") is False, "long-liq20 $0 (pure fade, no flush) -> SKIP")
c.liq_flush = lambda s: None
ok(c.short_liq_ok("X") is True, "Coinalyze down (None) -> FAIL-SAFE fire anyway (a data outage must not kill shorts)")
c.liq_flush = _svlq["lf"]; c.S_LIQ_MIN = _svlq["mn"]

print("\n=== SCAN-PERF: _pmap parallel fetch preserves order + serial fallback ===")
ok(c._pmap(lambda x: x*2, [1, 2, 3, 4]) == [2, 4, 6, 8], "parallel map preserves input order (deterministic ranking)")
ok(c._pmap(lambda x: x*2, [1, 2, 3, 4], workers=1) == [2, 4, 6, 8], "workers=1 serial fallback matches")
ok(c._pmap(lambda x: x, []) == [], "empty input -> empty (no pool spun up)")
ok(c._pmap(lambda x: 1/0 if x == 2 else x, [1, 2, 3]) == [1, None, 3], "per-item exception -> None, others unaffected")
ok(c.M_FETCH_WORKERS >= 2, "parallel fetch workers configured (movers scan ~144s -> ~12s)")

print("\n=== ULTRA config: momentum enabled, caps, movers universe params ===")
ok(c.M_ENABLED is True, "M_ENABLED default ON (momentum IS ultra's reason to exist; M_ENABLED=0 => pure combo)")
ok(c.M_POOL == 300 and c.M_N_UNI == 120 and c.M_RS_LONG == 90, "movers universe: top-300 pool, 120 universe, top-decile RS")
ok(c.M_SETUPS == ["break", "retest", "ignite"], "3 momentum setups configured")
ok("mom" in c.load_state() and isinstance(c.load_state()["mom"], list), "state carries a 'mom' list")

print("\n=== FADE-QUALITY FIX 2: PUMP CEILING (S_PUMP_MAX) skips a rocket, not a bust (15-07) ===")
_svpm = dict(fk=c.ss.fetch_klines, pmax=c.S_PUMP_MAX, cap=c.S_COIN7D_CAP, vm=c.S_VOL_MULT, wf=c.S_WICK_FRAC)
def _fbars(bhigh):
    """194 flat-100 bars (N20-high=100, coin7d=0), a breakout bar high=bhigh, then a reclaim close<100 -> fires; pump=(bhigh-100)%."""
    bars = [[t*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0] for t in range(194)]
    bars[192] = [192*3600000, 100.0, bhigh, 100.0, 100.3, 1000.0]   # breakout bar: high=bhigh > lvl 100
    bars[193] = [193*3600000, 100.0, 100.2, 99.0, 99.5, 1000.0]     # reclaim: close 99.5 < 100 -> fire
    bars.append([194*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0])  # dropped by k[:-1]
    return bars
c.S_VOL_MULT = 0; c.S_WICK_FRAC = 0; c.S_COIN7D_CAP = 1000.0        # flat coin7d=0 -> lift the weak-gate for this block only
c.ss.fetch_klines = lambda sym, tf, limit=0: _fbars(106.0)           # +6% pump
c.S_PUMP_MAX = 0                                                      # ceiling OFF
ok(c.short_signal("X")[0] is True, "S_PUMP_MAX=0 (off): a +6% pump still fires (byte-identical to before)")
c.S_PUMP_MAX = 3.0                                                    # ceiling +3%
ok(c.short_signal("X")[0] is False, "ceiling +3%: a +6% pump (a rocket) is SKIPPED (no fire)")
c.ss.fetch_klines = lambda sym, tf, limit=0: _fbars(102.0)           # +2% pump
ok(c.short_signal("X")[0] is True, "ceiling +3%: a +2% pump (a real bust) still fires")
c.ss.fetch_klines = lambda sym, tf, limit=0: _fbars(100.5)           # +0.5% pump (< S_PUMP_MIN 1.0)
ok(c.short_signal("X")[0] is False, "pump +0.5% < S_PUMP_MIN still no-fire (min gate unchanged by the ceiling)")
c.ss.fetch_klines = _svpm["fk"]; c.S_PUMP_MAX = _svpm["pmax"]; c.S_COIN7D_CAP = _svpm["cap"]
c.S_VOL_MULT = _svpm["vm"]; c.S_WICK_FRAC = _svpm["wf"]

print("\n=== FADE-QUALITY: c7d FLOOR (S_COIN7D_MIN) — fade only FLAT-to-STRONG coins (16-07, Artem) ===")
_svc7 = dict(fk=c.ss.fetch_klines, mn=c.S_COIN7D_MIN, cap=c.S_COIN7D_CAP, pm=c.S_PUMP_MAX, vm=c.S_VOL_MULT, wf=c.S_WICK_FRAC)
def _c7bars(early_close, bhigh=102.0):
    """like _fbars but first 40 bars at `early_close` so the 7d anchor (C[bar25]) sets coin7d=(99.5/early_close-1)*100."""
    bars = [[t*3600000, early_close, early_close, early_close, early_close, 1000.0] for t in range(40)]
    bars += [[t*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0] for t in range(40, 194)]
    bars[192] = [192*3600000, 100.0, bhigh, 100.0, 100.3, 1000.0]   # breakout bar high=bhigh>100
    bars[193] = [193*3600000, 100.0, 100.2, 99.0, 99.5, 1000.0]     # reclaim close 99.5<100 -> fire
    bars.append([194*3600000, 100.0, 100.0, 100.0, 100.0, 1000.0])  # dropped by k[:-1]
    return bars
c.S_VOL_MULT = 0; c.S_WICK_FRAC = 0; c.S_PUMP_MAX = 0; c.S_COIN7D_CAP = 1000.0   # lift ceiling/pump-max, isolate the floor
c.ss.fetch_klines = lambda sym, tf, limit=0: _c7bars(110.0)          # early 110 -> coin7d ≈ -9.5% (weak coin)
c.S_COIN7D_MIN = -1e9
ok(c.short_signal("X")[0] is True, "floor OFF (-1e9): a weak coin (c7d≈-9.5%) still fires (default byte-identical)")
c.S_COIN7D_MIN = 0.0
ok(c.short_signal("X")[0] is False, "floor 0: a weak coin (c7d≈-9.5%) is SKIPPED (no fire)")
c.ss.fetch_klines = lambda sym, tf, limit=0: _c7bars(90.0)           # early 90 -> coin7d ≈ +10.6% (strong coin)
ok(c.short_signal("X")[0] is True, "floor 0: a strong coin (c7d≈+10.6%) still fires (fades the overextended)")
c.ss.fetch_klines = _svc7["fk"]; c.S_COIN7D_MIN = _svc7["mn"]; c.S_COIN7D_CAP = _svc7["cap"]
c.S_PUMP_MAX = _svc7["pm"]; c.S_VOL_MULT = _svc7["vm"]; c.S_WICK_FRAC = _svc7["wf"]

print("\n=== FADE-QUALITY FIX 3: fade LOSS-STREAK BREAKER (S_LOSS_STREAK_N) in book() + open_short (15-07) ===")
_svls = dict(N=c.S_LOSS_STREAK_N, cd=c.S_STREAK_COOLDOWN_SEC, ss=c.short_signal, live=c.LIVE)
c.LIVE = False
def _sst(**kw):
    base = {"equity": 30.0, "long": [], "short": [], "mom": [], "realized": 0.0, "wins": 0, "losses": 0,
            "slot_turn": "short", "last_sig": {}, "sfires": -1, "fade_streak": 0, "fade_paused_until": 0,
            "fade_sl_ts": {}, "fade_pause_logged": 0, "fade_cd_logged": {}}
    base.update(kw); return base
c.S_LOSS_STREAK_N = 3; c.S_STREAK_COOLDOWN_SEC = 3600
stk = _sst()
c.book(stk, "short", {"coin": "F1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # short SL loss (exit>entry -> loss)
ok(stk["fade_streak"] == 1 and stk["fade_paused_until"] == 0, "fade SL loss #1 -> streak=1, not yet paused")
c.book(stk, "short", {"coin": "F2", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(stk["fade_streak"] == 2 and stk["fade_paused_until"] == 0, "fade SL loss #2 -> streak=2, still not paused (< N=3)")
c.book(stk, "short", {"coin": "F3", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(stk["fade_streak"] == 3 and stk["fade_paused_until"] > c.now(), "fade SL loss #3 == N -> PAUSE armed (fade_paused_until in future)")
c.book(stk, "short", {"coin": "F4", "entry": 100.0, "qty": 1.0}, 98.0, "TP")    # short WIN (exit<entry)
ok(stk["fade_streak"] == 0 and stk["fade_paused_until"] == 0, "a fade WIN resets the streak AND clears the pause")
stg = _sst(fade_streak=1)
c.book(stg, "short", {"coin": "M1", "entry": 100.0, "qty": 1.0}, 99.0, "SL")    # tag 'SL' but exit<entry -> POSITIVE (mislabeled manual close)
ok(stg["fade_streak"] == 0 and stg["fade_paused_until"] == 0, "mislabeled positive 'SL' (manual close in profit) is NOT counted as a stop (treated as a win)")
c.S_LOSS_STREAK_N = 0
sto = _sst()
for coin in ("A", "B", "C", "D"): c.book(sto, "short", {"coin": coin, "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(sto["fade_paused_until"] == 0, "FIX3 OFF (N=0): consecutive SL stops never arm a pause (no-op)")
# open_short pause gate: never fire -> isolates the gate; sfires stays at the -1 sentinel only if it returned EARLY
c.short_signal = lambda sym, tf="1h", bpd=24: (False, None, None, None, 0.0, 0.0)
c.S_LOSS_STREAK_N = 2
st_p = _sst(fade_paused_until=c.now()+3600)
c.open_short(st_p, ["AAAUSDT"])
ok(st_p["sfires"] == -1, "FIX3 paused: open_short returns EARLY (no new fades armed) while fade_paused_until is in the future")
c.S_LOSS_STREAK_N = 0
st_np = _sst(fade_paused_until=c.now()+3600)
c.open_short(st_np, ["AAAUSDT"])
ok(st_np["sfires"] == 0, "FIX3 OFF (N=0): the pause is ignored -> open_short proceeds (proves the gate is the cause)")
c.S_LOSS_STREAK_N = 2
st_u = _sst(fade_paused_until=c.now()-1)
c.open_short(st_u, ["AAAUSDT"])
ok(st_u["sfires"] == 0, "FIX3 cooldown EXPIRED (fade_paused_until in the past) -> pause lifts, open_short proceeds again")
c.S_LOSS_STREAK_N = _svls["N"]; c.S_STREAK_COOLDOWN_SEC = _svls["cd"]; c.short_signal = _svls["ss"]; c.LIVE = _svls["live"]

print("\n=== FADE-QUALITY FIX 4: PER-COIN fade cooldown (S_COIN_COOLDOWN_SEC) in book() + open_short (15-07) ===")
_svcc = dict(cc=c.S_COIN_COOLDOWN_SEC, N=c.S_LOSS_STREAK_N, ss=c.short_signal, lq=c.short_liq_ok, live=c.LIVE)
c.LIVE = False; c.S_LOSS_STREAK_N = 0
c.S_COIN_COOLDOWN_SEC = 7200
stc = _sst()
c.book(stc, "short", {"coin": "CDUSDT", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(abs(stc["fade_sl_ts"].get("CDUSDT", 0) - c.now()) < 5, "FIX4: a real fade SL records the coin's last-stop timestamp")
c.book(stc, "short", {"coin": "CDUSDT", "entry": 100.0, "qty": 1.0}, 98.0, "TP")   # a later WIN must NOT wipe the coin's SL ts
ok("CDUSDT" in stc["fade_sl_ts"], "FIX4: a subsequent win does not erase the per-coin SL timestamp (cooldown still enforced)")
# open_short: mock a fire; short_liq_ok=False stops arming right AFTER the cooldown gate -> isolates FIX4, no exchange calls
c.short_signal = lambda sym, tf="1h", bpd=24: (True, int(c.now()*1000), 100.0, 103.0, 2.0, -20.0)
c.short_liq_ok = lambda s: False
st_cd = _sst(fade_sl_ts={"CDUSDT": c.now()-60})
c.open_short(st_cd, ["CDUSDT"])
ok(st_cd["fade_cd_logged"].get("CDUSDT") == st_cd["fade_sl_ts"]["CDUSDT"] and len(st_cd["short"]) == 0,
   "FIX4: coin stopped 60s ago (< 2h) -> SKIPPED at the cooldown gate (S-COIN-CD logged, nothing armed)")
st_ex = _sst(fade_sl_ts={"CDUSDT": c.now()-8000})
c.open_short(st_ex, ["CDUSDT"])
ok("CDUSDT" not in st_ex["fade_cd_logged"], "FIX4: cooldown EXPIRED (8000s > 7200) -> NOT skipped (passes the gate onward to arming)")
c.S_COIN_COOLDOWN_SEC = 0
st_off = _sst(fade_sl_ts={"CDUSDT": c.now()-60})
c.open_short(st_off, ["CDUSDT"])
ok("CDUSDT" not in st_off["fade_cd_logged"], "FIX4 OFF (0): no per-coin cooldown skip even on a fresh stop (no-op)")
c.S_COIN_COOLDOWN_SEC = _svcc["cc"]; c.S_LOSS_STREAK_N = _svcc["N"]; c.short_signal = _svcc["ss"]; c.short_liq_ok = _svcc["lq"]; c.LIVE = _svcc["live"]

print("\n=== FADE-QUALITY FIX 5: BOOK-WIDE circuit-breaker (S_BOOK_BREAKER_N, time-window) in book() + open_short (16-07) ===")
_svbb = dict(N=c.S_BOOK_BREAKER_N, w=c.S_BOOK_WINDOW_SEC, cd=c.S_BOOK_COOLDOWN_SEC, sn=c.S_LOSS_STREAK_N, ss=c.short_signal, live=c.LIVE)
c.LIVE = False; c.S_LOSS_STREAK_N = 0   # isolate FIX5 from the FIX3 consecutive-streak pause
c.S_BOOK_BREAKER_N = 2; c.S_BOOK_WINDOW_SEC = 1800; c.S_BOOK_COOLDOWN_SEC = 3600
stb1 = _sst()
c.book(stb1, "short", {"coin": "B1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # stop #1
ok(len(stb1.get("fade_stop_times", [])) == 1 and stb1.get("book_paused_until", 0) == 0, "book: stop #1 in window -> tracked, not yet paused (< N=2)")
c.book(stb1, "short", {"coin": "B2", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # stop #2 within window
ok(stb1.get("book_paused_until", 0) > c.now(), "book: 2 stops within 30m -> BOOK-WIDE pause armed (all fades)")
# a WIN between stops must NOT clear the book-wide pause (the key difference vs the FIX3 consecutive streak)
stb2 = _sst()
c.book(stb2, "short", {"coin": "W1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # stop #1
c.book(stb2, "short", {"coin": "W2", "entry": 100.0, "qty": 1.0}, 98.0, "TP")    # a WIN in between
c.book(stb2, "short", {"coin": "W3", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # stop #2 -> 2 stops in window despite the win
ok(stb2.get("book_paused_until", 0) > c.now(), "book: a WIN between two stops does NOT reset the time-window breaker (still trips)")
# a stop OUTSIDE the window is pruned -> does not accumulate
stb3 = _sst(fade_stop_times=[c.now()-2000])   # 2000s ago > 1800s window
c.book(stb3, "short", {"coin": "O1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(len(stb3["fade_stop_times"]) == 1 and stb3.get("book_paused_until", 0) == 0, "book: a stop older than the window is pruned -> only the fresh one counts (no pause)")
# open_short gate: paused -> returns EARLY (isolate via a never-firing signal)
c.short_signal = lambda sym, tf="1h", bpd=24: (False, None, None, None, 0.0, 0.0)
st_bp = _sst(book_paused_until=c.now()+3600, book_pause_logged=0, fade_stop_times=[c.now(), c.now()])
c.open_short(st_bp, ["ZZZUSDT"])
ok(st_bp.get("book_pause_logged") == st_bp["book_paused_until"], "open_short: book-wide pause active -> S-BOOK-PAUSE logged, arming blocked")
c.S_BOOK_BREAKER_N = 0
st_bo = _sst()
for coin in ("X", "Y", "Z"): c.book(st_bo, "short", {"coin": coin, "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(st_bo.get("book_paused_until", 0) == 0, "FIX5 OFF (N=0): stops within a window never arm the book-wide pause (no-op)")
c.S_BOOK_BREAKER_N = _svbb["N"]; c.S_BOOK_WINDOW_SEC = _svbb["w"]; c.S_BOOK_COOLDOWN_SEC = _svbb["cd"]; c.S_LOSS_STREAK_N = _svbb["sn"]; c.short_signal = _svbb["ss"]; c.LIVE = _svbb["live"]

print("\n=== FADE-QUALITY FIX 5b: BREAKEVEN-TIGHTEN open fades on a book-breaker trip (S_BOOK_BE_TIGHTEN) (16-07) ===")
_svbe = dict(be=c.S_BOOK_BE_TIGHTEN, N=c.S_BOOK_BREAKER_N, sn=c.S_LOSS_STREAK_N, pn=c.price_now, sp=c.specs, live=c.LIVE)
c.LIVE = False; c.S_LOSS_STREAK_N = 0; c.S_BOOK_BREAKER_N = 2; c.S_BOOK_WINDOW_SEC = 1800; c.specs = lambda s: None
# trip WITH be-tighten on -> pending flag set
c.S_BOOK_BE_TIGHTEN = 1
stt = _sst()
c.book(stt, "short", {"coin": "T1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
c.book(stt, "short", {"coin": "T2", "entry": 100.0, "qty": 1.0}, 100.9, "SL")   # 2nd stop -> trip
ok(stt.get("book_be_pending") is True, "trip with S_BOOK_BE_TIGHTEN=1 -> flags book_be_pending for the next manage tick")
# GREEN open fade (px<entry): keep it, stop -> breakeven
c.price_now = lambda s: 99.0
stg = _sst(short=[{"coin": "GRN", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 100.5, "tp": 99.5, "tp_oid": None, "sl_oid": None, "ts": c.now()}])
c.book_be_tighten(stg)
ok(len(stg["short"]) == 1 and abs(stg["short"][0]["stop"] - 100.0) < 1e-9, "GREEN fade (px<entry): kept open, stop moved to breakeven (100.0)")
# RED open fade (px>=entry): closed now at ~breakeven, booked tag BE, counters untouched
c.price_now = lambda s: 100.5
sr = _sst(short=[{"coin": "RED", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 100.5, "tp": 99.5, "tp_oid": None, "sl_oid": None, "ts": c.now()}])
c.book_be_tighten(sr)
ok(len(sr["short"]) == 0, "RED fade (px>=entry): closed now (removed from the book)")
ok(sr.get("fade_stop_times", []) == [] and sr.get("book_paused_until", 0) == 0, "BE close does NOT feed the book/streak counters (no cascade)")
# OFF: trip does not flag a tighten
c.S_BOOK_BE_TIGHTEN = 0
sto2 = _sst()
c.book(sto2, "short", {"coin": "O1", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
c.book(sto2, "short", {"coin": "O2", "entry": 100.0, "qty": 1.0}, 100.9, "SL")
ok(sto2.get("book_be_pending") is None or sto2.get("book_be_pending") is False, "FIX5b OFF: a trip does NOT flag breakeven-tighten (book-breaker still pauses)")
c.S_BOOK_BE_TIGHTEN = _svbe["be"]; c.S_BOOK_BREAKER_N = _svbe["N"]; c.S_LOSS_STREAK_N = _svbe["sn"]; c.price_now = _svbe["pn"]; c.specs = _svbe["sp"]; c.LIVE = _svbe["live"]

print("\n=== ORPHAN-FIX (20-07): partial entry fill -> cancel the remainder (live incident: ONDO/MET orphans) ===")
_svof = dict(live=c.LIVE, lp=c.live_positions, os_=c.order_status, ca=c.cancel, tp=c.limit_buy_tp,
             sm=c.stop_market_buy, sp=c.specs, pn=c.price_now)
c.LIVE = True
c.live_positions = lambda: {}
c.specs = lambda s: {"tick": 0.0001, "pp": 4, "step": 0.1, "qp": 1}
c.price_now = lambda s: 0.35
_calls = {"cancel": [], "tp": [], "sl": []}
c.limit_buy_tp = lambda sym, qty, px: (_calls["tp"].append((sym, qty)), {"orderId": 111})[1]
c.stop_market_buy = lambda sym, trig: (_calls["sl"].append((sym, trig)), {"algoId": 222})[1]
def _mkpos():
    return {"coin": "ORPHUSDT", "state": "PENDING", "oid": 9001, "entry": 0.3518, "qty": 156.7,
            "stop": 0.3536, "tp": None, "tp_oid": None, "sl_oid": None, "ts": c.now(),
            "fill_deadline": c.now()+9999, "cts": 1, "tf": "15m", "risk_frac": 0.005, "minlow": None, "added": False}
# case 1: PARTIALLY_FILLED -> cancel the remainder, size the position at the FINAL executedQty
_stage = {"n": 0}
def _os_partial(sym, oid):
    _stage["n"] += 1
    if _stage["n"] == 1: return {"status": "PARTIALLY_FILLED", "executedQty": "63.9", "avgPrice": "0.3518"}
    return {"status": "CANCELED", "executedQty": "63.9", "avgPrice": "0.3518"}   # post-cancel snapshot
c.order_status = _os_partial
c.cancel = lambda sym, oid: (_calls["cancel"].append((sym, oid)), {"orderId": oid})[1]
stp = {"long": [], "short": [_mkpos()], "mom": [], "snap": [], "trend": [], "wins": 0, "losses": 0,
       "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stp)
ok(_calls["cancel"] == [("ORPHUSDT", 9001)], "partial fill: the entry-limit REMAINDER is cancelled (the orphan can never be born)")
ok(stp["short"][0]["state"] == "OPEN" and abs(stp["short"][0]["qty"] - 63.9) < 1e-9,
   "partial fill: position sized at the FINAL executedQty (63.9), not the arm qty")
ok(_calls["tp"] and abs(_calls["tp"][0][1] - 63.9) < 1e-9, "partial fill: TP is placed for the executed 63.9")
# case 2: fully FILLED -> no cancel call
_calls["cancel"].clear(); _calls["tp"].clear()
c.order_status = lambda sym, oid: {"status": "FILLED", "executedQty": "156.7", "avgPrice": "0.3518"}
stf = {"long": [], "short": [_mkpos()], "mom": [], "snap": [], "trend": [], "wins": 0, "losses": 0,
       "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stf)
ok(_calls["cancel"] == [] and stf["short"][0]["qty"] == 156.7, "full fill: NO cancel, full qty kept (zero behavior change)")
# case 3: race — cancel arrives after the order just fully filled -> re-query sees FILLED, full qty taken
_stage2 = {"n": 0}
def _os_race(sym, oid):
    _stage2["n"] += 1
    if _stage2["n"] == 1: return {"status": "PARTIALLY_FILLED", "executedQty": "63.9", "avgPrice": "0.3518"}
    return {"status": "FILLED", "executedQty": "156.7", "avgPrice": "0.3519"}    # filled during the cancel race
c.order_status = _os_race
c.cancel = lambda sym, oid: {"_error": True, "_body": "order filled"}            # cancel lost the race
str_ = {"long": [], "short": [_mkpos()], "mom": [], "snap": [], "trend": [], "wins": 0, "losses": 0,
        "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(str_)
ok(abs(str_["short"][0]["qty"] - 156.7) < 1e-9 and abs(str_["short"][0]["entry"] - 0.3519) < 1e-9,
   "cancel race lost (already filled): re-query wins — FULL qty + true avgPrice tracked (nothing stranded)")
c.LIVE = _svof["live"]; c.live_positions = _svof["lp"]; c.order_status = _svof["os_"]; c.cancel = _svof["ca"]
c.limit_buy_tp = _svof["tp"]; c.stop_market_buy = _svof["sm"]; c.specs = _svof["sp"]; c.price_now = _svof["pn"]

print("\n=== TOTAL_SLOTS env-gate (20-07, Artem): 4 slots = smaller pool AND bigger per-slot sizing ===")
_svts = dict(ts=c.TOTAL_SLOTS, fc=c.S_FADE_CAP)
ok(c.TOTAL_SLOTS == 5, "default TOTAL_SLOTS = 5 (byte-identical without the env)")
c.TOTAL_SLOTS = 4; c.S_FADE_CAP = 4
st4 = {"long": [], "short": [{"coin": f"S{i}", "state": "OPEN"} for i in range(4)], "mom": [], "snap": [], "trend": []}
ok(not c.can_open(st4, "short"), "4 slots: pool FULL at 4 shorts -> can_open blocks the 5th")
st3 = {"long": [], "short": [{"coin": f"S{i}", "state": "OPEN"} for i in range(3)], "mom": [], "snap": [], "trend": []}
ok(c.can_open(st3, "short"), "4 slots: 3 open -> the 4th still opens")
n5 = 70.0/5*3; c.TOTAL_SLOTS = 5
s5 = c.short_notional(70.0, 0.005, 1.0)
c.TOTAL_SLOTS = 4
s4 = c.short_notional(70.0, 0.005, 1.0)
ok(s4 > s5 and abs(s4/s5 - 1.25) < 0.01, f"sizing scales with the divisor: notional x1.25 at 4 slots ({s5:.1f} -> {s4:.1f})")
c.TOTAL_SLOTS = _svts["ts"]; c.S_FADE_CAP = _svts["fc"]

print("\n=== GREENER: S1 liq-at-fill gate (GR_FILL_LIQ_MIN) — arm fail-closed + pending drop ===")
_svg = dict(g=c.GR_FILL_LIQ_MIN, l30=c.liq30, live=c.LIVE, ca=c.cancel, os_=c.order_status, lp=c.live_positions, pn=c.price_now)
c.LIVE = True; c.live_positions = lambda: {}; c.price_now = lambda s: 0.35
c.GR_FILL_LIQ_MIN = 1000.0
_gcalls = {"cancel": []}
c.cancel = lambda sym, oid: (_gcalls["cancel"].append((sym, oid)), {"orderId": oid})[1]
c.order_status = lambda sym, oid: {"status": "NEW", "executedQty": "0"}
def _gpos():
    return {"coin": "LIQUSDT", "state": "PENDING", "oid": 7001, "entry": 0.35, "qty": 100.0, "stop": 0.3553,
            "tp": None, "tp_oid": None, "sl_oid": None, "ts": c.now(), "fill_deadline": c.now()+9999,
            "cts": 1, "tf": "15m", "risk_frac": 0.015, "minlow": None, "added": False}
# catalyst died -> pending cancelled
c.liq30 = lambda s: 200.0
stq = {"long": [], "short": [_gpos()], "mom": [], "snap": [], "trend": [], "squeeze": [], "wins": 0, "losses": 0,
       "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stq)
ok(len(stq["short"]) == 0 and _gcalls["cancel"] == [("LIQUSDT", 7001)],
   "pending retest CANCELLED when liq30 drops below the floor (catalyst died -> S-LIQDROP)")
# catalyst alive -> pending survives
_gcalls["cancel"].clear()
c.liq30 = lambda s: 5000.0
stq2 = {"long": [], "short": [_gpos()], "mom": [], "snap": [], "trend": [], "squeeze": [], "wins": 0, "losses": 0,
        "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stq2)
ok(len(stq2["short"]) == 1 and not _gcalls["cancel"], "pending survives while liq30 >= floor")
# data outage while pending -> tolerated (no cancel; TTL still guards)
c.liq30 = lambda s: None
stq3 = {"long": [], "short": [_gpos()], "mom": [], "snap": [], "trend": [], "squeeze": [], "wins": 0, "losses": 0,
        "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stq3)
ok(len(stq3["short"]) == 1, "liq data outage while PENDING is tolerated (fail-closed applies at ARM, not mid-flight)")
# gate off -> byte-identical (no liq30 consulted)
c.GR_FILL_LIQ_MIN = 0.0
_probe = {"n": 0}
c.liq30 = lambda s: (_probe.__setitem__("n", _probe["n"] + 1), 0.0)[1]
stq4 = {"long": [], "short": [_gpos()], "mom": [], "snap": [], "trend": [], "squeeze": [], "wins": 0, "losses": 0,
        "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}}
c.manage_short(stq4)
ok(_probe["n"] == 0, "GR_FILL_LIQ_MIN=0: liq30 never consulted (default byte-identical)")
c.GR_FILL_LIQ_MIN = _svg["g"]; c.liq30 = _svg["l30"]; c.LIVE = _svg["live"]; c.cancel = _svg["ca"]
c.order_status = _svg["os_"]; c.live_positions = _svg["lp"]; c.price_now = _svg["pn"]

print("\n=== GREENER: E5 squeeze-fade — slots, book math, exits ===")
_sve = dict(sqe=c.SQF_ENABLED, cap=c.SQF_CAP, live=c.LIVE, pn=c.price_now, lp=c.live_positions,
            rc=c.real_close_fill, cg=c.cancel_algo, mb=c.mkt_buy_close)
def _sqst(**kw):
    b = {"long": [], "short": [], "mom": [], "snap": [], "trend": [], "squeeze": [], "wins": 0, "losses": 0,
         "realized": 0.0, "equity": 70.0, "last_sig": {}, "fade_sl_ts": {}, "fade_streak": 0}
    b.update(kw); return b
c.SQF_CAP = 1
ok(c.can_open(_sqst(), "squeeze"), "squeeze can open on an empty book")
ok(not c.can_open(_sqst(squeeze=[{"coin": "A"}]), "squeeze"), "SQF_CAP=1 blocks the 2nd squeeze")
c.SQF_CAP = 0
ok(not c.can_open(_sqst(), "squeeze"), "SQF_CAP=0: engine takes no slots (default inert)")
c.SQF_CAP = 1
# book(): squeeze is SHORT-direction with taker/taker fees
stb_sq = _sqst()
c.book(stb_sq, "squeeze", {"coin": "SQ1", "entry": 100.0, "qty": 1.0}, 99.0, "TIME")   # price fell 1% -> short WIN
exp = (100.0-99.0)/100.0*100 - (c.FEE_TK + c.FEE_TK)                                    # taker entry + taker TIME exit
ok(abs(stb_sq["realized"] - exp) < 1e-9 and stb_sq["wins"] == 1,
   "book(squeeze): SHORT-direction pnl with taker+taker fees (price fell => win)")
stb_sq2 = _sqst(fade_streak=0)
c.book(stb_sq2, "squeeze", {"coin": "SQ2", "entry": 100.0, "qty": 1.0}, 104.0, "SL")   # +4% catastrophe stop
ok(stb_sq2["losses"] == 1 and stb_sq2["fade_streak"] == 1 and "SQ2" in stb_sq2["fade_sl_ts"],
   "a squeeze SL feeds the SHORT-SIDE counters (streak + per-coin ts) — side-aware breaker")
# manage_squeeze: TIME exit fires after hold
c.LIVE = False; c.price_now = lambda s: 99.5
st_t = _sqst(squeeze=[{"coin": "TQ", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 104.0, "sl_oid": None,
                        "ts": c.now(), "fill_ts": c.now() - (c.SQF_HOLD_MIN*60 + 5), "maxadv": 100.0}])
c.manage_squeeze(st_t)
ok(len(st_t["squeeze"]) == 0 and st_t["wins"] == 1, "TIME exit covers the short after SQF_HOLD_MIN (booked as a win at 99.5)")
# manage_squeeze: catastrophe stop
c.price_now = lambda s: 104.2
st_s = _sqst(squeeze=[{"coin": "TQ2", "state": "OPEN", "entry": 100.0, "qty": 1.0, "stop": 104.0, "sl_oid": None,
                        "ts": c.now(), "fill_ts": c.now(), "maxadv": 100.0}])
c.manage_squeeze(st_s)
ok(len(st_s["squeeze"]) == 0 and st_s["losses"] == 1, "catastrophe stop (+4%) closes and books the loss")
# open_squeeze respects the short-side book pause
c.SQF_ENABLED = True
_sq_probe = {"n": 0}
c.squeeze_signal = lambda s: (_sq_probe.__setitem__("n", _sq_probe["n"] + 1), None)[1]
st_p = _sqst(book_paused_until=c.now() + 999)
c._LAST_SQ_BAR[0] = -1
_svbbn = c.S_BOOK_BREAKER_N; c.S_BOOK_BREAKER_N = 2
c.open_squeeze(st_p, ["AUSDT"])
ok(_sq_probe["n"] == 0, "open_squeeze BLOCKED during the short-side book-wide pause (never scans)")
c.S_BOOK_BREAKER_N = _svbbn
c.SQF_ENABLED = _sve["sqe"]; c.SQF_CAP = _sve["cap"]; c.LIVE = _sve["live"]; c.price_now = _sve["pn"]
c.live_positions = _sve["lp"]; c.real_close_fill = _sve["rc"]; c.cancel_algo = _sve["cg"]; c.mkt_buy_close = _sve["mb"]

print("\n=== GREENER: snap ORPHAN-FIX + cross-leg same-coin exclusion ===")
_svs = dict(live=c.LIVE, lp=c.live_positions, ca=c.cancel, sm=c.stop_market_sell, sp=c.specs, pn=c.price_now)
c.LIVE = True; c.specs = lambda s: {"tick": 0.0001, "pp": 4, "step": 0.1, "qp": 1}
c.price_now = lambda s: 1.0
_scalls = {"cancel": []}
c.cancel = lambda sym, oid: (_scalls["cancel"].append((sym, oid)), {"orderId": oid})[1]
c.stop_market_sell = lambda sym, trig: {"algoId": 900}
c.live_positions = lambda: {("SNPUSDT", "LONG" if c.HEDGE else "BOTH"): 55.0}
st_sn = _sqst(snap=[{"coin": "SNPUSDT", "state": "PENDING", "oid": 8001, "entry": 1.0, "qty": 100.0, "stop": 0.99,
                     "tp": 1.008, "created": c.now(), "fill_ts": None}])
c.manage_snapback(st_sn)
p = st_sn["snap"][0]
ok(p["state"] == "OPEN" and _scalls["cancel"] == [("SNPUSDT", 8001)] and abs(p["qty"] - 55.0) < 1e-9,
   "snap A-FILL: entry-limit remainder CANCELLED + qty taken from the EXCHANGE amt (orphan can't be born)")
ok("SNPUSDT" in c.held_all(_sqst(snap=[{"coin": "SNPUSDT"}])) and "SNPUSDT" in c.held_all(_sqst(squeeze=[{"coin": "SNPUSDT"}])),
   "held_all sees coins across ALL legs (cross-leg same-coin exclusion input)")
c.LIVE = _svs["live"]; c.live_positions = _svs["lp"]; c.cancel = _svs["ca"]; c.stop_market_sell = _svs["sm"]
c.specs = _svs["sp"]; c.price_now = _svs["pn"]

print(f"\n{'='*40}\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
