"""
PropEdge Morning Grading Script
================================
Runs daily at 6 AM ET via GitHub Actions.

1. Fetches box scores from last night's games (NBA API)
2. Appends new game logs to the game_logs database
3. Recalculates ALL rolling averages from game logs (L3-L200)
4. Grades yesterday's ungraded plays (WIN/LOSS/PUSH)
5. Generates post-match reasoning
6. Updates season-wide accuracy stats
7. Saves everything back to the database files

Usage:
  python morning_grading.py                    # grade yesterday
  python morning_grading.py --date 2026-03-21  # grade specific date
  python morning_grading.py --backfill 7       # grade last 7 days
"""

import pandas as pd
import numpy as np
import json
import os
import sys
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path

# ─── Try importing nba_api; skip if not available (for local testing) ───
try:
    from nba_api.stats.endpoints import ScoreboardV3, BoxScoreTraditionalV3, LeagueGameLog
    from nba_api.stats.static import teams as nba_teams
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    print("⚠ nba_api not installed. Using local game logs only.")

import time

# ─── PATHS ───
BASE_DIR = Path(os.environ.get('PROPEDGE_DIR', '.'))
DATA_DIR = BASE_DIR / 'data'
HISTORY_DIR = DATA_DIR / 'history'

PLAYS_FILE = DATA_DIR / 'base_plays.json'
GAME_LOGS_FILE = DATA_DIR / 'game_logs.json'
PLAYER_AVGS_FILE = DATA_DIR / 'player_averages.json'
SUMMARY_FILE = DATA_DIR / 'summary.json'
DVP_FILE = DATA_DIR / 'dvp_rankings.json'

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
# Build reverse map too (NBA name → PropEdge name)
REVERSE_MAP = {v: k for k, v in NAME_MAP.items()}

def norm_name(name):
    return NAME_MAP.get(name.strip(), name.strip())

def reverse_name(nba_name):
    """Get PropEdge name from NBA API name"""
    return REVERSE_MAP.get(nba_name.strip(), nba_name.strip())

# ─── TEAM MAPS ───
TEAM_ABR_TO_ID = {}
TEAM_ID_TO_ABR = {}
if NBA_API_AVAILABLE:
    for t in nba_teams.get_teams():
        TEAM_ABR_TO_ID[t['abbreviation']] = t['id']
        TEAM_ID_TO_ABR[t['id']] = t['abbreviation']


# ═══════════════════════════════════════════════════════════════
# 1. FETCH BOX SCORES FROM NBA API
# ═══════════════════════════════════════════════════════════════

