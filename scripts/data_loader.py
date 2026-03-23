"""
PropEdge Data Loader
====================
Common utilities for loading/saving the database files.
Handles gzip compression for large files (game_logs, base_plays).
"""

import json
import gzip
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get('PROPEDGE_DIR', '.')).resolve() / 'data'


def load_json(filename):
    """Load a JSON file, trying gzip first then plain."""
    gz_path = DATA_DIR / f'{filename}.gz'
    plain_path = DATA_DIR / filename

    if gz_path.exists():
        with gzip.open(gz_path, 'rt') as f:
            return json.load(f)
    elif plain_path.exists():
        with open(plain_path) as f:
            return json.load(f)
    else:
        raise FileNotFoundError(f"Neither {gz_path} nor {plain_path} found")


def save_json(data, filename, compress=False):
    """Save data to JSON, optionally gzip compressed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if compress:
        path = DATA_DIR / f'{filename}.gz'
        with gzip.open(path, 'wt') as f:
            json.dump(data, f, default=str)
        # Remove uncompressed version if it exists
        plain = DATA_DIR / filename
        if plain.exists():
            plain.unlink()
    else:
        path = DATA_DIR / filename
        with open(path, 'w') as f:
            json.dump(data, f, default=str)

    return path


def load_plays():
    return load_json('base_plays.json')

def save_plays(plays):
    return save_json(plays, 'base_plays.json', compress=True)

def load_game_logs():
    return load_json('game_logs.json')

def save_game_logs(logs):
    return save_json(logs, 'game_logs.json', compress=True)

def load_averages():
    return load_json('player_averages.json')

def save_averages(avgs):
    return save_json(avgs, 'player_averages.json')

def load_dvp():
    return load_json('dvp_rankings.json')

def load_summary():
    return load_json('summary.json')

def save_summary(summary):
    return save_json(summary, 'summary.json')

def save_today(plays):
    return save_json(plays, 'today.json')

def save_history(plays, date_str):
    history_dir = DATA_DIR / 'history'
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f'{date_str}.json'
    with open(path, 'w') as f:
        json.dump(plays, f, default=str)
    return path
