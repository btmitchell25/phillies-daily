"""
Phillies Daily Update — page generator.

Pulls live data from the MLB Stats API, Open-Meteo, and the MLBTradeRumors
Phillies RSS feed, then renders a branded static page to index.html.

Run daily by GitHub Actions. No API keys required for data fetching.
"""

import os
import sys
import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from jinja2 import Template

API = "https://statsapi.mlb.com/api/v1"
PHILLIES_ID = 143
ET_TZ = ZoneInfo("America/New_York")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "phillies-daily/1.0"})


def get(url, params=None, timeout=20):
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def safe(fn, fallback):
    """Run a data-fetching section; never let one failure kill the page."""
    try:
        return fn()
    except Exception as e:
        print(f"  [warn] {fn.__name__} failed: {e}", file=sys.stderr)
        return fallback


# ---------------------------------------------------------------- game today
def fetch_game(today_str):
    data = get(f"{API}/schedule", params={
        "sportId": 1, "teamId": PHILLIES_ID, "date": today_str,
        "hydrate": "probablePitcher,venue(location)",
    })
    dates = data.get("dates", [])
    if not dates or not dates[0].get("games"):
        return None
    games = []
    for g in dates[0]["games"]:
        start = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")).astimezone(ET_TZ)
        home, away = g["teams"]["home"], g["teams"]["away"]
        venue = g.get("venue", {})
        coords = venue.get("location", {}).get("defaultCoordinates", {})
        games.append({
            "gamePk": g["gamePk"],
            "start_local": start.strftime("%-I:%M %p ET"),
            "start_dt": start,
            "venue": venue.get("name", ""),
            "city": venue.get("location", {}).get("city", ""),
            "lat": coords.get("latitude"),
            "lon": coords.get("longitude"),
            "is_home": home["team"]["id"] == PHILLIES_ID,
            "home": {"id": home["team"]["id"], "name": home["team"]["name"],
                     "record": f"{home['leagueRecord']['wins']}-{home['leagueRecord']['losses']}",
                     "pitcher": home.get("probablePitcher")},
            "away": {"id": away["team"]["id"], "name": away["team"]["name"],
                     "record": f"{away['leagueRecord']['wins']}-{away['leagueRecord']['losses']}",
                     "pitcher": away.get("probablePitcher")},
            "series_note": f"Game {g.get('seriesGameNumber', '?')} of {g.get('gamesInSeries', '?')}",
            "status": g.get("status", {}).get("detailedState", ""),
        })
    return games


def pitcher_season_line(pid):
    """Season pitching line for a probable starter."""
    data = get(f"{API}/people/{pid}/stats", params={
        "stats": "season", "group": "pitching", "season": datetime.now(ET_TZ).year})
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            s = split.get("stat", {})
            return {"w": s.get("wins", 0), "l": s.get("losses", 0),
                    "era": s.get("era", "-"), "ip": s.get("inningsPitched", "-"),
                    "so": s.get("strikeOuts", 0), "whip": s.get("whip", "-")}
    return None


# ------------------------------------------------------------ batter vs pitcher
def team_top_hitters(team_id, n=9):
    """Top n hitters on the active roster by season plate appearances."""
    year = datetime.now(ET_TZ).year
    roster = get(f"{API}/teams/{team_id}/roster", params={"rosterType": "active"})
    hitters = []
    for entry in roster.get("roster", []):
        if entry.get("position", {}).get("type") == "Pitcher":
            continue
        pid = entry["person"]["id"]
        name = entry["person"]["fullName"]
        try:
            data = get(f"{API}/people/{pid}/stats", params={
                "stats": "season", "group": "hitting", "season": year})
            for block in data.get("stats", []):
                for split in block.get("splits", []):
                    s = split.get("stat", {})
                    hitters.append({"id": pid, "name": name,
                                    "pa": int(s.get("plateAppearances", 0)),
                                    "avg": s.get("avg", "-"), "ops": s.get("ops", "-")})
        except Exception:
            continue
    hitters.sort(key=lambda h: -h["pa"])
    return hitters[:n]


def bvp(batter_id, pitcher_id):
    """Career batter-vs-pitcher line."""
    data = get(f"{API}/people/{batter_id}/stats", params={
        "stats": "vsPlayer", "group": "hitting", "opposingPlayerId": pitcher_id})
    for block in data.get("stats", []):
        if block.get("type", {}).get("displayName") == "vsPlayerTotal":
            for split in block.get("splits", []):
                s = split.get("stat", {})
                ab = int(s.get("atBats", 0))
                if ab == 0:
                    return None
                return {"ab": ab, "h": int(s.get("hits", 0)),
                        "hr": int(s.get("homeRuns", 0)), "avg": s.get("avg", "-"),
                        "ops": s.get("ops", "-")}
    return None


