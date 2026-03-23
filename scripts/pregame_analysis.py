"""
PropEdge Pre-Game Analysis Script
===================================
Runs daily at 2 PM ET via GitHub Actions.

1. Fetches today's player point props from The Odds API
2. Fetches today's spreads/totals
3. Loads current player averages (from morning grading)
4. Runs the 10-signal model on each prop
5. Generates all 68 fields per play including flags, reasoning, edge
6. Adds new plays to the database
7. Outputs today's plays for the dashboard

Usage:
  python pregame_analysis.py                        # analyze today
  python pregame_analysis.py --date 2026-03-22      # specific date
  python pregame_analysis.py --dry-run              # preview without saving
"""

import pandas as pd
import numpy as np
import json
import os
import sys
import argparse
import requests
import time
from datetime import datetime, timedelta, date
from pathlib import Path

# ─── PATHS ───
BASE_DIR = Path(os.environ.get('PROPEDGE_DIR', '.'))
DATA_DIR = BASE_DIR / 'data'

PLAYS_FILE = DATA_DIR / 'base_plays.json'
GAME_LOGS_FILE = DATA_DIR / 'game_logs.json'
PLAYER_AVGS_FILE = DATA_DIR / 'player_averages.json'
DVP_FILE = DATA_DIR / 'dvp_rankings.json'
SUMMARY_FILE = DATA_DIR / 'summary.json'
TODAY_FILE = DATA_DIR / 'today.json'

# ─── CONFIG ───
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_nba"
ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')

# ─── NAME MAP ───
NAME_MAP = {
    'A.J. Green':'AJ Green','C.J. McCollum':'CJ McCollum',
    'G.G. Jackson':'GG Jackson','R.J. Barrett':'RJ Barrett',
    'Carlton Carrington':'Bub Carrington','Nicolas Claxton':'Nic Claxton',
    'Jabari Smith Jr':'Jabari Smith Jr.','Jaime Jaquez Jr':'Jaime Jaquez Jr.',
    'Jaren Jackson Jr':'Jaren Jackson Jr.','Kelly Oubre Jr':'Kelly Oubre Jr.',
    'Michael Porter Jr':'Michael Porter Jr.','Scotty Pippen Jr':'Scotty Pippen Jr.',
    'Tim Hardaway Jr':'Tim Hardaway Jr.','Wendell Carter Jr':'Wendell Carter Jr.',
    'Gary Trent Jr':'Gary Trent Jr.','Craig Porter Jr':'Craig Porter Jr.',
    'Paul Reed Jr':'Paul Reed','Isaiah Stewart II':'Isaiah Stewart',
    'Robert Williams':'Robert Williams III','Jimmy Butler':'Jimmy Butler III',
    'Ron Holland':'Ronald Holland II','Herb Jones':'Herbert Jones',
    'Derrick Jones':'Derrick Jones Jr.','Moe Wagner':'Moritz Wagner',
    'Bogdan Bogdanovic':'Bogdan Bogdanović','Nikola Jokic':'Nikola Jokić',
    'Nikola Vucevic':'Nikola Vučević','Luka Doncic':'Luka Dončić',
    'Jonas Valanciunas':'Jonas Valančiūnas','Nikola Jovic':'Nikola Jović',
    'Vit Krejci':'Vít Krejčí','Dennis Schroder':'Dennis Schröder',
    'Jusuf Nurkic':'Jusuf Nurkić','Kristaps Porzingis':'Kristaps Porziņģis',
    'Moussa Diabate':'Moussa Diabaté','Tidjane Salaun':'Tidjane Salaün',
    'Kasparas Jakucionis':'Kasparas Jakučionis','Karlo Matkovic':'Karlo Matković',
    'Dario Saric':'Dario Šarić','Egor Demin':'Egor Dëmin',
    'Hugo Gonzalez':'Hugo González',
    'Yanic Konan Niederhauser':'Yanic Konan Niederhäuser',
    'Vincent Williams Jr':'Vincent Williams Jr.',
}
def norm_name(n): return NAME_MAP.get(n.strip(), n.strip())

