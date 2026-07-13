# Merz Analytics

This project contains a Flask dashboard and a reporting script for pulling chatbot analytics from the Inbenta API, storing results in MySQL, and showing them in a web interface.

## What this project includes

- A Flask web app for viewing reporting data
- A daily report script for generating and emailing reports
- Configuration for the Inbenta API in YAML
- Local data storage under the data folder

## Main files

- run_merz.py — starts the web dashboard
- simple_daily_report.py — generates a daily report and can send it by email
- config.yaml — main API configuration values
- requirements.txt — Python dependencies
- reporting_dashboard/ — Flask app modules, templates, and static assets
- data/ — local report output and generated files
- .env — local secrets and environment values (not committed)

## Requirements

- Python 3.11+
- MySQL 8+ with a database named merz_db
- Inbenta API credentials

## Quick start

1. Create the MySQL database

```bash
mysql -u root -p -e "CREATE DATABASE merz_db CHARACTER SET utf8mb4;"
```

2. Create a local .env file with the required values

Example:

```env
MERZ_INBENTA_API_KEY=your_key
MERZ_INBENTA_API_SECRET=your_secret
MERZ_INBENTA_SIGNATURE_KEY=your_signature_key
MERZ_INBENTA_AUTH_URL=https://api.inbenta.io/v1/auth
DASHBOARD_USERNAME=your_dashboard_user
DASHBOARD_PASSWORD=your_dashboard_password
EMAIL_USER=your_email
EMAIL_PASSWORD=your_password
EMAIL_TO=recipient@example.com
TIMEZONE=America/New_York
MERZ_REPORT_DIR=data/reports
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

4. Start the dashboard

```bash
python run_merz.py
```

Then open http://localhost:8501 in your browser.

## Run the daily report script

```bash
python simple_daily_report.py
```

This script uses the same environment settings and can produce report files and email them if configured.

## Project structure

```text
merz_analytics/
├── run_merz.py
├── simple_daily_report.py
├── config.yaml
├── requirements.txt
├── .env
├── data/
└── reporting_dashboard/
    ├── app.py
    ├── api.py
    ├── config.py
    ├── dashboard.py
    ├── db.py
    ├── excel.py
    ├── ingest.py
    ├── sync.py
    ├── static/
    └── templates/
```

## Notes

- Keep secrets in .env and do not commit them.
- The app uses the reporting_dashboard package for the Flask interface and API interactions.
- Generated reports are saved under data/reports by default.
