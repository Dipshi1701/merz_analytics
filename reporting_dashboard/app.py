"""Flask app factory and all routes."""

import logging
import os
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Blueprint, Flask, jsonify, redirect, render_template, request, send_file, session, url_for

from reporting_dashboard.config import REPORT_DIR
from reporting_dashboard.dashboard import get_adverse_events, get_insights, get_questions_by_session, get_survey_by_session
from reporting_dashboard.db import init_db
from reporting_dashboard.excel import generate_excel
from reporting_dashboard.sync import sync

logger = logging.getLogger(__name__)
bp = Blueprint("dashboard", __name__)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("dashboard.login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_dates():
    end = date.today() - timedelta(days=1)
    return end - timedelta(days=1), end


def _load_dashboard_data(start, end, search=""):
    return {
        "insights": get_insights(start, end),
        "sessions": get_questions_by_session(start, end, search=search),
        "adverse_events": get_adverse_events(start, end),
        "survey_by_session": get_survey_by_session(start, end),
        "period_label": f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
    }


def _read_dates():
    data = request.get_json(silent=True) or request.form
    start_str, end_str = data.get("start_date"), data.get("end_date")
    if not start_str or not end_str:
        raise ValueError("Start and end dates are required.")
    start, end = date.fromisoformat(start_str), date.fromisoformat(end_str)
    if start > end:
        raise ValueError("End date must be on or after start date.")
    return start, end, (data.get("search") or "").strip()


def _err(message, status=400):
    return jsonify({"success": False, "error": message}), status


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard.index"))

    error = None
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        valid_user = os.getenv("DASHBOARD_USERNAME", "merz")
        valid_pass = os.getenv("DASHBOARD_PASSWORD", "Merz@2024")

        if username == valid_user and password == valid_pass:
            session["logged_in"] = True
            session.permanent = True          # honour PERMANENT_SESSION_LIFETIME
            return redirect(url_for("dashboard.index"))
        else:
            error = "Invalid username or password."

    return render_template("login.html", error=error, username=username,
                           year=date.today().year)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("dashboard.login"))


@bp.route("/")
@_login_required
def index():
    start, end = _default_dates()
    return render_template("dashboard.html",
                           default_start=start.isoformat(),
                           default_end=end.isoformat(),
                           today=date.today().isoformat(),
                           now=date.today())


@bp.route("/api/sync", methods=["POST"])
@_login_required
def sync_and_load():
    try:
        start, end, search = _read_dates()
        result = sync(start, end)
        message = result["message"]
        if result["fetched_from_api"]:
            message += f" ({result['questions_synced']} questions, {result['conversations_synced']} conversations)"
        return jsonify({"success": True, "message": message, **_load_dashboard_data(start, end, search)})
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.exception("Sync failed")
        return _err(str(e), 500)


@bp.route("/api/refresh", methods=["POST"])
@_login_required
def refresh_data():
    try:
        start, end, search = _read_dates()
        return jsonify({"success": True, **_load_dashboard_data(start, end, search)})
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.exception("Refresh failed")
        return _err(str(e), 500)


@bp.route("/api/export", methods=["POST"])
@_login_required
def export_excel():
    try:
        start, end, _ = _read_dates()
        filepath = generate_excel(start, end)
        filename = Path(filepath).name
        return jsonify({"success": True, "filename": filename,
                        "download_url": f"/download/{filename}",
                        "message": f"Excel saved: {filename}"})
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.exception("Export failed")
        return _err(str(e), 500)


@bp.route("/download/<path:filename>")
def download(filename):
    file_path = (REPORT_DIR / filename).resolve()
    if not str(file_path).startswith(str(REPORT_DIR.resolve())):
        return "Invalid file path", 403
    if not file_path.exists():
        return f"File not found: {filename}", 404
    return send_file(file_path, as_attachment=True, download_name=file_path.name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/health")
def health():
    return jsonify({"status": "healthy"})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv(
        "MERZ_FLASK_SECRET_KEY",
        os.getenv("FLASK_SECRET_KEY", "merz-dev-secret-change-in-production"),
    )
    # Session expires automatically after 2 days of inactivity or 2 days from login
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=2)
    app.register_blueprint(bp)
    with app.app_context():
        init_db()
    return app
