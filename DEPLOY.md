# GLADIATOR BOT — COMPLETE RECOVERY & OPERATING MANUAL
### For Artem, and for any AI model asked to work on this bot. Read top to bottom BEFORE touching anything.
*(Master copy lives in the repo; the ZIP build copies it in. The ZIP additionally contains `secrets/` + `systemd/` pulled from the live VPS.)*

---

## 🔴🔴 STOP — READ FIRST

### 1. The ZIP build of this archive contains LIVE TRADING KEYS to a REAL-MONEY Binance Futures account.
`secrets/binance.env` holds `BINANCE_KEY` / `BINANCE_SECRET`. `systemd/gladiator.service` holds `COINALYZE_KEY`.
**Anyone with these can trade this account.**

> **⚠️ BEFORE giving the ZIP to ANY AI model, chat, cloud, or person: DELETE the `secrets/` folder and
> redact `COINALYZE_KEY` from `systemd/gladiator.service`.** The bot deploys fine without them — paste them
> back at deploy time (§5). Pasting keys into an AI chat puts them in that vendor's logs forever.

### 2. NEVER deploy, commit, push, scp, or restart anything without Artem's explicit «так».
Standing, non-negotiable rule. Propose → wait for «так» → act. Every step gated, every step reversible.

### 3. This is REAL MONEY (~$74). Not paper.

---

## 1. WHAT THIS BOT IS

`gladiator_bot.py` — event-driven crypto futures bot on Binance USDⓈ-M perpetuals. **5 legs**, each env-gated:

| leg | what it does | live status |
|---|---|---|
| **FADE** (short) | shorts a *busted breakout*: coin breaks a 20-bar high, the breakout FAILS (closes back under), we rest a maker sell limit AT that level and short the retest, betting the level rejects it again | ✅ **THE ONLY LIVE LEG** |
| DIP (long) | maker dip-buy −1.2% w/ strength gates | OFF (`M_DIP_CAP=0`) |
| SNAP (long) | liquidation-snapback V-reclaim buy | OFF (`SNAP_CAP=0`) |
| MOMENTUM (long) | breakout/ignition/retest on top-RS movers | OFF — structurally broken, §8 |
| TREND (long) | daily Donchian, BTC>200d gated | OFF (idle in bear) |