def last7(batter_id):
    """Hitting line over the last 7 days — hot/cold signal."""
    end = datetime.now(ET_TZ).date()
    start = end - timedelta(days=7)
    data = get(f"{API}/people/{batter_id}/stats", params={
        "stats": "byDateRange", "group": "hitting",
        "startDate": start.isoformat(), "endDate": end.isoformat()})
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            s = split.get("stat", {})
            if int(s.get("plateAppearances", 0)) > 0:
                return {"avg": s.get("avg", "-"), "ops": s.get("ops", "-"),
                        "hr": int(s.get("homeRuns", 0))}
    return None


def matchup_table(team_id, opposing_pitcher):
    """For one team's top hitters: career vs today's opposing starter + last 7 days."""
    if not opposing_pitcher:
        return []
    rows = []
    for h in team_top_hitters(team_id):
        row = {"name": h["name"], "season_avg": h["avg"], "season_ops": h["ops"],
               "bvp": safe(lambda: bvp(h["id"], opposing_pitcher["id"]), None),
               "l7": safe(lambda: last7(h["id"]), None)}
        rows.append(row)
    return rows


# ------------------------------------------------------------------- weather
def fetch_weather(lat, lon, game_dt):
    data = get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "America/New_York",
        "start_date": game_dt.date().isoformat(), "end_date": game_dt.date().isoformat()})
    hours = data.get("hourly", {})
    target = game_dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
    times = hours.get("time", [])
    if target not in times:
        return None
    i = times.index(target)
    codes = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
             45: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
             61: "Light rain", 63: "Rain", 65: "Heavy rain", 80: "Rain showers",
             81: "Rain showers", 82: "Heavy showers", 95: "Thunderstorms",
             96: "Thunderstorms", 99: "Severe thunderstorms"}
    return {"temp": round(hours["temperature_2m"][i]),
            "precip": hours["precipitation_probability"][i],
            "wind": round(hours["wind_speed_10m"][i]),
            "sky": codes.get(hours["weather_code"][i], "—")}


# -------------------------------------------------------------- transactions
def fetch_transactions():
    """Phillies transactions from yesterday through today."""
    today = datetime.now(ET_TZ).date()
    start = today - timedelta(days=1)
    data = get(f"{API}/transactions", params={
        "teamId": PHILLIES_ID, "startDate": start.isoformat(), "endDate": today.isoformat()})
    moves = []
    for t in data.get("transactions", []):
        moves.append({"date": t.get("date", ""), "desc": t.get("description", "")})
    return moves


# ----------------------------------------------------------------- standings
def fetch_standings():
    data = get(f"{API}/standings", params={
        "leagueId": 104, "season": datetime.now(ET_TZ).year, "standingsTypes": "regularSeason"})
    for rec in data.get("records", []):
        if rec.get("division", {}).get("id") == 204:  # NL East
            rows = []
            for tr in rec.get("teamRecords", []):
                rows.append({
                    "team": tr["team"]["name"],
                    "is_phi": tr["team"]["id"] == PHILLIES_ID,
                    "w": tr["wins"], "l": tr["losses"],
                    "pct": tr.get("winningPercentage", "-"),
                    "gb": tr.get("gamesBack", "-"),
                    "wc_gb": tr.get("wildCardGamesBack", "-"),
                    "streak": tr.get("streak", {}).get("streakCode", "-"),
                    "l10": next((f"{s['wins']}-{s['losses']}"
                                 for s in tr.get("records", {}).get("splitRecords", [])
                                 if s.get("type") == "lastTen"), "-"),
                })
            return rows
    return []