TEAM_FULL = {
    'ATL':'Atlanta Hawks','BOS':'Boston Celtics','BKN':'Brooklyn Nets',
    'CHA':'Charlotte Hornets','CHI':'Chicago Bulls','CLE':'Cleveland Cavaliers',
    'DAL':'Dallas Mavericks','DEN':'Denver Nuggets','DET':'Detroit Pistons',
    'GSW':'Golden State Warriors','HOU':'Houston Rockets','IND':'Indiana Pacers',
    'LAC':'Los Angeles Clippers','LAL':'Los Angeles Lakers','MEM':'Memphis Grizzlies',
    'MIA':'Miami Heat','MIL':'Milwaukee Bucks','MIN':'Minnesota Timberwolves',
    'NOP':'New Orleans Pelicans','NYK':'New York Knicks','OKC':'Oklahoma City Thunder',
    'ORL':'Orlando Magic','PHI':'Philadelphia 76ers','PHX':'Phoenix Suns',
    'POR':'Portland Trail Blazers','SAC':'Sacramento Kings','SAS':'San Antonio Spurs',
    'TOR':'Toronto Raptors','UTA':'Utah Jazz','WAS':'Washington Wizards'
}

POS_MAP = {'G':'G','PG':'G','SG':'G','F':'F','SF':'F','PF':'F','C':'C',
           'G-F':'G','F-G':'F','F-C':'F','C-F':'C'}

def american_to_decimal(odds):
    if odds is None or odds == 0: return None
    try:
        odds = float(odds)
        if odds > 0: return round(1 + odds / 100, 2)
        else: return round(1 + 100 / abs(odds), 2)
    except: return None


# ═══════════════════════════════════════════════════════════════
# 1. FETCH TODAY'S PROPS FROM ODDS API
# ═══════════════════════════════════════════════════════════════