def fetch_box_scores(game_date):
    """Fetch all box scores for games on a given date using NBA API V3."""
    if not NBA_API_AVAILABLE:
        print(f"  ⚠ NBA API not available, skipping fetch for {game_date}")
        return []

    # V3 uses YYYY-MM-DD format (V2 used MM/DD/YYYY)
    date_str = game_date.strftime('%Y-%m-%d')
    print(f"  Fetching scoreboard for {game_date}...")

    try:
        sb = ScoreboardV3(game_date=date_str)
        time.sleep(0.6)
        games_df   = sb.game_header.get_data_frame()   # gameId, gameCode, gameStatus, ...
        line_scores = sb.line_score.get_data_frame()   # gameId, teamId, teamTricode, score, ...
    except Exception as e:
        print(f"  ✗ Scoreboard error: {e}")
        return []

    if len(games_df) == 0:
        print(f"  No games found for {game_date}")
        return []

    # Derive home/away from gameCode (format: YYYYMMDD/AWYHOM, e.g. 20251031/ATLLAL)
    # Away tricode = chars 9-11, Home tricode = chars 12-14
    game_info = {}
    for _, row in games_df.iterrows():
        gid  = row['gameId']
        code = str(row.get('gameCode', ''))
        if '/' in code:
            teams_part = code.split('/')[-1]          # e.g. "ATLLAL"
            away_abr   = teams_part[:3].upper()
            home_abr   = teams_part[3:].upper()
        else:
            # Fallback: use line_score order (NBA convention: row0=away, row1=home)
            ls = line_scores[line_scores['gameId'] == gid]
            away_abr = ls.iloc[0]['teamTricode'] if len(ls) > 0 else ''
            home_abr = ls.iloc[1]['teamTricode'] if len(ls) > 1 else ''

        # Determine winner from line scores
        ls = line_scores[line_scores['gameId'] == gid]
        winner_abr = None
        if len(ls) >= 2:
            scores = ls[['teamTricode', 'score']].dropna(subset=['score'])
            if len(scores) == 2:
                scores = scores.copy()
                scores['score'] = pd.to_numeric(scores['score'], errors='coerce')
                scores = scores.dropna(subset=['score'])
                if len(scores) == 2:
                    winner_abr = scores.loc[scores['score'].idxmax(), 'teamTricode']

        game_info[gid] = {'home': home_abr, 'away': away_abr, 'winner': winner_abr}

    print(f"  Found {len(game_info)} games")

    all_logs = []
    for gid, info in game_info.items():
        home_abr = info['home']
        away_abr = info['away']
        winner_abr = info['winner']
        try:
            box = BoxScoreTraditionalV3(game_id=gid)
            time.sleep(0.6)
            # V3: first dataset is PlayerStats — camelCase column names
            player_stats = box.get_data_frames()[0]

            for _, ps in player_stats.iterrows():
                mins_raw = ps.get('minutes', '')
                # V3 returns ISO 8601 duration: PT27M15.00S  (or None/'' for DNP)
                if not mins_raw or str(mins_raw) in ('', 'PT00M00.00S', 'None'):
                    continue  # DNP

                team_abr = ps['teamTricode']
                is_home  = (team_abr == home_abr)
                opponent = away_abr if is_home else home_abr

                # Full player name from separate firstName + familyName
                player_name = f"{ps.get('firstName', '')} {ps.get('familyName', '')}".strip()

                # Parse ISO 8601 duration: PT27M15.00S → float minutes
                mins_float = 0.0
                mins_str = str(mins_raw)
                if mins_str.startswith('PT'):
                    mins_str = mins_str[2:]           # strip 'PT'
                    if 'M' in mins_str:
                        m_part, rest = mins_str.split('M', 1)
                        s_part = rest.replace('S', '') or '0'
                        try:
                            mins_float = int(m_part) + float(s_part) / 60
                        except ValueError:
                            mins_float = 0.0
                else:
                    try:
                        mins_float = float(mins_str)
                    except (ValueError, TypeError):
                        mins_float = 0.0

                # W/L for this player's team
                if winner_abr:
                    wl = 'W' if team_abr == winner_abr else 'L'
                else:
                    wl = ''

                log = {
                    'Player':     player_name,
                    'Team':       team_abr,
                    'Player ID':  ps.get('personId', ''),
                    'Season':     '2025-26',
                    'Date':       str(game_date),
                    'Matchup':    f"{team_abr} {'vs.' if is_home else '@'} {opponent}",
                    'Opponent':   opponent,
                    'Home/Away':  'Home' if is_home else 'Away',
                    'Minutes':    round(mins_float, 2),
                    'Points':     int(ps.get('points',                0) or 0),
                    'FGM':        int(ps.get('fieldGoalsMade',        0) or 0),
                    'FGA':        int(ps.get('fieldGoalsAttempted',   0) or 0),
                    'FG%':        round(float(ps.get('fieldGoalsPercentage',   0) or 0) * 100, 1),
                    '3PM':        int(ps.get('threePointersMade',     0) or 0),
                    '3PA':        int(ps.get('threePointersAttempted',0) or 0),
                    '3P%':        round(float(ps.get('threePointersPercentage',0) or 0) * 100, 1),
                    'FTM':        int(ps.get('freeThrowsMade',        0) or 0),
                    'FTA':        int(ps.get('freeThrowsAttempted',   0) or 0),
                    'FT%':        round(float(ps.get('freeThrowsPercentage',   0) or 0) * 100, 1),
                    'REB':        int(ps.get('reboundsTotal',         0) or 0),
                    'AST':        int(ps.get('assists',               0) or 0),
                    'STL':        int(ps.get('steals',                0) or 0),
                    'BLK':        int(ps.get('blocks',                0) or 0),
                    'TOV':        int(ps.get('turnovers',             0) or 0),
                    '+/-':        int(ps.get('plusMinusPoints',       0) or 0),
                    'W/L':        wl,
                    'Game_ID':    gid,
                }
                all_logs.append(log)

            print(f"    ✓ {away_abr} @ {home_abr}: {len(player_stats)} players")

        except Exception as e:
            print(f"    ✗ Game {gid} error: {e}")
            time.sleep(1)

    return all_logs


# ═══════════════════════════════════════════════════════════════
# 2. RECALCULATE ROLLING AVERAGES FROM GAME LOGS
# ═══════════════════════════════════════════════════════════════