**Current mode = FADE-ONLY, 4 short slots** (20-07: Artem's conscious choice; fewer slots = +25% per-trade sizing).

### The fade thesis in one line
A coin pumps → the pump fails → we sell into the retest of the failed level → it rejects again → we take +0.5%.

---

## 2. CURRENT LIVE STATE (as of 20-07-2026 evening)

```
host        root@167.233.55.14   (Hetzner VPS)
service     systemd unit `gladiator`   workdir /root/gladiator
equity      ~$74 USDT
code md5    2fd3af2354803a95434e8c668c080419
GitHub      https://github.com/artemmeleshko-trainar/Gladiator   (main, commit 3b40807)
tests       174/174 passing
era         4-slot era since 20-07 09:37 (counter reset then; ORPHAN-FIX restart 17:50 kept state)
```

### The exact live fade config (systemd drop-in `fadeonly.conf`)
```
slots   4 (TOTAL_SLOTS=4 + S_FADE_CAP=4)  ← TOTAL_SLOTS is ALSO the sizing divisor
size    margin = equity/4 × 3x ≈ $55 notional per fade (+25% vs the 5-slot era)
entry   maker limit sell at the failed level (GTC, above market)   → MAKER
TP      -0.5%  (S_TP_FIXED=0.005)  post-only maker limit buy        → MAKER
SL      +0.5%  (S_SL_FIXED=0.005)  native STOP_MARKET               → TAKER (unavoidable)
filter  0 <= c7d <= 50 (S_COIN7D_MIN=0 / S_COIN7D_CAP=50) · pump ≤2.5% (S_PUMP_MAX)
        per-coin re-fade cooldown 2h · 3 consecutive stops → 1h pause (S_LOSS_STREAK_N=3)
        2 stops/45min → pause ALL fades 1h (S_BOOK_BREAKER_N=2/S_BOOK_WINDOW_SEC=2700)
        + on that trip de-risk OPEN fades to breakeven (S_BOOK_BE_TIGHTEN=1)
scan    1h + 15m (S_SCAN_TFS)
```
**⚠️ 4 slots is a LEVERAGE choice, not an edge improvement** (2 agent studies, 192 fills: the 5th slot never
hosted an era fill → same trades, +25% size, +25% drawdown).

---

## 3. ZIP MANIFEST

```
gladiator-bot/
├── DEPLOY.md                  ← this manual
├── gladiator_bot.py           ← the bot (md5 §2 = exact live copy)
├── super_scanner.py           ← Binance REST helpers. Required.
├── test_gladiator_bot.py      ← 174 tests. Run before ANY deploy.
├── secrets/binance.env        ← 🔴 LIVE KEYS (ZIP only). DELETE BEFORE SHARING.
└── systemd/  gladiator.service (🔴 has COINALYZE_KEY) · fadeonly.conf (THE config) · caps/snap/trail.conf (legacy)
```
**Dependencies: NONE beyond Python 3 stdlib.** (VPS runs Python 3.14.)

---

## 4. DEPLOY FROM ZERO (new server)

```bash
ssh root@<NEW_SERVER>
mkdir -p /root/gladiator/data/logs /root/v2bot/data/secrets
scp gladiator-bot/{gladiator_bot.py,super_scanner.py,test_gladiator_bot.py} root@<NEW_SERVER>:/root/gladiator/
scp gladiator-bot/secrets/binance.env root@<NEW_SERVER>:/root/v2bot/data/secrets/binance.env
ssh root@<NEW_SERVER> 'chmod 600 /root/v2bot/data/secrets/binance.env'
scp gladiator-bot/systemd/gladiator.service root@<NEW_SERVER>:/etc/systemd/system/
ssh root@<NEW_SERVER> 'mkdir -p /etc/systemd/system/gladiator.service.d'
scp gladiator-bot/systemd/*.conf root@<NEW_SERVER>:/etc/systemd/system/gladiator.service.d/
# TESTS FIRST — must print "174 passed, 0 failed":
ssh root@<NEW_SERVER> 'cd /root/gladiator && COMBO_DATA_DIR=/tmp/t python3 test_gladiator_bot.py | tail -1'
ssh root@<NEW_SERVER> 'systemctl daemon-reload && systemctl enable --now gladiator'
# VERIFY: is-active=active · banner says "4 slots (…fade≤4…)" · heartbeat timestamp ADVANCES · md5 matches §2
```
**⚠️ Binance keys are IP-whitelisted → add the new server's IP in Binance API settings or all signed calls -2015.**

---

## 5. SECRETS

`secrets/binance.env` → `/root/v2bot/data/secrets/binance.env` (legacy path, NOT /root/gladiator/):
`BINANCE_KEY=` / `BINANCE_SECRET=`.
Fresh keys: Binance → API Management → ✅ Enable Futures · ❌ NO Withdrawals · ✅ IP-restrict. Keep the **BNB
fee discount ON** (the economics assume it). `COINALYZE_KEY` (in gladiator.service) = liquidation API, currently
inert (`S_LIQ_MIN=0`); free key at coinalyze.net or drop the line.

---

## 6. THE ECONOMICS — read before "improving" anything

maker 0.020%→**0.018%** w/BNB (entry+TP) · taker 0.050%→**0.045%** (SL only) · winner RT ~0.036% ≈ **7% of a
+0.5% win** · funding ≈ $0 (fades last minutes, never span 8h stamps).
- **Fees are PROPORTIONAL** — capital does NOT reduce the drag (only VIP ~$15M/mo would; we do ~$78k).
- **Fees are paid in BNB** → invisible in USDT balance → **USDT equity OVERSTATES net. Keep BNB topped up.**
- **TP 0.5% is settled:** +0.7%/+1.0% lose (move is ~0.5R); TP0.4 variants = indistinguishable-to-worse
  (20-07, 192 fills, 2 agents). c7d ceiling 30 = REFUTED (in-sample artifact). Don't re-tune.

---

## 7. THE DISCIPLINE (violating these has cost real money)

1. **Never deploy without «так»**; every change env-gated + reversible + tests green ON the VPS first.
2. **Validate ≥4 windows + max-DD.** 3. **"Beautifully conclusive" = red flag for a bug.**
4. **Audit sims adversarially BEFORE reading the number** (two false verdicts on 17-07 were the analyst's own bugs).
5. **Backtest bugs are not sign-neutral** (look-ahead/censoring flatter). 6. **"Refuted" ≠ "measured."**
7. **A hypothesis born from a sample cannot be confirmed by that sample.** 8. **Judge risk features by DRAWDOWN.**
9. **The bot's state is NOT the source of truth for positions — the EXCHANGE is** (see ORPHAN incident, §8):
   cross-check `positionRisk` against the state when anything looks odd.

### Honest state of the edge
Thin, NOT yet proven. Pre-reset era: 48W/33L, +$1.18/4d — positive, not statistically distinguishable from
zero. Forward-collect to n≈400 (checkpoint agenda in `~/Desktop/TrainAR/grid-bot/HANDOVER.md`). **Do not call
the bot proven profitable.**

---

## 8. KNOWN BUGS / SETTLED QUESTIONS (do NOT re-litigate without new data)

- **ORPHAN-FIX (20-07, FIXED in 3b40807):** a PARTIALLY_FILLED entry limit left its unfilled remainder resting;
  after the tracked part closed, the remainder could fill later = an untracked position with NO TP/SL (live
  incident: ONDO 92.8 + MET 314; Artem spotted −1% adverse with no stop and flattened by hand). Fix: on partial
  fill, cancel the remainder immediately + race-safe re-query (fade + dip legs). Momentum break/ignite = IOC =
  immune; momentum-retest + snap legs are OFF and still carry the old pattern — fix before ever enabling them.
- **Momentum leg: never measured, structurally mute** (forming-bar bug + brk=ref instant-exit; all prior numbers
  were proxy artifacts). Stays OFF.
- **c7d ceiling 30: REFUTED** (quasi-OOS the 30-50 band was the BEST band). **TP0.4 variants: REFUTED.**
- **Book-breaker FIX5: unresolvable on current data** — kept; can't see slow-drip clusters (~1 stop/hr). n≈400.
- **Research tooling:** never FIFO-match ARM→FILL; never trust reconstructed slot occupancy without restart
  force-cancels (use the log's printed `n/X`); banner "FADE R3.0 trail@…" text is stale cosmetics.

---

## 9. ROLLBACK CHEAT-SHEET

Edit `/etc/systemd/system/gladiator.service.d/fadeonly.conf` → `systemctl daemon-reload && systemctl restart gladiator`.

| revert | set |
|---|---|
| 5 slots (−25% sizing) | `TOTAL_SLOTS=5` + `S_FADE_CAP=5` |
| c7d floor off / SL 0.9% | `S_COIN7D_MIN=-1e9` / `S_SL_FIXED=0.009` |
| breaker / BE-tighten off | `S_BOOK_BREAKER_N=0` / `S_BOOK_BE_TIGHTEN=0` |
| pre-fade-only everything | `rm fadeonly.conf` + daemon-reload + restart |
| ORPHAN-FIX code rollback | VPS bak `gladiator_bot.py.bak-pre-orphanfix-*` (or any prior bak) |

Full stop: `systemctl disable --now gladiator` (native TP/SL orders stay on the exchange; the trail stops being managed).

---

## 10. USEFUL COMMANDS (run ON the VPS)

```bash
# stats one-liner
python3 -c 'import json,time;d=json.load(open("/root/gladiator/data/combo_state.json"));n=time.time();w,l=d["wins"],d["losses"];t=w+l;fp,bp=d.get("fade_paused_until",0),d.get("book_paused_until",0);print("eq $%.2f | %dW/%dL WR %.0f%% | realized $%+.2f"%(d["equity"],w,l,100*w/t if t else 0,d["realized"]));print("open:",[(p["coin"],p["state"]) for p in d["short"]] or "none");print("pause: streak",int((fp-n)/60) if fp>n else 0,"m | book",int((bp-n)/60) if bp>n else 0,"m")'
cat /root/gladiator/data/logs/combo_status.txt          # heartbeat — timestamp MUST advance
journalctl -u gladiator -f -o cat | grep -E "S-ARM|S-FILL|SHORT-CLOSE|S-BOOK|S-STREAK"   # live feed
# pause new entries only:  printf '[Service]\nEnvironment=S_FADE_CAP=0\n' > /etc/systemd/system/gladiator.service.d/pause.conf && systemctl daemon-reload && systemctl restart gladiator   (unpause: rm + reload + restart)
# ORPHAN check (state vs exchange — do this whenever something looks odd): positions on the exchange not present in combo_state.json = orphans
```

### Log decoder
`S-ARM … | n/4 (L0/Sk)` limit placed (k = shorts incl PENDING) · `S-FILL` short open · `SHORT-CLOSE (TP +0.4%)`
win · `(SL -0.5%)` real stop · `(SL +0.4%)` ⚠️ POSITIVE pct = mislabeled WIN · `S-RUNAWAY/S-EXPIRE` pending
cancelled (~⅓ of arms, normal) · `S-BOOK-PAUSE/S-BOOK-BE` squeeze guard.

---

## 11. WHERE EVERYTHING ELSE LIVES

Canonical resume: `~/Desktop/TrainAR/grid-bot/HANDOVER.md` → `▶▶ CURRENT STATE` (**read before any work**) ·
lessons `grid-bot/LESSONS.md` · repo `~/Desktop/TrainAR/gladiator-bot/` · GitHub
https://github.com/artemmeleshko-trainar/Gladiator · studies `grid-bot/{breaker_window_study,mom_clean,checkpoint_study}/`.

**The ZIP alone fully redeploys the bot** — stdlib-only .py files + §4 procedure.

---

*Updated 20-07-2026 evening. md5 `2fd3af2354803a95434e8c668c080419` · GitHub `3b40807` · 174/174 tests · 4-slot era.*
