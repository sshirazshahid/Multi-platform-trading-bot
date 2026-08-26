# AGENTS.md

Durable operating notes for agents working in this repo. For architecture,
module map, and the full command reference, see `CLAUDE.md` (authoritative) and
`README.md`. This file only records non-obvious, environment-level context.

## Cursor Cloud specific instructions

The startup update script already refreshes all dependencies (system Python via
`pip`). The notes below are the non-obvious gotchas discovered while setting up
this environment; standard run/test/lint commands live in `CLAUDE.md`.

### Interpreter & PATH
- Use `python3` (there is no `python` shim on the cloud VM). `CLAUDE.md` shows
  commands as `python …`; substitute `python3`.
- Dependencies install to the user site; console scripts land in
  `~/.local/bin`. Prefer module form (`python3 -m pytest`, `python3 -m ruff`)
  or ensure `export PATH="$HOME/.local/bin:$PATH"` so `ruff`/`codespell`/
  `bandit`/`detect-secrets`/`pytest` resolve.

### Dependency gaps not in requirements.txt
- `pytz` — required at runtime. `main.py` schedules `schedule.every().day.at("00:00","UTC")`,
  and `schedule` lazily `import pytz` for any tz-aware job. Without it the bot
  boots fully then crashes the watchdog loop with `No module named 'pytz'`.
- `pyarrow` — required to read/write the parquet OHLCV cache; several
  backfill/cache modules and their tests call `pd.read_parquet`. Missing it
  fails those tests with "Unable to find a usable engine".
- Both are installed by the update script. If you re-pin `requirements.txt`,
  consider adding them there.

### Running the bot (PAPER is the default, self-contained mode)
- `cp .env.example .env` (already defaults to `OPERATING_MODE=PAPER`,
  `DRY_RUN=true`). Run with `python3 main.py`; `python3 main.py --status` prints
  a quick status table without starting the engine. Needs a writable `data/`
  (single-instance lock at `data/bot.lock`).
- With NO exchange API keys the three exchange clients self-skip
  (`_connected=False`), so account balance reads as `$0` and the bot opens no
  autonomous trades. It still runs the full loop: MCP scoring over ~46 coins
  using PUBLIC data (CoinGecko prices, news, technicals), plus the 7 log-only
  shadow probes. This is a valid "it runs" state, not a failure.
- Network reality from the cloud VM: Binance public API returns `451`
  (geo-restricted) and Bybit returns `403` (CloudFront); **Bitget public data
  works**. To exercise the autonomous entry → paper-fill path you need at least
  one exchange API key (read+trade), and it must be a venue reachable from here
  (Binance/Bybit are geo-blocked).
- To exercise the paper-trade core WITHOUT keys, drive
  `core.position_tracker.PositionTracker` / `Position` directly (open → close);
  closed trades persist to `data/positions.json` and show up in
  `python3 main.py --status`.

### Tests (see CLAUDE.md for the full matrix)
- Bot suite: `python3 -m pytest tests/ -q` (~3744 tests). On Linux expect
  ~8 pre-existing failures that are NOT environment breakage:
  - 4 process/PID "self-healing"/supervisor tests are Windows-shaped — CI runs
    the full bot suite on `windows-latest` on purpose (see `.github/workflows/ci.yml`).
  - 4 `test_s2_basket_slice` / `test_s3_vertical_slice` / `test_spot_rotation_slice`
    tests require pre-populated parquet OHLCV cache fixtures under
    `data/ohlcv_cache/` that are not in the repo.
- Skill tests are per-skill (module-name collisions): e.g.
  `python3 -m pytest .agents/skills/position-sizer/scripts/tests/`.

### Lint (mirrors the CI `lint` job)
- `ruff check skills/ scripts/ .agents/skills/{…}/scripts/` (see ci.yml for the
  exact `.agents/skills` list). The pristine tree currently has pre-existing
  `I001` import-sort findings in `skills/*` and 2 codespell "re-declared" hits
  in `scripts/gate_status.py`; these are not introduced by setup.

### `.env` changes need a process restart
- A running `main.py` (or `launcher_supervisor`) will not pick up `.env` edits
  live; the supervisor's inherited environ even vetoes them. Restart the
  process/supervisor after editing `.env`. (Also in `CLAUDE.md` gotchas.)