def calculate_rolling_averages(game_logs_df):
    """
    Recalculate ALL rolling averages for every player from their game logs.
    Returns a dict: player_name → {L3_avg_pts, L5_avg_pts, ..., L30_fg_pct, ...}
    """
    game_logs_df = game_logs_df.sort_values(['Player', 'Date'], ascending=[True, False])

    averages = {}
    for player, logs in game_logs_df.groupby('Player'):
        logs = logs.head(200)  # max L200 window
        n = len(logs)
        avg = {'Player': player, 'Team': logs.iloc[0]['Team'], 'Total_Games': n}

        for window in [3, 5, 10, 20, 30, 50, 100, 200]:
            w = logs.head(window)
            if len(w) == 0:
                continue
            prefix = f'L{window}'
            avg[f'{prefix}_avg_pts'] = round(w['Points'].mean(), 1)
            avg[f'{prefix}_avg_min'] = round(w['Minutes'].mean(), 1)
            avg[f'{prefix}_avg_fga'] = round(w['FGA'].mean(), 1)
            avg[f'{prefix}_avg_fta'] = round(w['FTA'].mean(), 1)
            avg[f'{prefix}_avg_reb'] = round(w['REB'].mean(), 1)
            avg[f'{prefix}_avg_ast'] = round(w['AST'].mean(), 1)

            # Shooting percentages (from totals, not averages of percentages)
            total_fgm = w['FGM'].sum()
            total_fga = w['FGA'].sum()
            total_3pm = w['3PM'].sum()
            total_3pa = w['3PA'].sum()
            total_ftm = w['FTM'].sum()
            total_fta = w['FTA'].sum()

            avg[f'{prefix}_fg_pct'] = round(total_fgm / total_fga * 100, 1) if total_fga > 0 else 0
            avg[f'{prefix}_3p_pct'] = round(total_3pm / total_3pa * 100, 1) if total_3pa > 0 else 0
            avg[f'{prefix}_ft_pct'] = round(total_ftm / total_fta * 100, 1) if total_fta > 0 else 0

        # Recent 5 game scores
        avg['recent_5'] = logs.head(5)['Points'].tolist()

        # StdDev L10
        if n >= 3:
            avg['std_l10'] = round(logs.head(10)['Points'].std(), 1)
        else:
            avg['std_l10'] = 0

        # Home/Away splits
        home_logs = logs[logs['Home/Away'] == 'Home']
        away_logs = logs[logs['Home/Away'] == 'Away']
        avg['home_avg_pts'] = round(home_logs['Points'].mean(), 1) if len(home_logs) > 0 else None
        avg['away_avg_pts'] = round(away_logs['Points'].mean(), 1) if len(away_logs) > 0 else None
        avg['home_l30_avg_pts'] = round(home_logs.head(30)['Points'].mean(), 1) if len(home_logs) > 0 else None
        avg['away_l30_avg_pts'] = round(away_logs.head(30)['Points'].mean(), 1) if len(away_logs) > 0 else None

        # B2B detection (games where rest days = 1 or consecutive dates)
        dates = logs['Date'].tolist()
        b2b_logs = []
        rest_logs = []
        for i, row in logs.iterrows():
            row_date = pd.to_datetime(row['Date']).date()
            idx_in_list = dates.index(row['Date'])
            if idx_in_list < len(dates) - 1:
                prev_date = pd.to_datetime(dates[idx_in_list + 1]).date()
                diff = (row_date - prev_date).days
                if diff <= 1:
                    b2b_logs.append(row)
                else:
                    rest_logs.append(row)
            else:
                rest_logs.append(row)

        avg['b2b_avg_pts'] = round(pd.DataFrame(b2b_logs)['Points'].mean(), 1) if b2b_logs else None
        avg['rest_avg_pts'] = round(pd.DataFrame(rest_logs)['Points'].mean(), 1) if rest_logs else None

        averages[player] = avg

    return averages


def calculate_h2h(game_logs_df):
    """Calculate head-to-head records for all player-opponent combos."""
    h2h = {}
    for (player, opp), logs in game_logs_df.groupby(['Player', 'Opponent']):
        if len(logs) < 1:
            continue
        h2h[(player, opp)] = {
            'games': len(logs),
            'avg_pts': round(logs['Points'].mean(), 1),
            'avg_min': round(logs['Minutes'].mean(), 1),
        }
    return h2h


# ═══════════════════════════════════════════════════════════════
# 3. GRADE PLAYS
# ═══════════════════════════════════════════════════════════════

