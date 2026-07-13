# Merz Reporting Dashboard

This is a Flask app that pulls Merz chatbot data from the Inbenta Reporting API and shows it in a web dashboard. Everything lands in MySQL (`merz_db`) so you can reload the UI without hitting the API every time.

The `merz` folder is self-contained — you can copy it anywhere and run it from there. No parent repo required.

---

## What you need

- Python 3.11+
- MySQL 8+ with a database called `merz_db`
- Inbenta API credentials (key, secret, signature key)
- Dependencies from `requirements.txt`

---

## Getting started

**1. Create the database (one time)**

```bash
mysql -u root -p -e "CREATE DATABASE merz_db CHARACTER SET utf8mb4;"
```

**2. Set up `.env`**

Copy the example below and fill in your real values. See the Configuration section for details.

**3. Install and run**

```bash
pip install -r requirements.txt
py -3 run_merz.py
```

Then open **http://localhost:8501** in your browser.

Pick a date range in the sidebar and click **Generate Report**. That fetches from Inbenta (when needed), stores data in MySQL, and loads the dashboard.

---

## Configuration

### `.env` — secrets and database

Keep this file local. Don't commit it.

```env
MERZ_DATABASE_URL=mysql+pymysql://root:password@127.0.0.1:3306/merz_db
MERZ_REFRESH_DAYS=7

# Optional — needed if you want real content titles instead of "Content 123"
MERZ_INBENTA_EDITOR_API_KEY=...
MERZ_INBENTA_EDITOR_API_SECRET=...
MERZ_INBENTA_EDITOR_PERSONAL_SECRET_KEY=...
MERZ_INBENTA_EDITOR_BASE_URL=https://chatbot-api-us.inbenta.io/editor/v1
```

`MERZ_REFRESH_DAYS` controls caching: dates within the last 7 days are always re-fetched from the API. Older dates are served from MySQL if they're already there.

### `config.yaml` — Inbenta Reporting API

This file holds the reporting API settings: `auth_url`, `key`, `secret`, `signature_key`, `base_url`, and optional source filters. The database URL stays in `.env`, not here.

---

## How the app is organized

The Python code lives in `reporting_dashboard/` as a small set of flat modules — no deep package tree.

```
merz/
├── run_merz.py              # starts the server
├── config.yaml              # Inbenta reporting API config
├── .env                     # your secrets (gitignored)
├── requirements.txt
└── reporting_dashboard/
    ├── config.py            # paths, env vars, YAML loader, date helpers
    ├── db.py                # MySQL connection + table models
    ├── api.py               # Inbenta auth, signing, API clients
    ├── ingest.py            # fetch from API → save raw tables
    ├── sync.py              # enrich data, adverse flags, sync logic
    ├── dashboard.py         # queries + chart metrics
    ├── excel.py             # Excel export
    ├── app.py               # Flask app + routes
    ├── templates/
    └── static/
```

If you're trying to find something: API calls → `api.py`, database tables → `db.py`, "why didn't my sync run?" → `sync.py`.

---

## What happens when you click Generate Report

1. **ingest.py** calls Inbenta day-by-day and writes raw rows (`raw_user_questions`, `raw_sessions`, clicks, ratings, etc.)
2. **sync.py** enriches that data — content titles, adverse-event flags, HCP vs patient, recommendations
3. Enriched rows go into `user_questions` (the table the dashboard actually reads)
4. **dashboard.py** computes metrics and charts
5. The browser gets JSON from `/api/sync` and renders everything

If all dates in your range are already cached and older than 7 days, the app skips the API call and just loads from MySQL.

---

## Database tables (quick reference)

| Table | What's in it |
|---|---|
| `raw_user_questions` | Raw question events from the API |
| `raw_sessions` | Session events (variables, survey triggers, etc.) |
| `raw_uq_matchings` | Content recommendations per question |
| `raw_clicks` | Which content users clicked |
| `raw_ratings` | User ratings and comments |
| `agg_session_details` | Session summaries (duration, linked questions, content IDs) |
| `content_lookup` | Cached content ID → title (from Editor API) |
| `ingestion_runs` | Log of each sync (success/fail, counts) |
| `user_questions` | Dashboard-ready rows (enriched, with adverse flags) |
| `survey_answers` | Survey: solved?, rating 1–5, comment |

---

## API routes

| Route | What it does |
|---|---|
| `GET /` | Dashboard page |
| `POST /api/sync` | Fetch from Inbenta if needed, return dashboard data |
| `POST /api/refresh` | Reload dashboard from MySQL only (no API call) |
| `POST /api/export` | Build Excel file, return download link |
| `GET /download/<filename>` | Download the Excel file |
| `GET /health` | Simple health check |

There's no separate "Sync" button in the UI — **Generate Report** triggers `/api/sync`.

---

## What the dashboard shows

- Totals: questions, sessions, adverse events, HCP vs patient sessions
- Daily question trend
- Top questions and top clicked content
- Content performance (impressions, clicks, CTR)
- Product interaction breakdown (Belotero, Xeomin, etc.)
- Survey results: solved %, average rating, rating distribution
- Session drill-down: questions, recommendations (clicked or not), survey answers

---

## Excel export

Click **Export Excel** in the sidebar. The app builds a `.xlsx` grouped by session — questions, recommended content, ratings — and saves it under `data/reports/`. You'll get a download link when it's ready.