# --------------------------------------------------------- last night's game
def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def gather_last_game_facts():
    """Pull yesterday's final and the boxscore details that feed the recap."""
    yesterday = (datetime.now(ET_TZ).date() - timedelta(days=1)).isoformat()
    data = get(f"{API}/schedule", params={
        "sportId": 1, "teamId": PHILLIES_ID, "date": yesterday,
        "hydrate": "decisions,linescore"})
    dates = data.get("dates", [])
    if not dates or not dates[0].get("games"):
        return None
    finals = [g for g in dates[0]["games"]
              if g.get("status", {}).get("abstractGameState") == "Final"]
    if not finals:
        return None
    g = finals[-1]  # last game of a doubleheader if there were two
    home, away = g["teams"]["home"], g["teams"]["away"]
    is_home = home["team"]["id"] == PHILLIES_ID
    phi, opp = (home, away) if is_home else (away, home)
    won = phi.get("score", 0) > opp.get("score", 0)

    box = get(f"{API}/game/{g['gamePk']}/boxscore")
    phi_side = "home" if is_home else "away"
    players = box["teams"][phi_side]["players"]

    homers, multihit, starter = [], [], None
    for p in players.values():
        name = p["person"]["fullName"]
        bat = p.get("stats", {}).get("batting", {})
        if bat:
            hr = int(bat.get("homeRuns", 0) or 0)
            hits = int(bat.get("hits", 0) or 0)
            if hr:
                homers.append({"name": name, "hr": hr,
                               "rbi": int(bat.get("rbi", 0) or 0)})
            elif hits >= 2:
                multihit.append({"name": name, "h": hits,
                                 "ab": int(bat.get("atBats", 0) or 0)})
        pit = p.get("stats", {}).get("pitching", {})
        if pit and int(pit.get("gamesStarted", 0) or 0) == 1:
            starter = {"name": name, "ip": pit.get("inningsPitched", "0"),
                       "er": int(pit.get("earnedRuns", 0) or 0),
                       "k": int(pit.get("strikeOuts", 0) or 0),
                       "h": int(pit.get("hits", 0) or 0)}

    decisions = g.get("decisions", {})
    return {
        "won": won,
        "phi_score": phi.get("score", 0), "opp_score": opp.get("score", 0),
        "opp_name": opp["team"]["name"],
        "venue": g.get("venue", {}).get("name", ""),
        "is_home": is_home,
        "winner": decisions.get("winner", {}).get("fullName"),
        "loser": decisions.get("loser", {}).get("fullName"),
        "save": decisions.get("save", {}).get("fullName"),
        "homers": homers, "multihit": multihit[:3], "starter": starter,
        "phi_record": f"{phi['leagueRecord']['wins']}-{phi['leagueRecord']['losses']}",
        "date_display": (datetime.now(ET_TZ).date() - timedelta(days=1)).strftime("%A"),
    }


def compose_recap(f):
    """Deterministic sentence-by-sentence recap from the facts. No AI needed."""
    verb = "beat" if f["won"] else "fell to"
    where = f"at {f['venue']}" if f["is_home"] else f"on the road at {f['venue']}"
    s = [f"The Phillies {verb} the {f['opp_name']} {max(f['phi_score'], f['opp_score'])}-"
         f"{min(f['phi_score'], f['opp_score'])} {where} on {f['date_display']}, "
         f"moving to {f['phi_record']} on the season."]
    if f["homers"]:
        parts = [f"{h['name']} ({h['hr']} HR, {h['rbi']} RBI)" if h["hr"] > 1 or h["rbi"] > 1
                 else h["name"] for h in f["homers"]]
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        s.append(f"{joined} went deep for Philadelphia.")
    if f["multihit"]:
        mh = ", ".join(f"{m['name']} ({m['h']}-for-{m['ab']})" for m in f["multihit"])
        s.append(f"Multi-hit games came from {mh}.")
    if f["starter"]:
        st = f["starter"]
        s.append(f"Starter {st['name']} went {st['ip']} innings, allowing {st['er']} earned "
                 f"run{'s' if st['er'] != 1 else ''} on {st['h']} hit{'s' if st['h'] != 1 else ''} "
                 f"with {st['k']} strikeout{'s' if st['k'] != 1 else ''}.")
    dec = []
    if f["winner"]:
        dec.append(f"{f['winner']} got the win")
    if f["loser"]:
        dec.append(f"{f['loser']} took the loss")
    if f["save"]:
        dec.append(f"{f['save']} earned the save")
    if dec:
        sent = ", ".join(dec) + "."
        s.append(sent[0].upper() + sent[1:])
    return " ".join(s)


def ai_recap(facts, fallback):
    """Optional: if ANTHROPIC_API_KEY is set, have Claude write the recap."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return fallback
    try:
        r = SESSION.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 400,
                  "messages": [{"role": "user", "content":
                      "Write a 4-6 sentence recap of this Phillies game in the voice of a "
                      "sharp beat writer. Use ONLY these facts, do not invent anything: "
                      + json.dumps(facts) +
                      " Respond with the recap paragraph only, no preamble."}]},
            timeout=30)
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
        return text or fallback
    except Exception as e:
        print(f"  [warn] AI recap failed, using built-in: {e}", file=sys.stderr)
        return fallback


def fetch_last_night():
    facts = gather_last_game_facts()
    if not facts:
        return None
    facts["recap"] = ai_recap(facts, compose_recap(facts))
    return facts


# ------------------------------------------------------------- betting lines
def fetch_odds():
    """Today's Phillies lines from The Odds API (free tier). Needs ODDS_API_KEY."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return {"configured": False}
    data = get("https://api.the-odds-api.com/v4/sports/baseball_mlb/odds", params={
        "apiKey": key, "regions": "us", "markets": "h2h,spreads,totals",
        "oddsFormat": "american", "bookmakers": "draftkings,fanduel,betmgm"})
    for event in data:
        if "Philadelphia Phillies" not in (event.get("home_team"), event.get("away_team")):
            continue
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if start.astimezone(ET_TZ).date() != datetime.now(ET_TZ).date():
            continue
        books = event.get("bookmakers", [])
        if not books:
            continue
        book = books[0]
        out = {"configured": True, "book": book.get("title", ""),
               "home": event["home_team"], "away": event["away_team"],
               "ml": {}, "spread": {}, "total": None}
        for m in book.get("markets", []):
            if m["key"] == "h2h":
                for o in m["outcomes"]:
                    out["ml"][o["name"]] = f"{'+' if o['price'] > 0 else ''}{o['price']}"
            elif m["key"] == "spreads":
                for o in m["outcomes"]:
                    out["spread"][o["name"]] = {
                        "pt": f"{'+' if o['point'] > 0 else ''}{o['point']}",
                        "price": f"{'+' if o['price'] > 0 else ''}{o['price']}"}
            elif m["key"] == "totals":
                over = next((o for o in m["outcomes"] if o["name"] == "Over"), None)
                if over:
                    out["total"] = over["point"]
        return out
    return {"configured": True, "book": None}  # key works, no Phillies game listed


