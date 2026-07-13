"""Dashboard data queries and analytics computations."""

import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import func

from reporting_dashboard.db import AggSessionDetail, UserQuestion, SurveyAnswer, get_session

logger = logging.getLogger(__name__)

PRODUCT_CONTENT_IDS = {
    30: "Belotero",
    31: "DeScribe",
    32: "Radiesse",
    33: "Ultherapy",
    34: "Xeomin",
}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_questions(start: date, end: date, search: str = "") -> List[Dict]:
    db = get_session()
    try:
        q = (db.query(UserQuestion)
             .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
             .order_by(UserQuestion.question_date.desc()))
        if search:
            q = q.filter(UserQuestion.user_question.like(f"%{search}%"))
        results = []
        for r in q.all():
            recommendations = []
            if r.recommendations_json:
                try:
                    recommendations = json.loads(r.recommendations_json)
                except json.JSONDecodeError:
                    pass
            results.append({
                "date": r.question_date.strftime("%Y-%m-%d") if r.question_date else "",
                "question": r.user_question or "",
                "source": r.source or "",
                "session_id": r.session_id or "",
                "adverse_event": r.adverse_event,
                "adverse_reason": r.adverse_reason or "",
                "recommendations": recommendations,
            })
        return results
    finally:
        db.close()


def get_adverse_events(start: date, end: date) -> List[Dict]:
    db = get_session()
    try:
        rows = (db.query(UserQuestion)
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .filter(UserQuestion.adverse_event.is_(True))
                .order_by(UserQuestion.question_date.desc())
                .all())
        return [{"date": r.question_date.strftime("%Y-%m-%d") if r.question_date else "",
                 "question": r.user_question or "", "reason": r.adverse_reason or "",
                 "source": r.source or ""} for r in rows]
    finally:
        db.close()


def get_summary(start: date, end: date) -> Dict[str, int]:
    db = get_session()
    try:
        def _count(q):
            return q.count()

        base = db.query(UserQuestion).filter(
            UserQuestion.question_date >= start, UserQuestion.question_date <= end)

        return {
            "total_questions": base.count(),
            "total_conversations": (db.query(UserQuestion.session_id)
                                    .filter(UserQuestion.question_date >= start,
                                            UserQuestion.question_date <= end,
                                            UserQuestion.session_id != "")
                                    .distinct().count()),
            "total_adverse_events": base.filter(UserQuestion.adverse_event.is_(True)).count(),
            "hcp_sessions": (db.query(UserQuestion.session_id)
                             .filter(UserQuestion.question_date >= start,
                                     UserQuestion.question_date <= end,
                                     UserQuestion.session_id != "",
                                     UserQuestion.user_type == "HCP")
                             .distinct().count()),
            "consumer_sessions": (db.query(UserQuestion.session_id)
                                  .filter(UserQuestion.question_date >= start,
                                          UserQuestion.question_date <= end,
                                          UserQuestion.session_id != "",
                                          UserQuestion.user_type == "CONSUMER")
                                  .distinct().count()),
        }
    finally:
        db.close()


def get_questions_per_day(start: date, end: date) -> List[Dict]:
    db = get_session()
    try:
        rows = (db.query(UserQuestion.question_date, func.count(UserQuestion.id))
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .group_by(UserQuestion.question_date)
                .order_by(UserQuestion.question_date)
                .all())
        return [{"date": r[0].strftime("%Y-%m-%d"), "count": r[1]} for r in rows if r[0]]
    finally:
        db.close()


def get_adverse_by_severity(start: date, end: date) -> Dict[str, int]:
    """Count adverse events by severity (parsed from adverse_reason, e.g. 'high: keyword')."""
    db = get_session()
    try:
        rows = (db.query(UserQuestion.adverse_reason)
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .filter(UserQuestion.adverse_event.is_(True))
                .all())
        counts = {"high": 0, "medium": 0, "low": 0}
        for (reason,) in rows:
            if not reason:
                continue
            severity = reason.split(":", 1)[0].strip().lower()
            if severity in counts:
                counts[severity] += 1
        return counts
    finally:
        db.close()


