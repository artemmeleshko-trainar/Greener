# GREENER BOT — COMPLETE RECOVERY & OPERATING MANUAL
### For Artem, and for any AI model asked to work on this bot. Read top to bottom BEFORE touching anything.
*(Master copy in the repo. The ZIP build adds `secrets/` with live keys pulled from the VPS.)*

---

## 🔴🔴 STOP — READ FIRST

1. **The ZIP build contains LIVE TRADING KEYS** (`secrets/binance.env`; `systemd/greener.service` carries
   `COINALYZE_KEY` when built from the VPS). **Delete `secrets/` + redact the key before sharing with ANY AI/person.**
2. **NEVER deploy/commit/push/scp/restart without Artem's explicit «так».** Propose → wait → act.
3. **REAL MONEY** (~$74, Binance USDT-M, 3x). Greener and Gladiator share ONE account — **never run both**
   (pause one before starting the other: `systemctl disable --now gladiator` ⟷ `... greener`).

---

## 1. WHAT GREENER IS

A 2-LONG / 2-SHORT, 4-slot evolution of Gladiator (it IS the live gladiator codebase + three additions),
born 21-07-2026 from the WR70 research program:

| slot | engine | thesis | evidence |
|---|---|---|---|
| S1 | **FADE + liq-at-fill gate** | short a busted pump, but ONLY while trapped longs are actively liquidating (trailing-30min long-liq ≥ $1k at/into the fill) + **TP 0.5% / SL 1.5%** | WR70 study candidate #1: **84.4% WR, +0.136%/tr** after fees+measured slippage, n=64, survived in-era OOS/concentration/jackknife. Grade B — forward-validating live |
| S2 | **E5 SQUEEZE-FADE** (from clever-bot) | taker sell into a SHORT-liq flush spike; exits = **TRAIL** (arm on first +0.3% green; width 0.3% first 10min → 0.75% after; **NATIVE STOP_MARKET rests at the trail level** — FIX-A, ratcheted ≥5bp) + TIME 60m + +4% catastrophe stop | trail tuned 22-07 on the 12 real night-1 paths (Artem's call: "bank the early green, monsters be damned"); FIX-A killed the 2s-poll overshoot (0.08-0.31% on every armed exit). WR ~48%→up with trail; judge on NET expectancy |
| L1 | **SNAP** (liq-snapback) | buy the V-reclaim after a long-liq flush ≥$400k on liquid top-40 | PF 1.56 one window; the tape that stops the fades FILLS the snaps = squeeze hedge |
| L2 | **DIP** (ballast) | maker −1.2% dip-buy behind the full gate stack, tight trail | ~breakeven engine; exists to flatten book beta, not to earn |

Book beta ≈ 0 by construction (2S+2L). Trend leg (BTC>200d Donchian) stays OFF until the regime turns.

### Key mechanics added vs Gladiator (all env-gated, defaults inert)
- `GR_FILL_LIQ_MIN` — S1 arm requires liq30 ≥ floor (**FAIL-CLOSED** on missing data); a PENDING retest is
  **cancelled the minute the catalyst dies** (`S-LIQDROP`) → fills only happen while the catalyst is alive.
- `SQF_*` — E5 engine; pump-check runs FIRST so Coinalyze sees ~0-10 calls/min (free tier ~40/min).
- Squeeze SLs feed the **short-side breaker** (streak + book-wide + BE-tighten pauses block BOTH short engines).
- **Cross-leg same-coin exclusion** (`held_all`) — one symbol lives in ONE leg at a time.
- Snap **orphan-fix** (cancel entry remainder on fill, qty from exchange amt) — the 20-07 incident class is dead
  in fade/dip/snap; momentum stays off (own unfixed bugs).

## 2. CURRENT LIVE STATE — see the repo/HANDOVER for the running values
```
host      root@167.233.55.14 · systemd unit `greener` · workdir /root/greener
GitHub    https://github.com/artemmeleshko-trainar/Greener
tests     188/188 (run: COMBO_DATA_DIR=/tmp/t python3 test_greener_bot.py)
config    systemd/greener.conf (THE config — 4 slots, caps 1/1/1/1, TP0.5/SL1.5, GR_FILL_LIQ_MIN=1000)
```

## 3. DEPLOY FROM ZERO
```bash
ssh root@<SERVER>; mkdir -p /root/greener/data/logs /root/v2bot/data/secrets
scp greener-bot/{greener_bot.py,super_scanner.py,test_greener_bot.py} root@<SERVER>:/root/greener/
scp gladiator-bot/secrets/binance.env root@<SERVER>:/root/v2bot/data/secrets/   # same account keys
scp greener-bot/systemd/greener.service root@<SERVER>:/etc/systemd/system/      # set COINALYZE_KEY inside!
mkdir /etc/systemd/system/greener.service.d && scp greener-bot/systemd/greener.conf root@<SERVER>:/etc/systemd/system/greener.service.d/
ssh root@<SERVER> 'cd /root/greener && COMBO_DATA_DIR=/tmp/t python3 test_greener_bot.py | tail -1'  # MUST be 188 passed
ssh root@<SERVER> 'systemctl disable --now gladiator'        # ONE account — pause the other bot FIRST
ssh root@<SERVER> 'systemctl daemon-reload && systemctl enable --now greener'
# VERIFY: active · banner "GREENER BOT … 4 slots … S1-LIQGATE@fill≥$1,000 … E5-SQF ON" · heartbeat advances ·
#         positionRisk vs state = no orphans · md5 matches the repo
```
Keys live at `/root/v2bot/data/secrets/binance.env` (legacy path). Binance keys are IP-whitelisted.

## 4. ECONOMICS & DISCIPLINE (inherited — the short version)
- Fees: maker 0.018% / taker 0.045% (BNB ON — **keep BNB topped up**, it runs out in days and fees hide there).
  S1 winner RT 0.036%; E5 RT 0.09% (taker both ways) — E5's edge budget already includes it.
- **S1's SL 1.5% is wide**: one stop ≈ 3 wins. The gate's job is to make stops rare (studied 84% WR). If live
  WR falls toward its 78% breakeven → the candidate failed forward validation; revert to the old exits
  (`S_SL_FIXED=0.005`, `GR_FILL_LIQ_MIN=0`) and say so plainly.
- All Gladiator discipline applies: «так» before any deploy · ≥4-window validation · audit-before-numbers ·
  "beautifully conclusive = bug" · state ≠ truth for positions (cross-check positionRisk) · WR alone is NOT the
  objective — expectancy is.

## 5. ROLLBACKS
| what | how |
|---|---|
| Greener → Gladiator (full revert) | `systemctl disable --now greener && systemctl enable --now gladiator` |
| S1 gate off (plain fade) | `GR_FILL_LIQ_MIN=0` in greener.conf → reload → restart |
| S1 exits back to symmetric | `S_SL_FIXED=0.005` |
| E5 off / snap off / dip off | `SQF_ENABLED=0` / `SNAP_CAP=0` / `M_DIP_CAP=0` |
| E5 trail off (revert to pure TIME60+SL4) | `SQF_TRAIL_ACT=0` |
| E5 native trail stop off (software poll only) | `SQF_TRAIL_NATIVE=0` |

## 6. COMMANDS (on the VPS)
```bash
cat /root/greener/data/logs/combo_status.txt     # heartbeat (timestamp MUST advance); shows L/S/SQ/M + W/L
journalctl -u greener -f -o cat | grep -E "S-ARM|S-FILL|SHORT-CLOSE|SQF|A-FILL|L-ARM|S-LIQDROP|S-BOOK"
python3 -c 'import json;d=json.load(open("/root/greener/data/combo_state.json"));print(d["wins"],"W/",d["losses"],"L", round(d["realized"],2), [(p["coin"],p["state"]) for k in ("short","squeeze","snap","long") for p in d.get(k,[])])'
```
Log decoder additions vs Gladiator: `SQF-ARM/FILL/CLOSE` = E5 · `S-LIQDROP` = catalyst died, retest cancelled ·
`S-LIQGATE` = no liq data, fail-closed skip · `SQUEEZE-CLOSE (TIME …)` = E5 time exit.

*Born 21-07-2026 from the WR70 research program (3 agents). Gladiator remains installed+paused as the fallback.*
