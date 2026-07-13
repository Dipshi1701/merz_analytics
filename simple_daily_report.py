from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import smtplib
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

os.environ.setdefault("NO_PROXY", "*")
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""


@dataclass(frozen=True)
class Settings:
    tz: ZoneInfo
    report_dir: Path
    auth_url: str
    api_key: str
    api_secret: str
    signature_key: str
    sources: list[str] | None
    smtp_host: str
    smtp_port: int
    email_user: str
    email_password: str
    email_to: list[str]

    @classmethod
    def load(cls) -> Settings:
        required = ("MERZ_INBENTA_API_KEY", "MERZ_INBENTA_API_SECRET", "MERZ_INBENTA_SIGNATURE_KEY")
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise SystemExit(f"Missing in .env: {', '.join(missing)}")

        raw_sources = os.getenv("MERZ_SOURCES", "").strip()
        sources = [s.strip() for s in raw_sources.split(",") if s.strip()] or None
        email_user = os.getenv("EMAIL_USER", "")
        raw_to = os.getenv("EMAIL_TO", email_user)
        recipients = [a.strip() for a in raw_to.replace(";", ",").split(",") if a.strip()]
        if not recipients:
            recipients = [email_user] if email_user else []
        return cls(
            tz=ZoneInfo(os.getenv("TIMEZONE", "America/New_York")),
            report_dir=Path(os.getenv("MERZ_REPORT_DIR", str(ROOT / "data" / "reports"))),
            auth_url=os.getenv("MERZ_INBENTA_AUTH_URL", "https://api.inbenta.io/v1/auth"),
            api_key=os.environ["MERZ_INBENTA_API_KEY"],
            api_secret=os.environ["MERZ_INBENTA_API_SECRET"],
            signature_key=os.environ["MERZ_INBENTA_SIGNATURE_KEY"],
            sources=sources,
            smtp_host=os.getenv("SMTP_HOST", "mail.alphanumeric.ai"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            email_user=email_user,
            email_password=os.getenv("EMAIL_PASSWORD", ""),
            email_to=recipients,
        )


@dataclass
class SessionRow:
    session: str
    questions: str
    date: str
    time: str


class InbentaClient:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._token: str | None = None
        self._token_expires = datetime.min
        self._base_url: str | None = None
        self._last_call = 0.0

    def _auth(self) -> None:
        if self._token and datetime.now() < self._token_expires:
            return
        r = requests.post(
            self.cfg.auth_url,
            headers={"x-inbenta-key": self.cfg.api_key, "Content-Type": "application/json"},
            json={"secret": self.cfg.api_secret},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["accessToken"]
        self._token_expires = datetime.now() + timedelta(seconds=data.get("expiration", 1200) - 120)
        self._base_url = data["apis"]["reporting"].rstrip("/")

    def _sign(self, method: str, path: str, params: dict[str, str] | None) -> dict[str, str]:
        ts = int(datetime.now().timestamp())
        parts = [method.upper()]
        clean = path.split("/prod/")[-1].lstrip("/") if "/prod/" in path else path.lstrip("/")
        if clean:
            parts.append(quote(clean, safe=""))
        if params:
            qs = "&".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(params.items()))
            if qs:
                parts.append(quote(qs, safe="", encoding="utf-8"))
        parts += [str(ts), "v1"]
        sig = hmac.new(
            self.cfg.signature_key.encode(),
            "&".join(parts).encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "x-inbenta-signature": sig,
            "x-inbenta-signature-version": "v1",
            "x-inbenta-timestamp": str(ts),
        }

    def _get(self, path: str, report_date: date) -> list[dict[str, Any]]:
        self._auth()
        elapsed = time.time() - self._last_call
        if elapsed < 0.075:
            time.sleep(0.075 - elapsed)
        params = {"date_from": report_date.isoformat(), "date_to": report_date.isoformat()}
        if self.cfg.sources:
            params["source"] = json.dumps(self.cfg.sources)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "x-inbenta-key": self.cfg.api_key,
            **self._sign("GET", path, params),
        }
        r = requests.get(f"{self._base_url}{path}", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        self._last_call = time.time()
        return r.json().get("results", [])

    def questions(self, report_date: date) -> list[dict[str, Any]]:
        return self._get("/v1/events/user_questions", report_date)

    def sessions(self, report_date: date) -> list[dict[str, Any]]:
        return self._get("/v1/aggregates/session_details", report_date)


def parse_dt(raw: str, tz: ZoneInfo) -> datetime | None:
    if not raw:
        return None
    try:
        text = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(tz)
    except ValueError:
        return None


def in_day(raw: str, report_date: date, tz: ZoneInfo) -> bool:
    dt = parse_dt(raw, tz)
    if not dt:
        return False
    start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=tz)
    return start <= dt < start + timedelta(days=1)


def yesterday(tz: ZoneInfo) -> date:
    return datetime.now(tz).date() - timedelta(days=1)