def generate_post_reason(p):
    """Generate post-match reasoning for a graded play."""
    actual = p.get('actualPts')
    result = p.get('result')
    direction = p.get('dir')
    line = p['line']
    player = p['player']

    if actual is None or result not in ('WIN', 'LOSS'):
        return ""

    abs_delta = abs(actual - line)
    pts = int(actual)
    parts = []
    factors_h = []
    factors_f = []

    vol = p.get('volume', 0)
    if direction == 'OVER':
        if vol > 2 and result == 'WIN': factors_h.append(f"L30 avg ({p['l30']}) well above {line}")
        elif vol < -2 and result == 'LOSS': factors_f.append(f"L30 avg ({p['l30']}) was below the line")
    else:
        if vol < -2 and result == 'WIN': factors_h.append(f"L30 avg ({p['l30']}) well below {line}")
        elif vol > 2 and result == 'LOSS': factors_f.append(f"L30 avg ({p['l30']}) above the line")

    trend = p.get('trend', 0)
    if abs(trend) >= 2:
        if trend > 0 and direction == 'OVER' and result == 'WIN':
            factors_h.append(f"hot streak (L5 {p['l5']} vs L30 {p['l30']})")
        elif trend < 0 and direction == 'UNDER' and result == 'WIN':
            factors_h.append(f"cold streak (L5 {p['l5']} below L30)")
        elif trend > 0 and direction == 'UNDER' and result == 'LOSS':
            factors_f.append(f"rising form (L5 {p['l5']} trending up)")
        elif trend < 0 and direction == 'OVER' and result == 'LOSS':
            factors_f.append(f"declining form (L5 {p['l5']} dropping)")

    defP = p.get('defP', 15)
    if defP <= 5 and result == 'WIN' and direction == 'UNDER':
        factors_h.append(f"elite defense (#{defP}) suppressed scoring")
    elif defP >= 25 and result == 'WIN' and direction == 'OVER':
        factors_h.append(f"weak defense (#{defP}) gave easy looks")
    elif defP <= 5 and result == 'LOSS' and direction == 'OVER':
        factors_f.append(f"elite defense (#{defP}) was too tough")
    elif defP >= 25 and result == 'LOSS' and direction == 'UNDER':
        factors_f.append(f"weak defense (#{defP}) couldn't contain")

    fgT = p.get('fgTrend')
    if fgT and abs(fgT) >= 4:
        if fgT < -4 and direction == 'UNDER' and result == 'WIN':
            factors_h.append(f"shooting slump ({p['fgL10']}% vs {p['fgL30']}%)")
        elif fgT > 4 and direction == 'OVER' and result == 'WIN':
            factors_h.append(f"hot shooting ({p['fgL10']}% vs {p['fgL30']}%)")
        elif fgT < -4 and direction == 'OVER' and result == 'LOSS':
            factors_f.append(f"cold shooting ({p['fgL10']}% vs {p['fgL30']}%)")
        elif fgT > 4 and direction == 'UNDER' and result == 'LOSS':
            factors_f.append(f"hot shooting ({p['fgL10']}% vs {p['fgL30']}%)")

    if result == 'WIN':
        if direction == 'OVER':
            parts.append(f"{player} scored {pts}, clearing {line} by {abs_delta:.1f}.")
        else:
            parts.append(f"{player} held to {pts} pts against {line}, {abs_delta:.1f} under.")
        if factors_h:
            parts.append(f"Key drivers: {', '.join(factors_h[:3])}.")
    else:
        if direction == 'OVER':
            parts.append(f"OVER missed — {player} scored {pts} vs {line}, fell {abs_delta:.1f} short.")
        else:
            parts.append(f"UNDER missed — {player} scored {pts}, beat {line} by {abs_delta:.1f}.")
        if factors_f:
            parts.append(f"What went wrong: {', '.join(factors_f[:2])}.")
        else:
            parts.append("No single signal predicted this outcome.")

    return " ".join(parts)


