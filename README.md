# PropEdge 🏀

NBA Player Prop Analysis — 10-Signal Prediction Model

## Quick Start

### 1. Fork this repo
Click "Fork" on GitHub to create your own copy.

### 2. Add your Odds API key
Go to **Settings → Secrets → Actions** and add:
- `ODDS_API_KEY` — your key from [the-odds-api.com](https://the-odds-api.com) (free tier: 500 requests/month)

### 3. Enable GitHub Pages
Go to **Settings → Pages → Source: main branch** → Save.
Your dashboard will be live at `https://yourusername.github.io/propedge/`

### 4. Enable GitHub Actions
Go to **Actions** tab → Click "I understand, enable workflows".

### 5. Open on iPad
Visit your GitHub Pages URL in Safari → Share → Add to Home Screen.

## How It Works

| Time | What Happens |
|------|-------------|
| **6 AM ET** | Morning grading: fetches last night's box scores, grades picks, recalculates all rolling averages |
| **2 PM ET** | Pre-game analysis: fetches today's props, runs 10-signal model, generates picks |
| **7 PM – 1 AM ET** | Live scores: polls NBA scoreboard every 5 minutes |

## The 10-Signal Model

| Signal | Weight | What It Measures |
|--------|--------|-----------------|
| Volume | 3.0 | L30 scoring average vs line |
| HR L30 | 2.5 | Hit rate over last 30 games |
| HR L10 | 2.0 | Hit rate over last 10 games |
| Trend | 1.5 | L5 vs L30 momentum |
| Context | 1.5 | Home/away scoring split |
| Defense | 1.5 | Opponent defense vs position rank |
| H2H | 1.0 | Historical matchup average |
| Pace | 0.5 | Opponent pace rank |
| FG% Trend | 1.0 | Shooting efficiency trend |
| Min Trend | 0.75 | Minutes trend |

**Tiers:** T1 ≥ 62% confidence (2 units) · T2 ≥ 55% (1 unit)

## Season Performance (2025-26)

- **12,842** props scored
- **56.9%** overall hit rate
- **61.2%** Tier 1 hit rate (897 plays)
- **59.0%** Tier 2 hit rate (4,067 plays)

## Files

```
propedge/
├── index.html                    # Dashboard (opens in browser)
├── data/
│   ├── base_plays.json.gz        # All scored plays (compressed)
│   ├── game_logs.json.gz         # Player game logs (compressed)
│   ├── player_averages.json      # Current rolling averages
│   ├── dvp_rankings.json         # Defense vs position
│   ├── summary.json              # Per-date aggregates
│   ├── today.json                # Today's picks (generated at 2 PM)
│   ├── live.json                 # Live scores (updated every 5 min)
│   └── history/                  # Daily archives
├── scripts/
│   ├── morning_grading.py        # Grade last night's games
│   ├── pregame_analysis.py       # Generate today's picks
│   ├── live_scores.py            # Fetch live scores
│   └── data_loader.py            # Database utilities
└── .github/workflows/
    ├── morning_grading.yml       # 6 AM ET schedule
    ├── pregame_analysis.yml      # 2 PM ET schedule
    └── live_scores.yml           # Every 5 min game window
```

## Manual Triggers

You can run any workflow manually from the **Actions** tab:
- Click the workflow name → "Run workflow" → "Run"

## Cost

| Service | Free Tier | Monthly Cost |
|---------|-----------|-------------|
| GitHub Pages | Unlimited | $0 |
| GitHub Actions | 2,000 min/month | $0 |
| The Odds API | 500 requests/month | $0 (free tier) |
| NBA Live Data | Unlimited | $0 |

**Total: $0/month** for personal use.