# ---------------------------------------------------- NL wild card standings
def fetch_wildcard(limit=8):
    data = get(f"{API}/standings", params={
        "leagueId": 104, "season": datetime.now(ET_TZ).year, "standingsTypes": "wildCard"})
    rows = []
    for rec in data.get("records", []):
        for tr in rec.get("teamRecords", []):
            rows.append({
                "rank": tr.get("wildCardRank", ""),
                "team": tr["team"]["name"],
                "is_phi": tr["team"]["id"] == PHILLIES_ID,
                "w": tr["wins"], "l": tr["losses"],
                "wc_gb": tr.get("wildCardGamesBack", "-"),
                "streak": tr.get("streak", {}).get("streakCode", "-"),
                "l10": next((f"{s['wins']}-{s['losses']}"
                             for s in tr.get("records", {}).get("splitRecords", [])
                             if s.get("type") == "lastTen"), "-"),
            })
    rows.sort(key=lambda r: int(r["rank"]) if str(r["rank"]).isdigit() else 99)
    return rows[:limit]


# ------------------------------------------------------------------- rumors
def fetch_rumors(limit=6):
    r = SESSION.get("https://www.mlbtraderumors.com/philadelphia-phillies/feed", timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub = item.findtext("pubDate", "").strip()
        try:
            pub_dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z").astimezone(ET_TZ)
            pub_disp = pub_dt.strftime("%b %-d, %-I:%M %p ET")
        except Exception:
            pub_disp = pub
        items.append({"title": html.unescape(title), "link": link, "when": pub_disp})
        if len(items) >= limit:
            break
    return items


# -------------------------------------------------------------------- render
def build():
    now = datetime.now(ET_TZ)
    today_str = now.date().isoformat()
    print(f"Building Phillies Daily for {today_str}...")

    games = safe(lambda: fetch_game(today_str), None)
    game_blocks = []
    if games:
        for game in games:
            phi = game["home"] if game["is_home"] else game["away"]
            opp = game["away"] if game["is_home"] else game["home"]
            for side in (game["home"], game["away"]):
                if side["pitcher"]:
                    side["pitcher"]["line"] = safe(
                        lambda s=side: pitcher_season_line(s["pitcher"]["id"]), None)
            weather = None
            if game["lat"] and game["lon"]:
                weather = safe(lambda g=game: fetch_weather(g["lat"], g["lon"], g["start_dt"]), None)
            phi_bats = safe(lambda: matchup_table(PHILLIES_ID, opp["pitcher"]), [])
            opp_bats = safe(lambda: matchup_table(opp["id"], phi["pitcher"]), [])
            game_blocks.append({"game": game, "phi": phi, "opp": opp, "weather": weather,
                                "phi_bats": phi_bats, "opp_bats": opp_bats})

    context = {
        "date_display": now.strftime("%A, %B %-d, %Y"),
        "generated": now.strftime("%-I:%M %p ET"),
        "game_blocks": game_blocks,
        "no_game": not game_blocks,
        "last_night": safe(fetch_last_night, None),
        "odds": safe(fetch_odds, {"configured": False}),
        "transactions": safe(fetch_transactions, []),
        "standings": safe(fetch_standings, []),
        "wildcard": safe(fetch_wildcard, []),
        "rumors": safe(fetch_rumors, []),
    }

    with open("template.html") as f:
        page = Template(f.read()).render(**context)
    with open("index.html", "w") as f:
        f.write(page)
    print("Wrote index.html")


if __name__ == "__main__":
    build()