def grade_plays(plays, box_scores, target_date):
    """Grade ungraded plays for a specific date using box score data."""
    # Build lookup: player_name → points scored
    pts_lookup = {}
    for log in box_scores:
        pts_lookup[log['Player']] = log['Points']
        # Also add reverse-mapped name
        rev = reverse_name(log['Player'])
        if rev != log['Player']:
            pts_lookup[rev] = log['Points']

    graded_count = 0
    for p in plays:
        if p['date'] != str(target_date):
            continue
        if p.get('result') in ('WIN', 'LOSS', 'PUSH'):
            continue  # already graded

        player = p['player']
        matched = norm_name(player)

        actual = pts_lookup.get(player) or pts_lookup.get(matched)
        if actual is None:
            continue  # DNP or name mismatch

        p['actualPts'] = float(actual)
        p['delta'] = round(actual - p['line'], 1)

        if p['dir'] == 'OVER':
            if actual > p['line']:
                p['result'] = 'WIN'
            elif actual == p['line']:
                p['result'] = 'PUSH'
            else:
                p['result'] = 'LOSS'
        elif p['dir'] == 'UNDER':
            if actual < p['line']:
                p['result'] = 'WIN'
            elif actual == p['line']:
                p['result'] = 'PUSH'
            else:
                p['result'] = 'LOSS'
        else:
            p['result'] = 'NO_PLAY'

        p['reason'] = generate_post_reason(p)
        graded_count += 1

    return graded_count


# ═══════════════════════════════════════════════════════════════
# 4. UPDATE SEASON STATS
# ═══════════════════════════════════════════════════════════════

def update_season_stats(plays):
    """Recalculate player model HR and bucket HR across all graded plays."""
    player_hist = {}
    bucket_hist = {}

    for p in plays:
        if p.get('result') not in ('WIN', 'LOSS'):
            continue
        # Player accuracy
        name = p['player']
        if name not in player_hist:
            player_hist[name] = {'plays': 0, 'wins': 0}
        player_hist[name]['plays'] += 1
        if p['result'] == 'WIN':
            player_hist[name]['wins'] += 1

        # Bucket accuracy
        bucket = f"T{p['tier']}_{int(p['conf'] * 100) // 5 * 5}"
        if bucket not in bucket_hist:
            bucket_hist[bucket] = {'plays': 0, 'wins': 0}
        bucket_hist[bucket]['plays'] += 1
        if p['result'] == 'WIN':
            bucket_hist[bucket]['wins'] += 1

    # Apply to all plays
    for p in plays:
        ph = player_hist.get(p['player'])
        if ph and ph['plays'] >= 3:
            p['playerModelHR'] = round(ph['wins'] / ph['plays'] * 100, 1)
            p['playerModelPlays'] = ph['plays']

        bucket = f"T{p['tier']}_{int(p['conf'] * 100) // 5 * 5}"
        bh = bucket_hist.get(bucket)
        if bh and bh['plays'] >= 5:
            p['bucketHR'] = round(bh['wins'] / bh['plays'] * 100, 1)
            p['bucketPlays'] = bh['plays']