def get_adverse_per_day(start: date, end: date) -> List[Dict]:
    db = get_session()
    try:
        rows = (db.query(UserQuestion.question_date, func.count(UserQuestion.id))
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .filter(UserQuestion.adverse_event.is_(True))
                .group_by(UserQuestion.question_date)
                .order_by(UserQuestion.question_date)
                .all())
        return [{"date": r[0].strftime("%Y-%m-%d"), "count": r[1]} for r in rows if r[0]]
    finally:
        db.close()


def get_top_questions(start: date, end: date, limit: int = 10) -> List[Dict]:
    db = get_session()
    try:
        rows = (db.query(UserQuestion.user_question, func.count(UserQuestion.id))
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .filter(UserQuestion.user_question != "")
                .group_by(UserQuestion.user_question)
                .order_by(func.count(UserQuestion.id).desc())
                .limit(limit)
                .all())
        return [{"question": r[0], "count": r[1]} for r in rows]
    finally:
        db.close()


def get_questions_by_session(start: date, end: date, search: str = "") -> List[Dict]:
    questions = get_questions(start, end, search=search)
    sessions = {}
    for q in questions:
        sid = q.get("session_id") or "No Session"
        if sid not in sessions:
            sessions[sid] = {"session_id": sid, "source": q.get("source", ""),
                             "date": q.get("date", ""), "questions": []}
        sessions[sid]["questions"].append({
            "date": q.get("date", ""),
            "question": q.get("question", ""),
            "recommendations": [
                {"title": r.get("title", ""), "clicked": bool(r.get("clicked")), "external": bool(r.get("external"))}
                for r in q.get("recommendations", []) if r.get("title")
            ],
        })
        if q.get("date") and (not sessions[sid]["date"] or q["date"] < sessions[sid]["date"]):
            sessions[sid]["date"] = q["date"]
    result = list(sessions.values())
    result.sort(key=lambda s: (s["date"], s["session_id"]), reverse=True)
    return result


def _survey_date_in_range(raw_date: str, start: date, end: date) -> bool:
    """Return True if the survey event date falls within [start, end] (inclusive)."""
    if not raw_date:
        return False
    try:
        s = raw_date.strip().replace("Z", "+00:00")
        event_date = datetime.fromisoformat(s).date()
    except (ValueError, AttributeError):
        try:
            event_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return False
    return start <= event_date <= end


def get_survey_results(start: date, end: date) -> List[Dict]:
    """Return survey responses in date range, one row per answer_id (deduped across week_keys)."""
    db = get_session()
    try:
        rows = db.query(SurveyAnswer).all()
        seen_answer_ids: set = set()
        results = []
        for r in rows:
            if not r.answer_id or r.answer_id in seen_answer_ids:
                continue
            if not _survey_date_in_range(r.date, start, end):
                continue
            seen_answer_ids.add(r.answer_id)
            results.append({
                "session_id": r.session_id,
                "solved": r.solved,
                "rating": r.rating,
                "comment": r.comment,
                "date": r.date[:10] if r.date else "",
            })
        return results
    finally:
        db.close()


def get_survey_by_session(start: date, end: date) -> Dict[str, Any]:
    return {r["session_id"]: r for r in get_survey_results(start, end)}


