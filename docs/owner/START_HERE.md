# Start here: what your bot does, in plain English

*Written 2026-09-25. No coding or trading knowledge needed. Every rule in `TRADING_PLAN.md` still applies. This page explains it more simply and lists what changed today.*

## 1. The honest answer first

You asked for a bot that makes profitable trades every day or week.

**Nobody can honestly promise that, and I won't.** Any person, bot or product that promises daily trading profits is either guessing or selling something. What I can do is build a machine that only risks money when there is measured evidence it will pay, and that tells you the truth every day.

**What your bot's own records say (not my opinion; measured on your data):**

- **The trading strategy** (buying and selling to bet on price direction) has **no proven edge.** Over about 2,500 closed paper trades it lost a little on average after fees. In recent months the system tested more than 2,400 trading ideas; none survived honest testing (`.claude/skills/refuted-families-ledger/SKILL.md`).
- **The funding-carry strategy** is the one approach with real evidence behind it. It holds a coin and an equal bet against it, so price moves cancel out, and it collects a small fee that traders pay each other every 8 hours. **It only trades when that fee is unusually high** (around 100% a year). Today the fee is about 6% a year, so it waits. Waiting is correct: trading now would cost more in fees than it earns. When the market gets heated again, it will trade by itself.
- **Right now the best thing idle cash can do is earn interest.** Stablecoin savings rates are about 3.5–4% a year. That is less exciting than trading, but it is positive and it beats both strategies in today's market.

## 2. What changed today

1. **Interest on idle cash (paper).** Every hour the bot now counts the interest your idle paper cash *would* earn at a public stablecoin savings rate (the USDC rate on Aave, a large lending service). It is kept in its own ledger and is clearly labelled *not trading profit*. If the rate can't be fetched, it counts **zero**. It never makes money up.
2. **A daily scoreboard in plain English.** Every day at 23:55 UTC the bot writes one page and emails it to you. The page answers three questions: Is the bot running? How much did each part make today and this week? If a part didn't trade, why not?
3. **A new menu option.** Open `TradingBot.bat` and press **S** to see the scoreboard at any time.

Nothing about the trading rules, the safety limits or the carry strategy's entry rule was loosened.

## 3. What you need to do

**Step 1: get today's changes onto your computer.** The changes are on the branch `claude/vibrant-hawking-kvigfa`. If you use Claude Code on your computer, tell it: *"Merge the branch claude/vibrant-hawking-kvigfa into my bot and restart it."*

**Step 2 (recommended): stop the losing trading strategy from placing paper trades.**
1. In the bot folder, open the file named `.env` with Notepad.
2. Find the line `ENTRY_POLICY=APPROVED_PAPER` and change it to `ENTRY_POLICY=SHADOW_ONLY`.
3. Save the file and **restart your computer**. A restart is the simplest way to make sure the bot reads the new setting; closing only one window is not enough.

Why: the trading strategy has already been measured enough. More paper trades from it mostly add small losses, not new information. In SHADOW_ONLY it still *records* what it would have done, so nothing is lost. To undo, change the line back.

**Step 3: read the scoreboard once a day** (about 2 minutes). You don't need to act on it unless it says the bot is **not running**, or that the carry strategy is **LOCKED**.

## 4. What the bot needs to run 24/7

The bot runs **on your computer**, not in the cloud session where I work (that session shuts down when idle). Your computer should stay on and connected to the internet. The bot starts automatically when Windows starts (task `TradingBot-24x7`).

If you ever want it to run without your computer, it would need a small always-on server. That is optional, costs money monthly, and is not required for paper trading.

## 5. When would real money be used?

Real money moves **only** when all three conditions in `TRADING_PLAN.md` §2 are met:
1. The carry strategy completes enough profitable paper cycles.
2. The safety preconditions are done.
3. **You** sign `docs/CONTROLLED_LIVE_CHECKLIST.md`.

Being honest about timing: in today's market this is **not close**, because the carry strategy rarely gets a chance to trade.

Putting idle cash into a real savings product is a **separate decision** with its own risk. If the exchange or platform holding the money fails, the money can be lost (the FTX collapse in 2022 is the example). Make that decision deliberately, with a small amount first. The bot does not do it for you.

## 6. Things that would make results worse (please don't)

- Raising leverage, loosening stop-losses, or turning safety gates off to "get more trades".
- Switching strategies after a bad week. A flat or slightly negative week is normal.
- Paying for signals, "AI bots" or groups that promise daily profits.