def generate_summary(plays):
    """Generate per-date summary stats."""
    from collections import defaultdict
    ds = defaultdict(lambda: {'plays': 0, 'graded': 0, 'wins': 0, 'losses': 0,
                               't1': 0, 't1w': 0, 't2': 0, 't2w': 0, 'games': set()})
    for p in plays:
        d = ds[p['date']]
        d['plays'] += 1
        d['games'].add(p['match'])
        if p.get('result') in ('WIN', 'LOSS'):
            d['graded'] += 1
            if p['result'] == 'WIN':
                d['wins'] += 1
            else:
                d['losses'] += 1
            if p['tier'] == 1:
                d['t1'] += 1
                d['t1w'] += (1 if p['result'] == 'WIN' else 0)
            elif p['tier'] == 2:
                d['t2'] += 1
                d['t2w'] += (1 if p['result'] == 'WIN' else 0)

    return [{'date': k, 'plays': v['plays'], 'graded': v['graded'], 'wins': v['wins'],
             'losses': v['losses'], 'hr': round(v['wins'] / v['graded'] * 100, 1) if v['graded'] else 0,
             't1': v['t1'], 't1w': v['t1w'], 't2': v['t2'], 't2w': v['t2w'],
             'games': len(v['games'])} for k, v in sorted(ds.items())]


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='PropEdge Morning Grading')
    parser.add_argument('--date', type=str, help='Grade specific date (YYYY-MM-DD)')
    parser.add_argument('--backfill', type=int, help='Grade last N days')
    parser.add_argument('--skip-fetch', action='store_true', help='Skip NBA API fetch, use existing game logs')
    args = parser.parse_args()

    print("=" * 60)
    print("PropEdge Morning Grading")
    print("=" * 60)

    # Determine target date(s)
    today = date.today()
    if args.date:
        target_dates = [date.fromisoformat(args.date)]
    elif args.backfill:
        target_dates = [today - timedelta(days=i) for i in range(1, args.backfill + 1)]
    else:
        target_dates = [today - timedelta(days=1)]  # yesterday

    print(f"Target dates: {[str(d) for d in target_dates]}")

    # Load database
    print(f"\nLoading database...")
    with open(PLAYS_FILE) as f:
        plays = json.load(f)
    print(f"  Plays: {len(plays)}")

    # Load or initialize game logs
    if GAME_LOGS_FILE.exists():
        with open(GAME_LOGS_FILE) as f:
            game_logs = json.load(f)
        print(f"  Game logs: {len(game_logs)}")
    else:
        print(f"  ⚠ No game_logs.json found. Initializing from xlsx...")
        gl_df = pd.read_excel('NBA_2025_26_Season_Player_Stats.xlsx', sheet_name='All Game Logs')
        game_logs = gl_df.to_dict('records')
        for log in game_logs:
            log['Date'] = str(pd.to_datetime(log['Date']).date())
        print(f"  Initialized with {len(game_logs)} logs")

    existing_dates = set(log['Date'] for log in game_logs)

    # ─── STEP 1: Fetch new box scores ───
    all_new_logs = []
    for target_date in target_dates:
        ds = str(target_date)
        if ds in existing_dates and not args.skip_fetch:
            print(f"\n  {ds}: already have game logs, skipping fetch")
            # Still need to grade
            date_logs = [l for l in game_logs if l['Date'] == ds]
            all_new_logs.extend(date_logs)
            continue

        if not args.skip_fetch:
            print(f"\n  Fetching box scores for {ds}...")
            new_logs = fetch_box_scores(target_date)
            if new_logs:
                # Add to game logs
                game_logs.extend(new_logs)
                all_new_logs.extend(new_logs)
                print(f"  Added {len(new_logs)} game logs")
            else:
                print(f"  No box scores returned")
        else:
            date_logs = [l for l in game_logs if l['Date'] == ds]
            all_new_logs.extend(date_logs)

    # ─── STEP 2: Grade plays ───
    print(f"\nGrading plays...")
    total_graded = 0
    for target_date in target_dates:
        ds = str(target_date)
        date_logs = [l for l in game_logs if l['Date'] == ds]
        n = grade_plays(plays, date_logs, target_date)
        total_graded += n
        print(f"  {ds}: graded {n} plays")

    # ─── STEP 3: Recalculate rolling averages ───
    print(f"\nRecalculating rolling averages...")
    gl_df = pd.DataFrame(game_logs)
    gl_df['Date'] = pd.to_datetime(gl_df['Date'])
    gl_df = gl_df.sort_values(['Player', 'Date'], ascending=[True, False])

    averages = calculate_rolling_averages(gl_df)
    h2h = calculate_h2h(gl_df)
    print(f"  Averages for {len(averages)} players")
    print(f"  H2H records for {len(h2h)} player-opponent combos")

    # ─── STEP 4: Update season stats ───
    print(f"\nUpdating season stats...")
    update_season_stats(plays)

    # ─── STEP 5: Save everything ───
    print(f"\nSaving database...")

    # Ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    with open(PLAYS_FILE, 'w') as f:
        json.dump(plays, f, default=str)

    with open(GAME_LOGS_FILE, 'w') as f:
        json.dump(game_logs, f, default=str)

    with open(PLAYER_AVGS_FILE, 'w') as f:
        json.dump(averages, f, default=str)

    summary = generate_summary(plays)
    with open(SUMMARY_FILE, 'w') as f:
        json.dump(summary, f, default=str)

    # Archive
    for target_date in target_dates:
        ds = str(target_date)
        day_plays = [p for p in plays if p['date'] == ds]
        if day_plays:
            with open(HISTORY_DIR / f'{ds}.json', 'w') as f:
                json.dump(day_plays, f, default=str)

    # ─── Summary ───
    graded_all = [p for p in plays if p.get('result') in ('WIN', 'LOSS')]
    wins = sum(1 for p in graded_all if p['result'] == 'WIN')
    print(f"\n{'=' * 60}")
    print(f"GRADING COMPLETE")
    print(f"{'=' * 60}")
    print(f"  New plays graded: {total_graded}")
    print(f"  New game logs added: {len(all_new_logs)}")
    print(f"  Season total: {len(graded_all)} graded, {wins}W/{len(graded_all)-wins}L, HR: {wins/len(graded_all)*100:.1f}%")
    print(f"  Player averages updated: {len(averages)}")
    print(f"  Database saved ✓")


if __name__ == '__main__':
    main()