def fetch_todays_props(api_key):
    """Fetch player points props for today's NBA games."""
    print("  Fetching events...")
    events_url = f"{ODDS_API_BASE}/sports/{SPORT}/events"
    resp = requests.get(events_url, params={'apiKey': api_key}, timeout=30)
    if resp.status_code != 200:
        print(f"  ✗ Events fetch failed: {resp.status_code}")
        return [], []

    events = resp.json()
    today = date.today()
    today_events = []
    for e in events:
        commence = pd.to_datetime(e['commence_time'])
        event_date = (commence - pd.Timedelta(hours=5)).date()  # ET
        if event_date == today:
            today_events.append(e)

    print(f"  Found {len(today_events)} games today")

    # Fetch spreads/totals
    print("  Fetching spreads/totals...")
    spreads_url = f"{ODDS_API_BASE}/sports/{SPORT}/odds"
    resp = requests.get(spreads_url, params={
        'apiKey': api_key, 'regions': 'us', 'markets': 'spreads,totals',
        'oddsFormat': 'american'
    }, timeout=30)
    time.sleep(0.5)

    spreads_data = {}
    if resp.status_code == 200:
        for event in resp.json():
            eid = event['id']
            for book in event.get('bookmakers', []):
                for market in book.get('markets', []):
                    if market['key'] == 'spreads':
                        for outcome in market['outcomes']:
                            if outcome['name'] == event.get('home_team'):
                                spreads_data.setdefault(eid, {})['spread'] = outcome.get('point')
                    elif market['key'] == 'totals':
                        for outcome in market['outcomes']:
                            if outcome['name'] == 'Over':
                                spreads_data.setdefault(eid, {})['total'] = outcome.get('point')

    # Fetch player points props
    all_props = []
    for event in today_events:
        eid = event['id']
        home = event.get('home_team', '')
        away = event.get('away_team', '')
        commence = event.get('commence_time', '')
        commence_et = pd.to_datetime(commence) - pd.Timedelta(hours=5)
        game_time = commence_et.strftime('%-I:%M %p')

        print(f"  Fetching props: {away} @ {home}...")
        props_url = f"{ODDS_API_BASE}/sports/{SPORT}/events/{eid}/odds"
        resp = requests.get(props_url, params={
            'apiKey': api_key, 'regions': 'us,us2', 'markets': 'player_points',
            'oddsFormat': 'american'
        }, timeout=30)
        time.sleep(0.5)

        remaining = resp.headers.get('x-requests-remaining', '?')
        print(f"    API quota remaining: {remaining}")

        if resp.status_code != 200:
            print(f"    ✗ Failed: {resp.status_code}")
            continue

        data = resp.json()
        bookmakers = data.get('bookmakers', [])

        # Aggregate player lines across books
        player_lines = {}
        for book in bookmakers:
            for market in book.get('markets', []):
                if market.get('key') != 'player_points':
                    continue
                for outcome in market.get('outcomes', []):
                    player = outcome.get('description', '')
                    point = outcome.get('point')
                    price = outcome.get('price')
                    side = outcome.get('name', '')
                    if not player or point is None:
                        continue
                    if player not in player_lines:
                        player_lines[player] = {}
                    if point not in player_lines[player]:
                        player_lines[player][point] = {'over': [], 'under': []}
                    if side == 'Over':
                        player_lines[player][point]['over'].append(price)
                    elif side == 'Under':
                        player_lines[player][point]['under'].append(price)

        # Find home/away abbreviations
        home_abr = next((k for k, v in TEAM_FULL.items() if v == home), home[:3].upper())
        away_abr = next((k for k, v in TEAM_FULL.items() if v == away), away[:3].upper())

        sp = spreads_data.get(eid, {})

        for player, lines_by_point in player_lines.items():
            all_lines = list(lines_by_point.keys())
            if not all_lines:
                continue

            # Consensus line (most common)
            line_counts = {ln: len(v['over']) + len(v['under']) for ln, v in lines_by_point.items()}
            consensus = max(line_counts, key=line_counts.get)
            overs = lines_by_point[consensus]['over']
            unders = lines_by_point[consensus]['under']

            avg_over = round(np.mean(overs)) if overs else -110
            avg_under = round(np.mean(unders)) if unders else -110
            n_books = max(len(overs), len(unders))

            all_props.append({
                'player': player,
                'line': consensus,
                'overOdds': american_to_decimal(avg_over),
                'underOdds': american_to_decimal(avg_under),
                'books': n_books,
                'minLine': min(all_lines),
                'maxLine': max(all_lines),
                'home': home_abr,
                'away': away_abr,
                'gameTime': game_time,
                'commence': commence,
                'eventId': eid,
                'spread': sp.get('spread'),
                'total': sp.get('total'),
                'fullHome': home,
                'fullAway': away,
            })

        print(f"    ✓ {len(player_lines)} player props")

    print(f"\n  Total props fetched: {len(all_props)}")
    return all_props, today_events


# ═══════════════════════════════════════════════════════════════
# 2. RUN 10-SIGNAL MODEL
# ═══════════════════════════════════════════════════════════════

def get_dvp(dvp_data, team, pos):
    if not dvp_data or team not in dvp_data: return 15
    d = dvp_data[team]
    if pos == 'G': return round((d.get('PG',15) + d.get('SG',15)) / 2)
    elif pos == 'F': return round((d.get('SF',15) + d.get('PF',15)) / 2)
    elif pos == 'C': return d.get('C', 15)
    return 15

def get_def_overall(dvp_data, team):
    if not dvp_data or team not in dvp_data: return 15
    d = dvp_data[team]
    return round(sum(d.values()) / len(d))

