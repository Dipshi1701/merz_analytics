"""API fetch, raw data extraction, EST filtering, and raw MySQL writes."""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.mysql import insert as mysql_insert

from reporting_dashboard.config import ConfigLoader, get_week_key
from reporting_dashboard.db import (
    AggSessionDetail, ContentLookup, IngestionRun, RawClick, RawRating,
    RawSession, RawUqMatching, RawUserQuestion, SurveyAnswer, get_session,
)
from reporting_dashboard.api import get_reporting_client

logger = logging.getLogger(__name__)

_EST = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_WEEK_TABLES = [
    RawUqMatching, RawClick, RawRating, RawSession,
    RawUserQuestion, AggSessionDetail, SurveyAnswer,
]


# ---------------------------------------------------------------------------
# EST date filtering
# ---------------------------------------------------------------------------

def filter_by_est_range(records: List[dict], date_from: datetime, date_to: datetime, date_field: str = "date") -> List[dict]:
    range_start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=_EST)
    exclusive_end = date_to + timedelta(days=1)
    range_end = datetime(exclusive_end.year, exclusive_end.month, exclusive_end.day, tzinfo=_EST)

    kept, dropped = [], 0
    for rec in records:
        raw = rec.get(date_field, "")
        if not raw:
            kept.append(rec)
            continue
        try:
            s = raw.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_UTC)
            if range_start <= dt.astimezone(_EST) < range_end:
                kept.append(rec)
            else:
                dropped += 1
        except (ValueError, AttributeError):
            kept.append(rec)

    if dropped:
        logger.info(f"EST filter: kept {len(kept)}, dropped {dropped} outside {date_from.date()} – {date_to.date()} EST")
    return kept


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def extract_user_questions(questions: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    q_out, m_out = [], []
    for idx, q in enumerate(questions):
        event_id = q.get("event_id") or f"{q.get('date', '')}_{idx}"
        q_out.append({
            "event_id": event_id,
            "user_question": q.get("user_question"),
            "date": q.get("date"),
            "log_id": q.get("log_id"),
            "source": q.get("source"),
        })
        for m in q.get("matchings") or []:
            m_out.append({
                "event_id": event_id,
                "id_content": m.get("id_content"),
                "weight": m.get("weight"),
                "is_external": m.get("external", False),
            })
    return q_out, m_out


def extract_sessions(sessions: List[Dict]) -> List[Dict]:
    return [
        {
            "event_id": s.get("event_id") or f"{s.get('date', '')}_{i}",
            "session_id": s.get("session_id"),
            "key": s.get("key"),
            "value": s.get("value"),
            "user_question": s.get("user_question"),
            "date": s.get("date"),
            "log_id": s.get("log_id"),
        }
        for i, s in enumerate(sessions)
    ]


def extract_clicks(clicks: List[Dict]) -> List[Dict]:
    return [
        {
            "event_id": c.get("event_id") or f"{c.get('date', '')}_{i}",
            "session_id": c.get("session_id"),
            "id_content": c.get("id_content"),
            "click_type": c.get("click_type"),
            "log_id": c.get("log_id"),
            "date": c.get("date"),
        }
        for i, c in enumerate(clicks)
    ]


def extract_ratings(ratings: List[Dict]) -> List[Dict]:
    return [
        {
            "event_id": r.get("event_id") or f"{r.get('date', '')}_{i}",
            "session_id": r.get("session_id"),
            "rating": r.get("rating"),
            "comment": r.get("comment"),
            "log_id": r.get("log_id"),
            "date": r.get("date"),
        }
        for i, r in enumerate(ratings)
    ]


def extract_session_details(details: List[Dict]) -> List[Dict]:
    return [
        {
            "session_id": d.get("session_id"),
            "source": d.get("source"),
            "duration": d.get("duration"),
            "environment": d.get("environment"),
            "date": d.get("date"),
            "log_ids": json.dumps(d["log_ids"]) if d.get("log_ids") else None,
            "ids_contents": json.dumps(d["ids_contents"]) if d.get("ids_contents") else None,
            "data_keys": json.dumps(d["data_keys"]) if d.get("data_keys") else None,
        }
        for d in details
    ]


# ---------------------------------------------------------------------------
# Raw DB writes
# ---------------------------------------------------------------------------

def clear_week(week_key: str) -> None:
    db = get_session()
    try:
        for model in _WEEK_TABLES:
            db.query(model).filter(model.week_key == week_key).delete()
        db.commit()
    finally:
        db.close()


def _record_run(week_key, status, start_time, end_time=None, record_counts="", error_message=""):
    db = get_session()
    try:
        db.add(IngestionRun(
            week_key=week_key, status=status,
            start_time=start_time, end_time=end_time,
            record_counts=record_counts, error_message=error_message,
        ))
        db.commit()
    finally:
        db.close()


def insert_raw_questions(questions: List[Dict], week_key: str) -> int:
    if not questions:
        return 0
    db = get_session()
    try:
        for q in questions:
            stmt = mysql_insert(RawUserQuestion).values(
                event_id=q.get("event_id", ""),
                user_question=q.get("user_question", "") or "",
                date=q.get("date", "") or "",
                log_id=q.get("log_id", "") or "",
                source=str(q.get("source", "") or ""),
                week_key=week_key,
            )
            stmt = stmt.on_duplicate_key_update(
                user_question=stmt.inserted.user_question,
                date=stmt.inserted.date,
                log_id=stmt.inserted.log_id,
                source=stmt.inserted.source,
            )
            db.execute(stmt)
        db.commit()
        return len(questions)
    finally:
        db.close()


def insert_matchings(matchings: List[Dict], week_key: str) -> int:
    if not matchings:
        return 0
    db = get_session()
    try:
        db.bulk_insert_mappings(RawUqMatching, [
            {"event_id": m.get("event_id", ""), "id_content": m.get("id_content"),
             "weight": m.get("weight"), "is_external": bool(m.get("is_external", False)),
             "week_key": week_key}
            for m in matchings
        ])
        db.commit()
        return len(matchings)
    finally:
        db.close()


def insert_raw_sessions(sessions: List[Dict], week_key: str) -> int:
    if not sessions:
        return 0
    db = get_session()
    try:
        db.bulk_insert_mappings(RawSession, [
            {"event_id": s.get("event_id", ""), "session_id": s.get("session_id", "") or "",
             "key": s.get("key", "") or "", "value": s.get("value", "") or "",
             "user_question": s.get("user_question", "") or "", "date": s.get("date", "") or "",
             "log_id": s.get("log_id", "") or "", "week_key": week_key}
            for s in sessions
        ])
        db.commit()
        return len(sessions)
    finally:
        db.close()


def insert_clicks(clicks: List[Dict], week_key: str) -> int:
    if not clicks:
        return 0
    db = get_session()
    try:
        db.bulk_insert_mappings(RawClick, [
            {"event_id": c.get("event_id", ""), "session_id": c.get("session_id", "") or "",
             "id_content": c.get("id_content"), "click_type": c.get("click_type", "") or "",
             "log_id": c.get("log_id", "") or "", "date": c.get("date", "") or "",
             "week_key": week_key}
            for c in clicks
        ])
        db.commit()
        return len(clicks)
    finally:
        db.close()


def insert_ratings(ratings: List[Dict], week_key: str) -> int:
    if not ratings:
        return 0
    db = get_session()
    try:
        db.bulk_insert_mappings(RawRating, [
            {"event_id": r.get("event_id", ""), "session_id": r.get("session_id", "") or "",
             "rating": str(r.get("rating", "") or ""), "comment": r.get("comment", "") or "",
             "log_id": r.get("log_id", "") or "", "date": r.get("date", "") or "",
             "week_key": week_key}
            for r in ratings
        ])
        db.commit()
        return len(ratings)
    finally:
        db.close()


def upsert_session_details(details: List[Dict], week_key: str) -> int:
    if not details:
        return 0
    db = get_session()
    try:
        for d in details:
            li, ic, dk = d.get("log_ids"), d.get("ids_contents"), d.get("data_keys")
            stmt = mysql_insert(AggSessionDetail).values(
                session_id=d.get("session_id", "") or "",
                source=d.get("source", "") or "",
                duration=d.get("duration"),
                environment=d.get("environment", "") or "",
                date=d.get("date", "") or "",
                log_ids=li if isinstance(li, str) else json.dumps(li or []),
                ids_contents=ic if isinstance(ic, str) else json.dumps(ic or []),
                data_keys=dk if isinstance(dk, str) else json.dumps(dk or []),
                week_key=week_key,
            )
            stmt = stmt.on_duplicate_key_update(
                source=stmt.inserted.source, duration=stmt.inserted.duration,
                environment=stmt.inserted.environment, date=stmt.inserted.date,
                log_ids=stmt.inserted.log_ids, ids_contents=stmt.inserted.ids_contents,
                data_keys=stmt.inserted.data_keys,
            )
            db.execute(stmt)
        db.commit()
        return len(details)
    finally:
        db.close()


def upsert_content_title(id_content: int, title: str) -> None:
    db = get_session()
    try:
        stmt = mysql_insert(ContentLookup).values(id_content=id_content, title=title)
        stmt = stmt.on_duplicate_key_update(title=stmt.inserted.title, updated_at=datetime.utcnow())
        db.execute(stmt)
        db.commit()
    finally:
        db.close()


def upsert_survey_answers(answers: List[Dict], week_key: str) -> int:
    if not answers:
        return 0
    db = get_session()
    try:
        for a in answers:
            stmt = mysql_insert(SurveyAnswer).values(
                session_id=a.get("session_id", ""), answer_id=a.get("answer_id", ""),
                solved=a.get("solved", ""), rating=a.get("rating"),
                comment=a.get("comment", ""), date=a.get("date", ""), week_key=week_key,
            )
            stmt = stmt.on_duplicate_key_update(
                solved=stmt.inserted.solved, rating=stmt.inserted.rating, comment=stmt.inserted.comment,
            )
            db.execute(stmt)
        db.commit()
        return len(answers)
    finally:
        db.close()


def get_survey_answer_ids(week_key: str) -> List[Dict]:
    db = get_session()
    try:
        rows = (db.query(RawSession)
                .filter(RawSession.week_key == week_key)
                .filter(RawSession.key == "SURVEY_ANSWER")
                .all())
        return [{"session_id": r.session_id, "answer_id": r.value, "date": r.date} for r in rows if r.value]
    finally:
        db.close()


def get_content_titles() -> Dict[int, str]:
    db = get_session()
    try:
        return {r.id_content: r.title for r in db.query(ContentLookup).all()}
    finally:
        db.close()


def get_unique_content_ids(week_key: str) -> List[int]:
    db = get_session()
    try:
        rows = (db.query(RawUqMatching.id_content)
                .filter(RawUqMatching.week_key == week_key)
                .distinct().all())
        return [r[0] for r in rows if r[0] is not None]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Full ingest pipeline
# ---------------------------------------------------------------------------

def ingest_from_api(start_date: date, end_date: date) -> Dict[str, Any]:
    config = ConfigLoader()
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    week_key = get_week_key(start_dt)

    client = get_reporting_client()
    allowed_sources = config.get_filters_config().get("sources", []) or None

    clear_week(week_key)
    run_start = datetime.utcnow()
    counts: Dict[str, int] = {}

    try:
        questions_raw = filter_by_est_range(client.get_user_questions(start_dt, end_dt, sources=allowed_sources), start_dt, end_dt)
        questions, matchings = extract_user_questions(questions_raw)
        counts["raw_user_questions"] = insert_raw_questions(questions, week_key)
        counts["raw_uq_matchings"] = insert_matchings(matchings, week_key)

        details_raw = filter_by_est_range(client.get_session_details(start_dt, end_dt, sources=allowed_sources), start_dt, end_dt)
        counts["agg_session_details"] = upsert_session_details(extract_session_details(details_raw), week_key)

        sessions_raw = filter_by_est_range(client.get_sessions(start_dt, end_dt, sources=allowed_sources), start_dt, end_dt)
        valid_log_ids = {q["event_id"] for q in questions}
        filtered_sessions = [
            s for s in sessions_raw
            if (s.get("log_id") and s.get("log_id") in valid_log_ids) or s.get("key") == "SURVEY_ANSWER"
        ]
        counts["raw_sessions"] = insert_raw_sessions(extract_sessions(filtered_sessions), week_key)

        clicks_raw = filter_by_est_range(client.get_clicks(start_dt, end_dt, sources=allowed_sources), start_dt, end_dt)
        counts["raw_clicks"] = insert_clicks(extract_clicks(clicks_raw), week_key)

        ratings_raw = filter_by_est_range(client.get_ratings(start_dt, end_dt, sources=allowed_sources), start_dt, end_dt)
        counts["raw_ratings"] = insert_ratings(extract_ratings(ratings_raw), week_key)

        counts["survey_answers"] = _ingest_survey_answers(client, week_key)

        _record_run(week_key, "completed", run_start, datetime.utcnow(), json.dumps(counts))
        return {"week_key": week_key, "counts": counts}

    except Exception as exc:
        _record_run(week_key, "failed", run_start, datetime.utcnow(), error_message=str(exc))
        raise


def _ingest_survey_answers(client, week_key: str) -> int:
    survey_events = get_survey_answer_ids(week_key)
    if not survey_events:
        return 0
    parsed = []
    for event in survey_events:
        try:
            result = client.get_survey_answer(event["answer_id"])
            answers = {a["question"]: a["value"] for a in result.get("answers", [])}
            parsed.append({
                "session_id": event["session_id"],
                "answer_id": event["answer_id"],
                "solved": answers.get("2", ""),
                "rating": int(answers["3"]) if answers.get("3", "").isdigit() else None,
                "comment": answers.get("4", ""),
                "date": event["date"],
            })
        except Exception as e:
            logger.warning(f"Failed to fetch survey answer {event['answer_id']}: {e}")
    return upsert_survey_answers(parsed, week_key)


def build_session_map(week_key: str) -> Dict[str, Any]:
    db = get_session()
    try:
        session_map = {}
        for row in db.query(AggSessionDetail).filter(AggSessionDetail.week_key == week_key).all():
            if not row.log_ids:
                continue
            user_type = _derive_user_type(row.ids_contents or "")
            try:
                for log_id in json.loads(row.log_ids):
                    session_map[log_id] = {"session_id": row.session_id, "user_type": user_type}
            except (json.JSONDecodeError, TypeError):
                pass
        return session_map
    finally:
        db.close()


def _derive_user_type(ids_contents_json: str) -> str:
    try:
        ids = json.loads(ids_contents_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    user_type = ""
    for id_content in ids:
        if id_content == 28:
            user_type = "HCP"
        elif id_content == 27:
            user_type = "CONSUMER"
    return user_type


def read_raw_questions(week_key: str) -> List[Dict]:
    db = get_session()
    try:
        rows = db.query(RawUserQuestion).filter(RawUserQuestion.week_key == week_key).all()
        return [{"event_id": r.event_id, "user_question": r.user_question, "date": r.date,
                 "log_id": r.log_id, "source": r.source} for r in rows]
    finally:
        db.close()
