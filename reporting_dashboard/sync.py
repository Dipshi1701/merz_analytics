"""Adverse event detection, question enrichment, and sync orchestration."""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List

from reporting_dashboard.db import RawClick, RawSession, RawUqMatching, RawUserQuestion, UserQuestion, get_session
from reporting_dashboard.ingest import (
    build_session_map, get_content_titles, get_unique_content_ids,
    ingest_from_api, read_raw_questions, upsert_content_title,
)
from reporting_dashboard.api import create_editor_client

logger = logging.getLogger(__name__)

PRODUCT_CONTENT_IDS = {
    30: "Belotero",
    31: "DeScribe",
    32: "Radiesse",
    33: "Ultherapy",
    34: "Xeomin",
}
PRODUCT_NAMES = list(PRODUCT_CONTENT_IDS.values())
_PRODUCT_LOOKUP = {p.lower(): p for p in PRODUCT_NAMES}


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
# Product context resolution
# ---------------------------------------------------------------------------

def _normalize_product(value: str) -> str:
    if not value:
        return ""
    cleaned = str(value).strip()
    if not cleaned:
        return ""
    exact = _PRODUCT_LOOKUP.get(cleaned.lower())
    if exact:
        return exact
    for name in PRODUCT_NAMES:
        if cleaned.lower().startswith(name.lower()):
            return name
    return ""


def product_from_recommendations(recs: List[Dict]) -> str:
    """Extract product from recommendation content_id or title prefix."""
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        cid = rec.get("content_id")
        if cid in PRODUCT_CONTENT_IDS:
            return PRODUCT_CONTENT_IDS[cid]
        title = rec.get("title") or ""
        product = _normalize_product(title)
        if product:
            return product
    return ""


def _parse_product_variable(value_str: str) -> str:
    if not value_str:
        return ""
    try:
        data = json.loads(value_str) if isinstance(value_str, str) else value_str
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for var in data.get("variables", []) or []:
        if not isinstance(var, dict):
            continue
        if str(var.get("name", "")).lower() == "product":
            return _normalize_product(str(var.get("value", "")))
    return ""


def map_products_from_sessions(week_key: str, session_ids: List[str]) -> Dict[str, str]:
    """
    Parse VARIABLES/ACTION_DATA_FIELD + SEARCH per session.

    A product-setting event is normally attached to the SEARCH event(s) that
    follow it in the same session (e.g. the user picks a product from a menu,
    then asks a question). But for single-exchange sessions -- or whenever the
    API happens to log the product event right after the SEARCH instead of
    before it -- there is no earlier event to draw from. To cover that case,
    any SEARCH left untagged by the forward pass is retried against the
    nearest *following* product-setting event in the same session.

    Returns mapping of log_id/event_id -> product name.
    """
    if not session_ids:
        return {}
    db = get_session()
    try:
        events = (db.query(RawSession)
                  .filter(
                      RawSession.week_key == week_key,
                      RawSession.session_id.in_(session_ids),
                      RawSession.key.in_(["VARIABLES", "ACTION_DATA_FIELD", "SEARCH"]),
                  )
                  .order_by(RawSession.date.asc(), RawSession.id.asc())
                  .all())
    finally:
        db.close()

    by_session: Dict[str, List[RawSession]] = {}
    for ev in events:
        by_session.setdefault(ev.session_id or "", []).append(ev)

    log_to_product: Dict[str, str] = {}
    for sid, session_events in by_session.items():
        if not sid:
            continue

        def _tag(ev, product, sid=sid):
            if ev.log_id:
                log_to_product[ev.log_id] = product
            if ev.event_id:
                log_to_product[ev.event_id] = product
            qtext = (ev.user_question or "").strip().lower()
            if qtext:
                log_to_product[f"text::{sid}::{qtext}"] = product

        # Forward pass: product selected before the question (e.g. menu pick).
        current_product = ""
        pending_searches = []
        for ev in session_events:
            key = (ev.key or "").upper()
            if key in ("VARIABLES", "ACTION_DATA_FIELD"):
                parsed = _parse_product_variable(ev.value or "")
                if parsed:
                    current_product = parsed
            elif key == "SEARCH":
                if current_product:
                    _tag(ev, current_product)
                else:
                    pending_searches.append(ev)

        if not pending_searches:
            continue

        # Backward pass: rescue SEARCH events whose product event was logged
        # after them (common for single-exchange sessions).
        pending_ids = {id(ev) for ev in pending_searches}
        upcoming_product = ""
        for ev in reversed(session_events):
            key = (ev.key or "").upper()
            if key in ("VARIABLES", "ACTION_DATA_FIELD"):
                parsed = _parse_product_variable(ev.value or "")
                if parsed:
                    upcoming_product = parsed
            elif key == "SEARCH" and id(ev) in pending_ids and upcoming_product:
                _tag(ev, upcoming_product)

    return log_to_product


