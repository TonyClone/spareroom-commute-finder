# 🏖️ Vacation mode — new rooms on your phone, no laptop

Going away? GitHub Actions runs the daily hunt in the cloud (free) and a
Telegram bot sends each new room to your phone as a tappable card — photo
preview included. Tap → the SpareRoom page opens; message the advertiser from
the beach.

```
GitHub Actions (cron, every 2h during waking hours)
   → flatfinder daily --no-open --notify
   → Telegram bot → your phone (one tappable message per room)
   → seen-DB persisted encrypted on a `runner-state` branch (never re-sent)
```

**Cost: £0.** Actions is free on public repos, Telegram bots are free, and the
seen-database lives in the repo itself (encrypted) — no server, nothing to
babysit.

## Setup (~10 minutes, do it before you leave)

1. **Merge this feature to your default branch** (the workflow must exist on
   `main` for scheduled + phone-triggered runs).
2. **Create a Telegram bot** (2 min, on your phone or desktop): message
   [@BotFather](https://t.me/BotFather) → `/newbot` → pick any name → copy the
   token it gives you.
3. **Run the setup script** from your repo folder on the laptop:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_vacation.ps1
   ```

   It uploads your `config.yaml` and keys as **repo secrets** (never
   committed), generates the state-encryption key, auto-detects your Telegram
   chat id from a test DM, and kicks off a test run. Needs the
   [GitHub CLI](https://cli.github.com/) (`gh auth login` once).
4. **Watch your phone.** The first run baselines (marks everything currently
   live as seen) and says "no new rooms — you're caught up". Every run after
   that sends only rooms posted since.

That's it. It runs **every 2 hours from ~07:23 to ~21:23 London time**
(nothing overnight — the morning run sweeps up late posts). Each incremental
run is cheap and polite: it stops scraping at the first already-seen page.
For a fresh hunt on demand: **GitHub mobile app → your repo → Actions →
Vacation hunt → Run workflow**.

## What arrives on the phone

- A short digest header (scanned / passed / already seen — and a ⚠️ warning if
  TfL rate limits made the shortlist partial). "No new rooms" digests arrive
  **silently** (no buzz), so frequent runs never feel like spam but you can
  always glance at the chat to confirm it's alive.
- One message per new room: title, £ pcm, door-to-door minutes, area,
  move-in date, and the link (Telegram renders SpareRoom's photo card).
- If a run **fails**, you get a ⚠️ Telegram alert with a link to the logs —
  silence always means "nothing new", never "it broke".

## Change settings from the chat

The bot chat doubles as a **settings console** — no laptop needed. Send
**`/menu`** and you get a tappable **menu card**: every button toggles a
filter or steps a number (budget −100/−50/+50/+100, commute ±5/±10, move-in
±1 week, …). Typed commands still work too.

Because the bot only wakes when a run starts, **taps and commands queue until
the next run (~2h)** — it then applies them, answers each tap, refreshes the
menu card in place, and hunts with the new settings. Impatient? Trigger a run
now from the GitHub app. Changes persist across runs (they live in the
encrypted state DB) and sit **on top of** `config.yaml` until you unset them.

```
/menu                         tappable settings card
/settings                     show current values (★ = set from chat)
/set budget.max_pcm 1400      change any settable key
/unset budget.max_pcm         back to the config.yaml value
/unset all                    clear every chat override
/help                         all commands + keys
```

Shortcuts for the common ones:

```
/budget 1400        max £/month          /livingroom on|off   drop no-lounge flats
/commute 35         max minutes          /shortterm on|off    drop short-term-only sublets
/movein 2026-09-01  ideal move-in        /tabs 10             rooms per run
/double on          doubles only         /bills on            bills included only
```

**Settings are per user, not global.** `TELEGRAM_CHAT_ID` can hold several
comma-separated chat ids (e.g. `"111,222"` — add your partner's). Every listed
chat gets its **own settings and its own filtered shortlist** each run: one
person can cap at £1,200 with the lounge filter on while the other hunts at
£1,700 without it. The scrape itself runs once on the most permissive union of
everyone's settings, then each chat's list is filtered from it. The **first**
id is the admin: only that chat can change the shared hunt settings (arrive-by
time, search size, the AI filter) — the menu marks those rows.

Security: **only listed chats can see or change anything.** Messages from any
other chat are ignored (consumed silently, never applied, never answered), and
one user's settings never touch another's. Sensitive knobs — office address,
proxy, scraper politeness — are deliberately not settable from chat at all.

## How it stays private on a public repo

| Data | Where it lives |
|---|---|
| Office postcode, budget (`config.yaml`) | Actions **secret**, and its values are masked (`***`) in the public run logs |
| TfL / DeepSeek / proxy keys | Actions secrets (auto-masked) |
| Seen-rooms database | `runner-state` branch, **AES-256 encrypted** with the `STATE_KEY` secret, single force-pushed commit (no history) |
| Telegram token / chat id | Actions secrets (auto-masked) |

## Troubleshooting

- **Run fails with HTTP 403 from SpareRoom** — SpareRoom may be blocking
  GitHub's datacenter IPs. Fix: set `scraper.use_proxy: true` in your local
  `config.yaml`, add a `PROXY_URL` line to your local `.env` (any cheap
  residential proxy), and re-run `setup_vacation.ps1` to refresh the secrets.
- **TfL "shortlist may be partial" warnings** — add a free TfL key locally
  (menu → *TfL key*) and re-run the setup script so CI uses it too.
- **Changed budget/commute?** Just message the bot (see *Change settings from
  the chat* above). For the office postcode or anything not settable from
  chat: edit `config.yaml` locally (or `flatfinder setup`), then re-run
  `setup_vacation.ps1` to re-upload it.
- **Duplicate room messages after a failed run** — expected: rooms are only
  marked seen *after* Telegram delivery succeeds, so a mid-send failure
  re-sends rather than loses. Duplicates are the safe direction.
- **Back home?** Just stop the schedule: repo → Actions → Vacation hunt →
  "···" → *Disable workflow*. Local `flatfinder daily` keeps working as
  always — but note the cloud seen-DB and your laptop's are separate; rooms
  the bot already sent you will open again locally once, then be marked seen.

## Notes

- Scheduled workflows pause automatically after 60 days without repo
  activity — fine for a holiday; push any commit to wake them.
- Log output on public repos is public: personal values are masked, but the
  run summary (counts, listing titles in tables) is visible. If that bothers
  you, run the workflow from a private fork instead.
- Local use works too: `flatfinder daily --notify` sends to your phone *and*
  opens tabs (add `--no-open` to skip the tabs) once `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` are in your `.env`.
