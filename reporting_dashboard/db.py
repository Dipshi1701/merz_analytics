"""SQLAlchemy engine, session, Base, and all table models."""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Date, Float, Index,
    Integer, String, Text, UniqueConstraint, create_engine, inspect, text,
)
from sqlalchemy.orm import sessionmaker, declarative_base

from reporting_dashboard.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_session():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RawUserQuestion(Base):
    __tablename__ = "raw_user_questions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), nullable=False)
    user_question = Column(Text, default="")
    date = Column(String(50), default="")
    log_id = Column(String(255), default="")
    source = Column(String(255), default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("event_id", "week_key", name="uq_raw_uq_event_week"),)


class RawUqMatching(Base):
    __tablename__ = "raw_uq_matchings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), nullable=False)
    id_content = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)
    is_external = Column(Boolean, default=False)
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RawSession(Base):
    __tablename__ = "raw_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), nullable=False, index=True)
    session_id = Column(String(255), default="", index=True)
    key = Column(String(100), default="")
    value = Column(Text, default="")
    user_question = Column(Text, default="")
    date = Column(String(50), default="")
    log_id = Column(String(255), default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RawClick(Base):
    __tablename__ = "raw_clicks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), nullable=False)
    session_id = Column(String(255), default="")
    id_content = Column(Integer, nullable=True)
    click_type = Column(String(50), default="")
    log_id = Column(String(255), default="")
    date = Column(String(50), default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_raw_clicks_log_content", "log_id", "id_content"),)


class RawRating(Base):
    __tablename__ = "raw_ratings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), nullable=False)
    session_id = Column(String(255), default="")
    rating = Column(String(20), default="")
    comment = Column(Text, default="")
    log_id = Column(String(255), default="")
    date = Column(String(50), default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AggSessionDetail(Base):
    __tablename__ = "agg_session_details"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    source = Column(String(255), default="")
    duration = Column(Integer, nullable=True)
    environment = Column(String(50), default="")
    date = Column(String(50), default="")
    log_ids = Column(Text, default="")
    ids_contents = Column(Text, default="")
    data_keys = Column(Text, default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("session_id", "week_key", name="uq_agg_session_week"),)


class ContentLookup(Base):
    __tablename__ = "content_lookup"
    id_content = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)
    last_updated = Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    week_key = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    record_counts = Column(Text, default="")
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    answer_id = Column(String(255), nullable=False)
    solved = Column(String(10), default="")
    rating = Column(Integer, nullable=True)
    comment = Column(Text, default="")
    date = Column(String(50), default="")
    week_key = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("answer_id", "week_key", name="uq_survey_answer_week"),)


class UserQuestion(Base):
    __tablename__ = "user_questions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    user_question = Column(Text, default="")
    question_date = Column(Date, index=True)
    log_id = Column(String(255), default="")
    source = Column(String(255), default="")
    session_id = Column(String(255), default="", index=True)
    adverse_event = Column(Boolean, default=False, index=True)
    adverse_reason = Column(String(255), default="")
    user_type = Column(String(20), default="", index=True)
    recommendations_json = Column(Text, default="")
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

_WEEK_TABLES = [
    RawUqMatching, RawClick, RawRating, RawSession,
    RawUserQuestion, AggSessionDetail, SurveyAnswer,
]


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns():
    inspector = inspect(engine)
    if "user_questions" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("user_questions")}
        if "recommendations_json" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE user_questions ADD COLUMN recommendations_json TEXT"
                ))
        if "user_type" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE user_questions ADD COLUMN user_type VARCHAR(20) DEFAULT ''"
                ))