def count_flags(direction, vol, hr30, hr10, trend, defP, h2h_avg, line, h2hG, pace, fgTrend, minTrend):
    """Count how many of 10 signals agree with direction."""
    is_over = direction == 'OVER'
    flags = 0
    total = 0
    details = []

    checks = [
        ('Volume', (is_over and vol > 0) or (not is_over and vol < 0), f"L30 {'>' if vol>0 else '<'} line by {abs(vol):.1f}"),
        ('HR L30', (is_over and hr30 > 50) or (not is_over and hr30 < 50), f"{hr30}% over rate"),
        ('HR L10', (is_over and hr10 > 50) or (not is_over and hr10 < 50), f"{hr10}% over rate"),
        ('Trend', (is_over and trend > 0) or (not is_over and trend < 0), f"L5-L30: {trend:+.1f}"),
        ('Context', (is_over and vol > -1) or (not is_over and vol < 1), "Home/Away edge"),
        ('Defense', (is_over and defP > 15) or (not is_over and defP < 15), f"#{defP} vs position"),
    ]

    for name, agrees, detail in checks:
        total += 1
        if agrees: flags += 1
        details.append({'name': name, 'agrees': agrees, 'detail': detail})

    if h2hG >= 3 and h2h_avg is not None:
        total += 1
        agrees = (is_over and h2h_avg > line) or (not is_over and h2h_avg < line)
        if agrees: flags += 1
        details.append({'name': 'H2H', 'agrees': agrees, 'detail': f"Avg {h2h_avg:.1f} vs line {line}"})

    more = [
        ('Pace', (is_over and pace < 15) or (not is_over and pace > 15), f"#{pace}"),
        ('FG Trend', fgTrend is not None and ((is_over and fgTrend > 0) or (not is_over and fgTrend < 0)), f"{fgTrend:+.1f}%" if fgTrend else "N/A"),
        ('Min Trend', minTrend is not None and ((is_over and minTrend > 0) or (not is_over and minTrend < 0)), f"{minTrend:+.1f} min" if minTrend else "N/A"),
    ]
    for name, agrees, detail in more:
        total += 1
        if agrees: flags += 1
        details.append({'name': name, 'agrees': agrees, 'detail': detail})

    return flags, total, details


def generate_pre_reason(direction, conf, flags, total, flag_details, line, h2h_str, blowout, spread, std10):
    """Generate pre-match reasoning."""
    if direction == 'NO PLAY':
        return f"Signals split — {flags}/{total} flags agree, not enough conviction."

    pct = int(conf * 100)
    parts = [f"Model projects {direction} {line} at {pct}% confidence ({flags}/{total} flags)."]

    supporting = [d for d in flag_details if d['agrees']]
    opposing = [d for d in flag_details if not d['agrees']]

    if supporting:
        names = [d['name'].lower() + f" ({d['detail']})" for d in supporting[:3]]
        parts.append(f"Key signals: {', '.join(names)}.")

    if opposing and len(opposing) <= 3:
        parts.append(f"Caution: {', '.join(d['name'] for d in opposing[:2])} lean against.")

    if h2h_str:
        parts.append(f"H2H: {h2h_str}.")
    if blowout and direction == 'UNDER':
        parts.append(f"Blowout risk (spread {spread:+.1f}) may limit minutes.")
    if std10 and std10 > 8:
        parts.append(f"⚠ High variance (σ={std10}).")

    return " ".join(parts)