def get_product_interactions(start: date, end: date) -> List[Dict]:
    """Count unique sessions per product based on recommendation titles in user_questions."""
    db = get_session()
    try:
        rows = (db.query(UserQuestion)
                .filter(UserQuestion.question_date >= start, UserQuestion.question_date <= end)
                .all())
        # product name -> set of session_ids
        product_sessions: Dict[str, set] = {}
        product_names = list(PRODUCT_CONTENT_IDS.values())
        for row in rows:
            if not row.recommendations_json or not row.session_id:
                continue
            try:
                recs = json.loads(row.recommendations_json)
            except (json.JSONDecodeError, TypeError):
                continue
            products_in_question = set()
            for rec in recs:
                title = rec.get("title", "") if isinstance(rec, dict) else str(rec)
                for product in product_names:
                    if title.lower().startswith(product.lower()):
                        products_in_question.add(product)
            for product in products_in_question:
                product_sessions.setdefault(product, set()).add(row.session_id)
        return [
            {"product": p, "sessions": len(sids)}
            for p, sids in sorted(product_sessions.items(), key=lambda x: -len(x[1]))
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def get_insights(start: date, end: date) -> Dict[str, Any]:
    questions = get_questions(start, end)
    summary = get_summary(start, end)

    total_questions = summary["total_questions"]
    total_sessions = summary["total_conversations"]
    total_adverse = summary["total_adverse_events"]

    total_recommendations = total_clicks = questions_with_clicks = 0
    content_click_counts: Counter = Counter()
    content_impression_counts: Counter = Counter()
    session_clicks: Dict[str, bool] = {}

    for q in questions:
        recs = q.get("recommendations", [])
        total_recommendations += len(recs)
        clicked_in_q = False
        for rec in recs:
            title = rec.get("title", "Unknown")
            content_impression_counts[title] += 1
            if rec.get("clicked"):
                total_clicks += 1
                clicked_in_q = True
                content_click_counts[title] += 1
        if clicked_in_q:
            questions_with_clicks += 1
        sid = q.get("session_id") or "no_session"
        had_click = any(r.get("clicked") for r in recs)
        session_clicks[sid] = session_clicks.get(sid, False) or had_click

    sessions_with_no_click = sum(1 for v in session_clicks.values() if not v)
    total_sessions_counted = len(session_clicks)

    # Daily trend
    daily_raw = get_questions_per_day(start, end)
    daily_map = {d["date"]: d["count"] for d in daily_raw}
    daily_trend = []
    current = start
    while current <= end:
        key = current.strftime("%Y-%m-%d")
        daily_trend.append({"date": key, "questions": daily_map.get(key, 0)})
        current += timedelta(days=1)

    peak_day = max(daily_trend, key=lambda x: x["questions"]) if daily_trend else {"date": "—", "questions": 0}

    adverse_raw = get_adverse_per_day(start, end)
    adverse_map = {d["date"]: d["count"] for d in adverse_raw}
    adverse_trend = [{"date": d["date"], "adverse_events": adverse_map.get(d["date"], 0)} for d in daily_trend]

    survey_rows = get_survey_results(start, end)
    survey_total = len(survey_rows)
    solved_count = sum(1 for r in survey_rows if r["solved"].lower() == "yes")
    ratings = [r["rating"] for r in survey_rows if r["rating"] is not None]
    EXCLUDED_CONTENT = {"closing - patient"}
    content_volume = [
        {"title": t, "impressions": imp, "clicks": content_click_counts.get(t, 0),
         "ctr": round(content_click_counts.get(t, 0) / imp * 100, 1) if imp else 0}
        for t, imp in content_impression_counts.most_common(15)
        if t.strip().lower() not in EXCLUDED_CONTENT
    ]
    days_in_range = (end - start).days + 1
    adverse_severity = get_adverse_by_severity(start, end)

    return {
        "metrics": {
            "total_questions": total_questions,
            "total_sessions": total_sessions,
            "total_adverse": total_adverse,
            "adverse_high": adverse_severity["high"],
            "adverse_medium": adverse_severity["medium"],
            "adverse_low": adverse_severity["low"],
            "avg_per_session": round(total_questions / total_sessions, 1) if total_sessions else 0,
            "avg_per_day": round(total_questions / days_in_range, 1) if days_in_range else 0,
            "click_rate": round(questions_with_clicks / total_questions * 100, 1) if total_questions else 0,
            "content_ctr": round(total_clicks / total_recommendations * 100, 1) if total_recommendations else 0,
            "total_clicks": total_clicks,
            "questions_with_clicks": questions_with_clicks,
            "peak_day": peak_day["date"],
            "peak_day_count": peak_day["questions"],
            "zero_click_session_rate": round(sessions_with_no_click / total_sessions_counted * 100, 1) if total_sessions_counted else 0,
            "sessions_with_no_click": sessions_with_no_click,
            "hcp_sessions": summary["hcp_sessions"],
            "consumer_sessions": summary["consumer_sessions"],
        },
        "daily_trend": daily_trend,
        "adverse_trend": adverse_trend,
        "top_questions": get_top_questions(start, end, limit=8),
        "top_clicked_content": [
            {"content": t, "clicks": c}
            for t, c in content_click_counts.most_common(8)
            if t.strip().lower() not in EXCLUDED_CONTENT
        ],
        "content_volume": content_volume,
        "product_interactions": get_product_interactions(start, end),
        "survey": {
            "total": survey_total,
            "solved_pct": round(solved_count / survey_total * 100, 1) if survey_total else 0,
            "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
            "rating_dist": [{"rating": i, "count": ratings.count(i)} for i in range(1, 6)],
        },
    }