def resolve_product_context(records: List[Dict], week_key: str) -> Dict[str, int]:
    """
    Fill product_context on records in-place.
    1) recommendations / content titles
    2) session VARIABLES + SEARCH for remaining
    """
    stats = {"from_recommendations": 0, "from_session_events": 0, "still_missing": 0}
    unresolved: List[Dict] = []

    for rec in records:
        try:
            recs = json.loads(rec.get("recommendations_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            recs = []
        product = product_from_recommendations(recs)
        if product:
            rec["product_context"] = product
            stats["from_recommendations"] += 1
        else:
            unresolved.append(rec)

    session_ids = list({r.get("session_id") for r in unresolved if r.get("session_id")})
    log_to_product = map_products_from_sessions(week_key, session_ids)

    for rec in unresolved:
        product = ""
        event_id = rec.get("event_id") or ""
        log_id = rec.get("log_id") or ""
        sid = rec.get("session_id") or ""
        qtext = (rec.get("user_question") or "").strip().lower()

        if event_id and event_id in log_to_product:
            product = log_to_product[event_id]
        elif log_id and log_id in log_to_product:
            product = log_to_product[log_id]
        elif sid and qtext:
            product = log_to_product.get(f"text::{sid}::{qtext}", "")

        if product:
            rec["product_context"] = product
            stats["from_session_events"] += 1
        else:
            rec["product_context"] = ""
            stats["still_missing"] += 1

    return stats


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
                product_context=rec.get("product_context", ""),
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


def backfill_product_context(start_date: date, end_date: date) -> Dict[str, int]:
    """Fill empty product_context on existing user_questions without re-ingest."""
    db = get_session()
    try:
        rows = (db.query(UserQuestion)
                .filter(
                    UserQuestion.question_date >= start_date,
                    UserQuestion.question_date <= end_date,
                    (UserQuestion.product_context.is_(None)) | (UserQuestion.product_context == ""),
                )
                .all())
        if not rows:
            return {"from_recommendations": 0, "from_session_events": 0, "still_missing": 0, "updated": 0}

        records = []
        for r in rows:
            records.append({
                "event_id": r.event_id,
                "user_question": r.user_question or "",
                "log_id": r.log_id or "",
                "session_id": r.session_id or "",
                "recommendations_json": r.recommendations_json or "[]",
                "product_context": "",
            })
    finally:
        db.close()

    # Use any week_key present in raw_sessions for these session_ids
    week_keys = set()
    session_ids = [r["session_id"] for r in records if r.get("session_id")]
    db = get_session()
    try:
        if session_ids:
            for wk, in (db.query(RawSession.week_key)
                        .filter(RawSession.session_id.in_(session_ids))
                        .distinct().all()):
                if wk:
                    week_keys.add(wk)
        if not week_keys:
            # Fall back: resolve from recommendations only
            week_keys.add("")
    finally:
        db.close()

    combined_stats = {"from_recommendations": 0, "from_session_events": 0, "still_missing": 0}
    # Resolve once using all week keys (merge session maps)
    product_by_event: Dict[str, str] = {}
    for wk in week_keys:
        if not wk:
            continue
        sid_list = list({r["session_id"] for r in records if r.get("session_id")})
        product_by_event.update(map_products_from_sessions(wk, sid_list))

    for rec in records:
        try:
            recs = json.loads(rec.get("recommendations_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            recs = []
        product = product_from_recommendations(recs)
        source = "from_recommendations"
        if not product:
            event_id = rec.get("event_id") or ""
            log_id = rec.get("log_id") or ""
            sid = rec.get("session_id") or ""
            qtext = (rec.get("user_question") or "").strip().lower()
            product = (
                product_by_event.get(event_id)
                or product_by_event.get(log_id)
                or product_by_event.get(f"text::{sid}::{qtext}", "")
            )
            source = "from_session_events" if product else "still_missing"
        rec["product_context"] = product
        combined_stats[source] += 1

    updated = 0
    db = get_session()
    try:
        for rec in records:
            if not rec.get("product_context"):
                continue
            row = db.query(UserQuestion).filter(UserQuestion.event_id == rec["event_id"]).first()
            if row:
                row.product_context = rec["product_context"]
                updated += 1
        db.commit()
    finally:
        db.close()
    combined_stats["updated"] = updated
    return combined_stats


def sync(start_date: date, end_date: date) -> Dict[str, Any]:
    """Always fetch from API and save enriched questions to user_questions."""
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
            "product_context": "",
        })

    product_stats = resolve_product_context(records, week_key)
    q_count = upsert_questions(records)
    backfill_stats = backfill_product_context(start_date, end_date)
    if backfill_stats.get("updated"):
        product_stats = {
            "from_recommendations": (
                product_stats["from_recommendations"] + backfill_stats.get("from_recommendations", 0)
            ),
            "from_session_events": (
                product_stats["from_session_events"] + backfill_stats.get("from_session_events", 0)
            ),
            "still_missing": backfill_stats.get("still_missing", product_stats["still_missing"]),
        }
    day_count = (end_date - start_date).days + 1
    logger.info(
        "product_context: from_recs=%s from_session=%s missing=%s",
        product_stats["from_recommendations"],
        product_stats["from_session_events"],
        product_stats["still_missing"],
    )
    return {
        "fetched_from_api": True,
        "message": f"Fetched {day_count} day(s) from API and saved to database.",
        "questions_synced": q_count,
        "conversations_synced": result["counts"].get("agg_session_details", 0),
        "ingestion_counts": result["counts"],
        "product_stats": product_stats,
    }