def run_model(prop, averages, game_logs_df, h2h_data, dvp_data, player_hist, bucket_hist):
    """Run the 10-signal model on a single prop. Returns a complete play dict or None."""
    player_name = prop['player']
    matched = norm_name(player_name)
    line = prop['line']
    home = prop['home']
    away = prop['away']
    today = date.today()

    # Get player averages
    avg = averages.get(matched)
    if not avg:
        return None

    team = avg.get('Team')
    if not team:
        return None

    # Determine home/away
    if team == home:
        is_home = True; opponent = away
    elif team == away:
        is_home = False; opponent = home
    else:
        return None

    # Get stats from averages
    L30 = avg.get('L30_avg_pts')
    L10 = avg.get('L10_avg_pts')
    L5 = avg.get('L5_avg_pts')
    L3 = avg.get('L3_avg_pts')
    if L30 is None: return None
    if L5 is None: L5 = L30
    if L10 is None: L10 = L30
    if L3 is None: L3 = L30

    # Get player game logs for hit rate calculation
    player_logs = game_logs_df[game_logs_df['Player'] == matched].sort_values('Date', ascending=False)
    if len(player_logs) < 5:
        return None

    prior = player_logs.head(30)
    hr30 = round((prior['Points'] > line).sum() / len(prior) * 100)
    hr10 = round((prior.head(10)['Points'] > line).sum() / min(10, len(prior)) * 100)
    recent = [int(x) for x in player_logs.head(5)['Points'].tolist()]
    std10 = round(player_logs.head(10)['Points'].std(), 1) if len(player_logs) >= 3 else 0

    # Position
    pos = None  # Will come from prop data or lookup
    # For now use a simple heuristic or external position data
    # The prop itself might have position info depending on API response

    # ─── 10 SIGNALS ───
    s1 = np.clip((L30 - line) / 5, -1, 1)
    s2 = (hr30 / 100 - 0.5) * 2
    s3 = (hr10 / 100 - 0.5) * 2
    s4 = np.clip((L5 - L30) / 5, -1, 1)

    # Context (home/away)
    if is_home:
        ctx = avg.get('home_l30_avg_pts', L30) or L30
    else:
        ctx = avg.get('away_l30_avg_pts', L30) or L30
    s5 = np.clip((ctx - line) / 5, -1, 1)

    # Defense vs position
    defP = get_dvp(dvp_data, opponent, pos) if pos else 15
    defO = get_def_overall(dvp_data, opponent)
    s6 = np.clip((defP - 15) / 15, -1, 1)

    # H2H
    h2h_key = (matched, opponent)
    h2h_info = h2h_data.get(h2h_key, {})
    h2h_games = h2h_info.get('games', 0)
    h2h_avg = h2h_info.get('avg_pts')
    use_h2h = h2h_games >= 3 and h2h_avg is not None
    s7 = np.clip((h2h_avg - line) / 5, -1, 1) if use_h2h else 0.0
    h2h_str = f"{h2h_avg:.1f} ({h2h_games}g)" if use_h2h else ""

    # Pace
    opp_logs = game_logs_df[game_logs_df['Opponent'] == opponent]
    pace_rank = 15  # default
    if 'Opp Pace Rank' in opp_logs.columns and len(opp_logs) > 0:
        pace_rank = round(opp_logs['Opp Pace Rank'].mean())
    s8 = np.clip((15 - pace_rank) / 15, -1, 1)

    # FG% trend
    fg30 = avg.get('L30_fg_pct')
    fg10 = avg.get('L10_fg_pct')
    fga30 = avg.get('L30_avg_fga')
    fga10 = avg.get('L10_avg_fga')
    s9 = np.clip((fg10 - fg30) / 10, -1, 1) if (fg10 and fg30) else 0.0
    fgTrend = round(fg10 - fg30, 1) if (fg10 and fg30) else None

    # Minutes trend
    min30 = avg.get('L30_avg_min')
    min10 = avg.get('L10_avg_min')
    s10 = np.clip((min10 - min30) / 5, -1, 1) if (min10 and min30) else 0.0
    minTrend = round(min10 - min30, 1) if (min10 and min30) else None

    # ─── COMPOSITE ───
    W = {1:3.0,2:2.5,3:2.0,4:1.5,5:1.5,6:1.5,7:1.0,8:0.5,9:1.0,10:0.75}
    S = {1:s1,2:s2,3:s3,4:s4,5:s5,6:s6,7:s7,8:s8,9:s9,10:s10}
    if not use_h2h:
        tw = sum(w for k, w in W.items() if k != 7)
        ws = sum(W[k]*S[k] for k in S if k != 7)
    else:
        tw = sum(W.values())
        ws = sum(W[k]*S[k] for k in S)
    norm = ws / tw

    direction = 'OVER' if norm > 0.05 else 'UNDER' if norm < -0.05 else 'NO PLAY'
    raw_dir = 'OVER' if norm > 0 else 'UNDER'

    conf = np.clip(0.5 + abs(norm) * 0.3, 0.50, 0.85)
    if pos == 'C': conf -= 0.06
    if direction == 'OVER' and 10 <= line <= 15: conf += 0.05
    if std10 > 8: conf -= 0.03
    conf = float(np.clip(conf, 0.45, 0.85))

    tier = 1 if conf >= 0.62 else 2 if conf >= 0.55 else 3
    units = 2.0 if tier == 1 else 1.0 if tier == 2 else 1.0
    if direction == 'NO PLAY': units = 0.0

    vol = round(L30 - line, 1)
    trend_val = round(L5 - L30, 1)

    spread_val = prop.get('spread')
    total_val = prop.get('total')
    blowout = abs(spread_val) >= 10 if spread_val else False

    match_short = f"{TEAM_FULL.get(away,'').split()[-1]} @ {TEAM_FULL.get(home,'').split()[-1]}"
    match_full = f"{TEAM_FULL.get(away, away)} @ {TEAM_FULL.get(home, home)}"

    # Flags
    flags, flagsTotal, flagDetails = count_flags(
        direction if direction != 'NO PLAY' else raw_dir,
        vol, hr30, hr10, trend_val, defP, h2h_avg, line, h2h_games, pace_rank, fgTrend, minTrend
    )

    # Implied probability + edge
    if direction == 'OVER' and prop['overOdds']:
        impliedProb = round(1 / prop['overOdds'] * 100, 1)
    elif direction == 'UNDER' and prop['underOdds']:
        impliedProb = round(1 / prop['underOdds'] * 100, 1)
    else:
        impliedProb = None
    edge = round(conf * 100 - impliedProb, 1) if impliedProb else None

    # Player historical accuracy
    ph = player_hist.get(player_name, {})
    playerModelHR = round(ph['wins'] / ph['plays'] * 100, 1) if ph.get('plays', 0) >= 3 else None
    playerModelPlays = ph.get('plays')

    bucket = f"T{tier}_{int(conf*100)//5*5}"
    bh = bucket_hist.get(bucket, {})
    bucketHR = round(bh['wins'] / bh['plays'] * 100, 1) if bh.get('plays', 0) >= 5 else None
    bucketPlays = bh.get('plays')

    # Pre-match reason
    preMatchReason = generate_pre_reason(direction, conf, flags, flagsTotal, flagDetails,
                                          line, h2h_str, blowout, spread_val or 0, std10)

    recentOver = sum(1 for r in recent if r > line)
    recentUnder = sum(1 for r in recent if r < line)

    return {
        'date': str(today),
        'player': player_name,
        'match': match_short,
        'fullMatch': match_full,
        'gameTime': prop.get('gameTime', ''),
        'position': pos or '',
        'posSimple': pos,
        'line': line,
        'dir': direction,
        'rawDir': raw_dir,
        'conf': round(conf, 3),
        'tier': tier,
        'units': units,
        'l30': round(L30, 1),
        'l10': round(L10, 1),
        'l5': round(L5, 1),
        'l3': round(L3, 1),
        'hr30': hr30,
        'hr10': hr10,
        'recent': recent,
        'defO': defO,
        'defP': defP,
        'pace': pace_rank,
        'h2h': h2h_str,
        'h2hG': h2h_games,
        'fgL30': fg30,
        'fgL10': fg10,
        'fga30': fga30,
        'fga10': fga10,
        'minL30': min30,
        'minL10': min10,
        'std10': std10,
        'avail': 'Active',
        'spread': spread_val,
        'total': total_val,
        'blowout': blowout,
        'actualPts': None,
        'result': None,
        'delta': None,
        'volume': vol,
        'trend': trend_val,
        'fgTrend': fgTrend,
        'minTrend': minTrend,
        'overOdds': prop['overOdds'],
        'underOdds': prop['underOdds'],
        'books': prop['books'],
        'minLine': prop['minLine'],
        'maxLine': prop['maxLine'],
        'flags': flags,
        'flagsTotal': flagsTotal,
        'flagsStr': f"{flags}/{flagsTotal}",
        'flagDetails': flagDetails,
        'preMatchReason': preMatchReason,
        'reason': '',
        'homeAvgPts': avg.get('home_avg_pts'),
        'awayAvgPts': avg.get('away_avg_pts'),
        'b2bAvgPts': avg.get('b2b_avg_pts'),
        'restAvgPts': avg.get('rest_avg_pts'),
        'recentOver': recentOver,
        'recentUnder': recentUnder,
        'lineSpread': prop['maxLine'] - prop['minLine'],
        'impliedProb': impliedProb,
        'edge': edge,
        'playerModelHR': playerModelHR,
        'playerModelPlays': playerModelPlays,
        'bucketHR': bucketHR,
        'bucketPlays': bucketPlays,
    }


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='PropEdge Pre-Game Analysis')
    parser.add_argument('--date', type=str, help='Analyze specific date')
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    parser.add_argument('--api-key', type=str, help='Odds API key (overrides env var)')
    args = parser.parse_args()

    api_key = args.api_key or ODDS_API_KEY
    if not api_key:
        print("✗ No Odds API key. Set ODDS_API_KEY env var or use --api-key")
        sys.exit(1)

    print("=" * 60)
    print("PropEdge Pre-Game Analysis")
    print("=" * 60)

    # Load database
    print("\nLoading database...")
    with open(PLAYS_FILE) as f:
        plays = json.load(f)
    with open(PLAYER_AVGS_FILE) as f:
        averages = json.load(f)
    with open(GAME_LOGS_FILE) as f:
        game_logs = json.load(f)

    gl_df = pd.DataFrame(game_logs)
    gl_df['Date'] = pd.to_datetime(gl_df['Date'])
    gl_df = gl_df.sort_values(['Player', 'Date'], ascending=[True, False])

    # Load DvP
    dvp_data = {}
    if DVP_FILE.exists():
        with open(DVP_FILE) as f:
            dvp_data = json.load(f)

    # Build H2H lookup
    h2h_data = {}
    for (player, opp), logs in gl_df.groupby(['Player', 'Opponent']):
        if len(logs) < 1: continue
        h2h_data[(player, opp)] = {
            'games': len(logs),
            'avg_pts': round(logs['Points'].mean(), 1),
        }

    # Build historical accuracy
    player_hist = {}
    bucket_hist = {}
    for p in plays:
        if p.get('result') not in ('WIN', 'LOSS'): continue
        name = p['player']
        if name not in player_hist: player_hist[name] = {'plays': 0, 'wins': 0}
        player_hist[name]['plays'] += 1
        if p['result'] == 'WIN': player_hist[name]['wins'] += 1
        bucket = f"T{p['tier']}_{int(p['conf']*100)//5*5}"
        if bucket not in bucket_hist: bucket_hist[bucket] = {'plays': 0, 'wins': 0}
        bucket_hist[bucket]['plays'] += 1
        if p['result'] == 'WIN': bucket_hist[bucket]['wins'] += 1

    print(f"  Plays: {len(plays)}, Averages: {len(averages)}, Game logs: {len(game_logs)}")

    # Fetch today's props
    print("\nFetching today's props...")
    raw_props, events = fetch_todays_props(api_key)

    if not raw_props:
        print("No props found for today. Exiting.")
        return

    # Run model
    print(f"\nRunning 10-signal model on {len(raw_props)} props...")
    today_plays = []
    skipped = 0
    for prop in raw_props:
        play = run_model(prop, averages, gl_df, h2h_data, dvp_data, player_hist, bucket_hist)
        if play:
            today_plays.append(play)
        else:
            skipped += 1

    today_plays.sort(key=lambda p: (p['tier'], -p['conf']))

    t1 = sum(1 for p in today_plays if p['tier'] == 1)
    t2 = sum(1 for p in today_plays if p['tier'] == 2)
    overs = sum(1 for p in today_plays if p['dir'] == 'OVER')
    unders = sum(1 for p in today_plays if p['dir'] == 'UNDER')

    print(f"\n  Scored: {len(today_plays)}, Skipped: {skipped}")
    print(f"  Tier 1: {t1}, Tier 2: {t2}")
    print(f"  OVER: {overs}, UNDER: {unders}")

    if not args.dry_run:
        # Add to database
        plays.extend(today_plays)
        with open(PLAYS_FILE, 'w') as f:
            json.dump(plays, f, default=str)

        # Save today's plays separately for dashboard
        with open(TODAY_FILE, 'w') as f:
            json.dump(today_plays, f, default=str)

        print(f"\n  Database updated: {len(plays)} total plays")
        print(f"  Today's plays saved to {TODAY_FILE}")
    else:
        print(f"\n  DRY RUN — not saving. Preview:")
        for p in today_plays[:5]:
            print(f"    {p['player']} {p['dir']} {p['line']} | {p['flagsStr']} | Conf:{p['conf']} | Edge:{p.get('edge')}%")

    print(f"\n{'=' * 60}")
    print(f"PRE-GAME ANALYSIS COMPLETE")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