def build_rows(cfg: Settings, report_date: date) -> list[SessionRow]:
    client = InbentaClient(cfg)
    log.info("Fetching data for %s", report_date)

    questions = {
        q["event_id"]: {"text": q["user_question"].strip(), "date": q.get("date", "")}
        for q in client.questions(report_date)
        if q.get("event_id") and in_day(q.get("date", ""), report_date, cfg.tz)
        and (q.get("user_question") or "").strip()
    }

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    linked: set[str] = set()

    for session in client.sessions(report_date):
        sid = session.get("session_id") or ""
        for eid in session.get("log_ids") or []:
            linked.add(eid)
            if eid in questions:
                grouped[sid].append(questions[eid])

    for eid, q in questions.items():
        if eid not in linked:
            grouped["No Session"].append(q)

    rows: list[SessionRow] = []
    for sid, qs in grouped.items():
        qs.sort(key=lambda x: x["date"])
        stamps = [t for t in (parse_dt(q["date"], cfg.tz) for q in qs) if t]
        first = min(stamps) if stamps else None
        rows.append(SessionRow(
            session=sid,
            questions="\n".join(f"{i}. {q['text']}" for i, q in enumerate(qs, 1)),
            date=first.strftime("%m/%d/%Y") if first else report_date.strftime("%m/%d/%Y"),
            time=first.strftime("%H:%M:%S") if first else "",
        ))

    rows.sort(key=lambda r: (r.date, r.time, r.session), reverse=True)
    log.info("%d sessions, %d questions", len(rows), len(questions))
    return rows


def write_excel(cfg: Settings, rows: list[SessionRow], report_date: date) -> Path:
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.report_dir / f"merz_daily_{report_date.strftime('%d%m%Y')}.xlsx"
    fills = [
        PatternFill(start_color="D3E3F3", end_color="D3E3F3", fill_type="solid"),
        PatternFill(start_color="E8F4E8", end_color="E8F4E8", fill_type="solid"),
    ]
    headers = ["Session", "Questions", "Date", "Time"]
    widths = [38, 90, 14, 12]

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"

    for i, h in enumerate(headers, 1):
        c = ws.cell(1, i, h)
        c.font = Font(bold=True)

    for ri, row in enumerate(rows, 2):
        fill = fills[(ri - 2) % 2]
        align = Alignment(horizontal="left", vertical="top", wrap_text=True)
        for ci, val in enumerate([row.session, row.questions, row.date, row.time], 1):
            c = ws.cell(ri, ci, val)
            c.fill = fill
            c.alignment = align
        ws.row_dimensions[ri].height = max(20, row.questions.count("\n") * 15 + 15)

    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:D{len(rows) + 1}"

    wb.save(path)
    log.info("Saved %s", path)
    return path


NO_DATA_MSG = "No questions recorded for this date."


def email_body(report_date: date, count: int, filename: str | None) -> tuple[str, str]:
    label = report_date.strftime("%A, %B %d, %Y")
    short = report_date.strftime("%b %d, %Y")
    if count == 0:
        note = NO_DATA_MSG
        banner = (
            f'<p style="margin:0;padding:12px;background:#fff3cd;color:#856404;'
            f'border-left:4px solid #ffc107">{NO_DATA_MSG}</p>'
        )
        attach = ""
    else:
        note = f"Sessions: {count}"
        banner = (
            f'<p style="margin:0;padding:12px;background:#d4edda;color:#155724;'
            f'border-left:4px solid #28a745"><b>{count}</b> session(s). Excel attached.</p>'
        )
        attach = f"<tr><td style='padding:10px;color:#888'>Attachment</td><td style='padding:10px'>{filename}</td></tr>"

    plain = f"Merz Daily Report\n\nDate: {label}\n{note}\n"
    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f6f9;padding:24px">
<table width="600" style="margin:0 auto;background:#fff;border-radius:8px">
<tr><td style="background:#1a3a5c;color:#fff;padding:24px"><h2 style="margin:0">Merz Chatbot</h2>
<p style="margin:4px 0 0;opacity:.8">Daily Report</p></td></tr>
<tr><td style="padding:24px"><p>Summary for <b>{short}</b></p>
<table width="100%" style="border:1px solid #e8ecf0;margin-bottom:16px">
<tr><td style="padding:10px;color:#888">Report Date</td><td style="padding:10px"><b>{label}</b></td></tr>
<tr><td style="padding:10px;color:#888">Sessions</td><td style="padding:10px"><b>{count}</b></td></tr>
{attach}</table>{banner}</td></tr></table></body></html>"""
    return plain, html


def send_mail(cfg: Settings, report_date: date, count: int, path: Path | None = None) -> None:
    if not cfg.email_user or not cfg.email_password:
        raise SystemExit("EMAIL_USER and EMAIL_PASSWORD required in .env")

    if not cfg.email_to:
        raise SystemExit("EMAIL_TO required in .env")

    plain, html = email_body(report_date, count, path.name if path else None)
    msg = MIMEMultipart("mixed")
    subject = f"Merz Daily Report - {report_date.strftime('%b %d, %Y')}"
    if count == 0:
        subject += " - No activity"
    msg["Subject"] = subject
    msg["From"] = cfg.email_user
    msg["To"] = ", ".join(cfg.email_to)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    if path:
        with path.open("rb") as f:
            part = MIMEApplication(f.read(), _subtype="xlsx")
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)

    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context(), timeout=60) as s:
        s.login(cfg.email_user, cfg.email_password)
        s.sendmail(cfg.email_user, cfg.email_to, msg.as_string())
    log.info("Email sent to %s", ", ".join(cfg.email_to))


def run(report_date: date | None = None, send: bool = True) -> Path | None:
    cfg = Settings.load()
    day = report_date or yesterday(cfg.tz)
    rows = build_rows(cfg, day)

    if not rows:
        log.info(NO_DATA_MSG)
        if send:
            send_mail(cfg, day, 0)
        return None

    path = write_excel(cfg, rows, day)
    if send:
        send_mail(cfg, day, len(rows), path)
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--no-email", action="store_true")
    args = p.parse_args()
    day = date.fromisoformat(args.date) if args.date else None
    result = run(day, send=not args.no_email)
    if result:
        print(result)


if __name__ == "__main__":
    main()
