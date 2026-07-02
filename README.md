# Phillies Daily

A fully autonomous daily Phillies briefing. Every day at noon ET, GitHub Actions rebuilds a live website with today's game info, batter-vs-pitcher matchups, ballpark weather, roster moves, NL East standings, and MLBTradeRumors headlines, then texts you the link via Twilio.

Total cost: $0/month for hosting and data. Twilio is roughly $1/month for the phone number plus less than a penny per text (about $1.25/month all-in).

## What runs every day

1. `build_update.py` pulls live data from the MLB Stats API (free, no key), Open-Meteo (free, no key), and the MLBTradeRumors Phillies RSS feed
2. It renders `index.html` and commits it, which updates your GitHub Pages site
3. `send_sms.py` texts you: "Hey Brandon, here is your daily Phillies update. [link]"

The schedule is DST-proof: the workflow fires at both 16:00 and 17:00 UTC and a guard step only proceeds when it is actually 12 PM in New York.

## One-time setup (about 20 minutes)

### 1. Create the GitHub repo

Create a new **public** repo (public repos get unlimited free Actions minutes and free Pages). Push this folder to it:

```bash
cd phillies-daily
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/phillies-daily.git
git push -u origin main
```

### 2. Enable GitHub Pages

Repo Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder `/ (root)` → Save.

Your site will be live at `https://YOUR_USERNAME.github.io/phillies-daily/` within a couple of minutes.

### 3. Set up Twilio

1. Create an account at twilio.com and buy a phone number with SMS capability (~$1.15/month)
2. Note your Account SID and Auth Token from the Twilio Console dashboard
3. If you are on a free trial, verify your personal cell number in Console → Phone Numbers → Verified Caller IDs (trial accounts can only text verified numbers). Upgrading with a few dollars of credit removes this restriction and the "Sent from your Twilio trial account" prefix.
4. US numbers require A2P 10DLC registration for reliable delivery. For a single personal-use number, register the free "Sole Proprietor" brand in Console → Messaging → Regulatory Compliance. It takes a few minutes and avoids carrier filtering.

### 4. Add the five secrets

Repo Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | From Twilio Console |
| `TWILIO_AUTH_TOKEN` | From Twilio Console |
| `TWILIO_FROM_NUMBER` | Your Twilio number, e.g. `+12155551234` |
| `MY_PHONE_NUMBER` | Your cell, e.g. `+13055551234` |
| `SITE_URL` | `https://YOUR_USERNAME.github.io/phillies-daily/` |
| `ODDS_API_KEY` | Optional: from the-odds-api.com free tier, enables betting lines |
| `ANTHROPIC_API_KEY` | Optional: from console.anthropic.com, upgrades the recap to AI-written |

### 5. Test it

Repo → Actions → "Phillies Daily Update" → "Run workflow". You should get the text within about a minute, and the link should show today's page.

That's it. It now runs itself every day at noon ET.

## Notes and tweaks

- **Change the send time:** edit the guard hour in `.github/workflows/daily.yml` (`"12"`) and the two cron lines to match.
- **Doubleheaders** are handled: the page shows a card per game.
- **Off days** show a clean "no game today" card with standings and rumors still populated.
- **Resilience:** every data section is wrapped so one API hiccup never kills the page or the text.
- **GitHub Actions cron drift:** scheduled runs can start a few minutes late during high-load periods (typically 5 to 15 minutes). If exact-noon delivery matters, an alternative is a free cron ping service like cron-job.org hitting a `repository_dispatch` webhook.
- The `hot` styling flags Phillies batters with a 1.000+ OPS over the last 7 days.

## Files

- `build_update.py` — fetches all data, renders the page
- `template.html` — Phillies-branded page template (pinstripes, red #E81828, blue #002D72)
- `send_sms.py` — Twilio text
- `.github/workflows/daily.yml` — the noon ET scheduler
- `requirements.txt` — Python dependencies
