"""
PropEdge Live Scores
=====================
Polls NBA live scoreboard and updates live.json for the dashboard.
Runs every 5 minutes during game windows (7 PM – 1 AM ET).

Usage:
  python live_scores.py           # fetch current live scores
  python live_scores.py --once    # single fetch (for testing)
"""

import json
import requests
import os
import sys
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(os.environ.get('PROPEDGE_DIR', '.')).resolve() / 'data'

NBA_SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"

TEAM_MAP = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE',
    'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
    'LA Clippers': 'LAC', 'Los Angeles Clippers': 'LAC',
    'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN',
    'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK',
    'Oklahoma City Thunder': 'OKC', 'Orlando Magic': 'ORL',
    'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC',
    'San Antonio Spurs': 'SAS', 'Toronto Raptors': 'TOR',
    'Utah Jazz': 'UTA', 'Washington Wizards': 'WAS',
}


def fetch_live_scores():
    """Fetch current NBA scoreboard."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.nba.com/',
            'Origin': 'https://www.nba.com',
        }
        resp = requests.get(NBA_SCOREBOARD_URL, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  ✗ Scoreboard returned {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        print(f"  ✗ Fetch error: {e}")
        return None


def parse_scoreboard(data):
    """Parse NBA scoreboard JSON into live scores format."""
    scoreboard = data.get('scoreboard', {})
    games = scoreboard.get('games', [])

    live_games = []
    for game in games:
        home = game.get('homeTeam', {})
        away = game.get('awayTeam', {})

        home_name = home.get('teamName', '')
        away_name = away.get('teamName', '')
        home_city = home.get('teamCity', '')
        away_city = away.get('teamCity', '')

        home_full = f"{home_city} {home_name}"
        away_full = f"{away_city} {away_name}"
        home_abr = TEAM_MAP.get(home_full, home.get('teamTricode', ''))
        away_abr = TEAM_MAP.get(away_full, away.get('teamTricode', ''))

        status = game.get('gameStatusText', '')
        game_status = game.get('gameStatus', 1)
        # 1 = not started, 2 = in progress, 3 = final

        # Player leaders
        leaders = {}
        for leader_cat in game.get('gameLeaders', {}).values():
            if isinstance(leader_cat, dict):
                name = leader_cat.get('name', '')
                pts = leader_cat.get('points', 0)
                if name and pts:
                    leaders[name] = pts

        live_games.append({
            'gameId': game.get('gameId', ''),
            'home': home_abr,
            'away': away_abr,
            'homeScore': home.get('score', 0),
            'awayScore': away.get('score', 0),
            'status': status,
            'gameStatus': game_status,  # 1=pregame, 2=live, 3=final
            'period': game.get('period', 0),
            'clock': game.get('gameClock', ''),
            'homeRecord': f"{home.get('wins',0)}-{home.get('losses',0)}",
            'awayRecord': f"{away.get('wins',0)}-{away.get('losses',0)}",
        })

    return {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'date': str(date.today()),
        'gamesTotal': len(games),
        'gamesLive': sum(1 for g in live_games if g['gameStatus'] == 2),
        'gamesFinal': sum(1 for g in live_games if g['gameStatus'] == 3),
        'games': live_games,
    }


def main():
    print(f"PropEdge Live Scores — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    data = fetch_live_scores()
    if not data:
        print("  No data returned. Exiting.")
        return

    live = parse_scoreboard(data)
    print(f"  Games: {live['gamesTotal']} total, {live['gamesLive']} live, {live['gamesFinal']} final")

    for g in live['games']:
        status_icon = "🔴" if g['gameStatus'] == 2 else "✅" if g['gameStatus'] == 3 else "⏳"
        print(f"  {status_icon} {g['away']} {g['awayScore']} @ {g['home']} {g['homeScore']} — {g['status']}")

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    live_path = DATA_DIR / 'live.json'
    with open(live_path, 'w') as f:
        json.dump(live, f, default=str)
    print(f"  Saved to {live_path}")

    # Check if any games still in progress
    if live['gamesLive'] > 0:
        print(f"  {live['gamesLive']} games still live")
    elif live['gamesFinal'] == live['gamesTotal'] and live['gamesTotal'] > 0:
        print(f"  All games final — no more updates needed tonight")
    else:
        print(f"  No games started yet")


if __name__ == '__main__':
    main()
