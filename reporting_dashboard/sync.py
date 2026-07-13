"""Adverse event detection, question enrichment, and sync orchestration."""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from reporting_dashboard.config import REFRESH_DAYS, get_week_key
from reporting_dashboard.db import RawClick, RawUqMatching, RawUserQuestion, UserQuestion, get_session
from reporting_dashboard.ingest import (
    build_session_map, get_content_titles, get_unique_content_ids,
    ingest_from_api, read_raw_questions, upsert_content_title,
)
from reporting_dashboard.api import create_editor_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adverse event detection
# ---------------------------------------------------------------------------

_ADVERSE_KEYWORDS = {
    "high": ["emergency", "hospitalization", "hospital", "allergic reaction", "anaphylaxis", "severe reaction"],
    "medium": ["side effect", "rash", "vomiting", "swelling", "pain", "nausea", "dizziness", "bleeding", "infection"],
    "low": ["complaint", "unsafe", "reaction", "adverse", "hurt", "burning", "itching", "redness"],
}


def check_adverse_event(question_text: str):
    """Return (is_adverse: bool, reason: str)."""
    if not question_text:
        return False, ""
    text = question_text.lower()
    for severity, keywords in _ADVERSE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return True, f"{severity}: {keyword}"
    return False, ""


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _was_clicked(event_id: str, id_content: int, week_key: str) -> bool:
    db = get_session()
    try:
        return db.query(RawClick).filter(
            RawClick.log_id == event_id,
            RawClick.id_content == id_content,
            RawClick.week_key == week_key,
        ).count() > 0
    finally:
        db.close()


def _resolve_titles(week_key: str, editor_client=None) -> Dict[int, str]:
    titles = get_content_titles()
    content_ids = get_unique_content_ids(week_key)
    to_fetch = [cid for cid in content_ids if cid not in titles or str(titles[cid]).startswith("Content ")]

    if editor_client and to_fetch:
        batch = editor_client.get_contents_batch(to_fetch)
        for cid in to_fetch:
            title = batch[cid].get("title", f"Content {cid}") if cid in batch else f"Content {cid}"
            upsert_content_title(cid, title)
            titles[cid] = title
    else:
        for cid in content_ids:
            titles.setdefault(cid, f"Content {cid}")
    return titles


def _get_recommendations(event_id: str, week_key: str, titles: Dict[int, str]) -> List[Dict]:
    db = get_session()
    try:
        matchings = (db.query(RawUqMatching)
                     .filter(RawUqMatching.event_id == event_id, RawUqMatching.week_key == week_key)
                     .order_by(RawUqMatching.weight.desc())
                     .all())
        return [
            {
                "content_id": m.id_content,
                "title": titles.get(m.id_content, f"Content {m.id_content}"),
                "clicked": _was_clicked(event_id, m.id_content, week_key),
                "external": bool(m.is_external),
            }
            for m in matchings
        ]
    finally:
        db.close()


def enrich_all_questions(week_key: str, editor_client=None) -> Dict[str, List[Dict]]:
    if editor_client is None:
        editor_client = create_editor_client()
    titles = _resolve_titles(week_key, editor_client)
    db = get_session()
    try:
        event_ids = [r.event_id for r in db.query(RawUserQuestion.event_id).filter(RawUserQuestion.week_key == week_key).all()]
    finally:
        db.close()
    return {eid: _get_recommendations(eid, week_key, titles) for eid in event_ids}


# ---------------------------------------------------------------------------
# Upsert enriched questions (dashboard table)
# ---------------------------------------------------------------------------

def upsert_questions(records: List[Dict]) -> int:
    if not records:
        return 0
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    db = get_session()
    try:
        for rec in records:
            stmt = mysql_insert(UserQuestion).values(**rec)
            stmt = stmt.on_duplicate_key_update(
                user_question=rec.get("user_question", ""),
                question_date=rec.get("question_date"),
                log_id=rec.get("log_id", ""),
                source=rec.get("source", ""),
                session_id=rec.get("session_id", ""),
                adverse_event=rec.get("adverse_event", False),
                adverse_reason=rec.get("adverse_reason", ""),
                user_type=rec.get("user_type", ""),
                recommendations_json=rec.get("recommendations_json", ""),
                synced_at=datetime.utcnow(),
            )
            db.execute(stmt)
        db.commit()
        return len(records)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------

def _get_existing_dates(start: date, end: date) -> set:
    db = get_session()
    try:
        rows = (db.query(UserQuestion.question_date)
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .distinct().all())
        return {r[0] for r in rows if r[0]}
    finally:
        db.close()


def _parse_date(raw_date: str):
    if not raw_date:
        return None
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return None


def sync(start_date: date, end_date: date) -> Dict[str, Any]:
    """Fetch from API if needed and save enriched questions to user_questions."""
    all_dates = []
    current = start_date
    while current <= end_date:
        all_dates.append(current)
        current += timedelta(days=1)

    existing = _get_existing_dates(start_date, end_date)
    refresh_cutoff = date.today() - timedelta(days=REFRESH_DAYS)
    dates_needed = [d for d in all_dates if d not in existing or d >= refresh_cutoff]

    if not dates_needed:
        return {
            "fetched_from_api": False,
            "message": "Data already in database. Showing cached results.",
            "questions_synced": 0,
            "conversations_synced": 0,
        }

    result = ingest_from_api(start_date, end_date)
    week_key = result["week_key"]

    raw_questions = read_raw_questions(week_key)
    session_map = build_session_map(week_key)
    editor_client = create_editor_client()
    enrichment_map = enrich_all_questions(week_key, editor_client)

    records = []
    for row in raw_questions:
        event_id = row.get("event_id", "")
        question_text = row.get("user_question", "") or ""
        is_adverse, reason = check_adverse_event(question_text)
        log_id = row.get("log_id", "") or ""
        session_info = session_map.get(event_id) or session_map.get(log_id) or {}
        session_id = session_info.get("session_id", "") if isinstance(session_info, dict) else session_info
        user_type = session_info.get("user_type", "") if isinstance(session_info, dict) else ""
        q_date = _parse_date(row.get("date", ""))
        if not q_date or not (start_date <= q_date <= end_date):
            continue
        records.append({
            "event_id": event_id,
            "user_question": question_text,
            "question_date": q_date,
            "log_id": log_id,
            "source": str(row.get("source", "") or ""),
            "session_id": session_id,
            "adverse_event": is_adverse,
            "adverse_reason": reason,
            "user_type": user_type,
            "recommendations_json": json.dumps(enrichment_map.get(event_id, [])),
        })

    q_count = upsert_questions(records)
    return {
        "fetched_from_api": True,
        "message": f"Fetched {len(dates_needed)} day(s) from API and saved to database.",
        "questions_synced": q_count,
        "conversations_synced": result["counts"].get("agg_session_details", 0),
        "ingestion_counts": result["counts"],
    }
