# 🏖️ Vacation mode — new rooms on your phone, no laptop

Going away? GitHub Actions runs the daily hunt in the cloud (free) and a
Telegram bot sends each new room to your phone as a tappable card — photo
preview included. Tap → the SpareRoom page opens; message the advertiser from
the beach.

```
GitHub Actions (cron, 2×/day)
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

That's it. It runs **twice a day** (~07:17 and ~17:17 London time). For a
fresh hunt on demand: **GitHub mobile app → your repo → Actions → Vacation
hunt → Run workflow**.

## What arrives on the phone

- A short digest header (scanned / passed / already seen — and a ⚠️ warning if
  TfL rate limits made the shortlist partial).
- One message per new room: title, £ pcm, door-to-door minutes, area,
  move-in date, and the link (Telegram renders SpareRoom's photo card).
- If a run **fails**, you get a ⚠️ Telegram alert with a link to the logs —
  silence always means "nothing new", never "it broke".

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
- **Changed budget/commute/postcode?** Edit `config.yaml` locally (or
  `flatfinder setup`), then re-run `setup_vacation.ps1` to re-upload it.
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
