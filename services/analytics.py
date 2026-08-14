"""Lightweight product analytics for PLC Intelligence.

Production:
    Set [analytics].database_url in Streamlit Secrets to a PostgreSQL URL.

Local development:
    If no PostgreSQL URL is configured, analytics falls back to
    data/plc_analytics.db so the feature can be tested immediately.

Analytics failures are intentionally non-fatal. Telemetry must never prevent a
teacher from using the instructional application.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
from urllib.parse import urlparse
import uuid

import streamlit as st

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # Local development can work before psycopg is installed.
    psycopg = None
    dict_row = None


LOCAL_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "plc_analytics.db"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _analytics_secret(name: str, default: str | None = None) -> str | None:
    try:
        value = st.secrets["analytics"][name]
        return str(value) if value is not None else default
    except Exception:
        return os.getenv(f"PLC_ANALYTICS_{name.upper()}", default)


def database_url() -> str | None:
    value = _analytics_secret("database_url")
    return value.strip() if value and value.strip() else None


def backend_name() -> str:
    return "PostgreSQL" if database_url() else "Local SQLite"


def is_persistent_backend() -> bool:
    return bool(database_url())


def _connect():
    url = database_url()

    if url:
        if psycopg is None:
            raise RuntimeError(
                'PostgreSQL analytics is configured but psycopg is not installed. '
                'Add "psycopg[binary]>=3.3,<4" to requirements.txt.'
            )
        return psycopg.connect(
            url,
            row_factory=dict_row,
            connect_timeout=5,
        )

    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LOCAL_DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _sql(statement: str) -> str:
    """Convert qmark placeholders to PostgreSQL placeholders when needed."""
    if database_url():
        return statement.replace("?", "%s")
    return statement


def initialize_analytics() -> None:
    """Create analytics tables if needed."""
    if database_url():
        ddl = """
        CREATE TABLE IF NOT EXISTS analytics_sessions (
            session_id TEXT PRIMARY KEY,
            tester_name TEXT,
            tester_email TEXT,
            demo_user_id BIGINT,
            demo_display_name TEXT,
            demo_role TEXT,
            started_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            entry_url TEXT,
            locale TEXT,
            timezone TEXT,
            user_agent TEXT,
            ip_hash TEXT
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            event_id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL
                REFERENCES analytics_sessions(session_id) ON DELETE CASCADE,
            demo_user_id BIGINT,
            event_type TEXT NOT NULL,
            page_name TEXT,
            entity_type TEXT,
            entity_id TEXT,
            duration_ms DOUBLE PRECISION,
            metadata_json TEXT,
            created_at TIMESTAMPTZ NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_analytics_events_created
            ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_events_type
            ON analytics_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_events_session
            ON analytics_events(session_id, created_at);
        """
    else:
        ddl = """
        CREATE TABLE IF NOT EXISTS analytics_sessions (
            session_id TEXT PRIMARY KEY,
            tester_name TEXT,
            tester_email TEXT,
            demo_user_id INTEGER,
            demo_display_name TEXT,
            demo_role TEXT,
            started_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            entry_url TEXT,
            locale TEXT,
            timezone TEXT,
            user_agent TEXT,
            ip_hash TEXT
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL
                REFERENCES analytics_sessions(session_id) ON DELETE CASCADE,
            demo_user_id INTEGER,
            event_type TEXT NOT NULL,
            page_name TEXT,
            entity_type TEXT,
            entity_id TEXT,
            duration_ms REAL,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_analytics_events_created
            ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_events_type
            ON analytics_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_events_session
            ON analytics_events(session_id, created_at);
        """

    with _connect() as connection:
        if database_url():
            with connection.cursor() as cursor:
                cursor.execute(ddl)
        else:
            connection.executescript(ddl)


def _context_value(name: str) -> str | None:
    try:
        value = getattr(st.context, name)
        return str(value) if value is not None else None
    except Exception:
        return None


def _user_agent() -> str | None:
    try:
        return st.context.headers.get("User-Agent")
    except Exception:
        return None


def _ip_hash() -> str | None:
    try:
        ip = st.context.ip_address
    except Exception:
        ip = None

    if not ip:
        return None

    salt = _analytics_secret("ip_hash_salt", "plc-intelligence-demo")
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


def _insert_session(session_id: str) -> None:
    now = _utcnow()
    entry_url = _context_value("url")

    with _connect() as connection:
        cursor = connection.cursor() if database_url() else connection

        if database_url():
            cursor.execute(
                _sql(
                    """
                    INSERT INTO analytics_sessions (
                        session_id, started_at, last_seen_at, entry_url,
                        locale, timezone, user_agent, ip_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (session_id) DO NOTHING
                    """
                ),
                (
                    session_id,
                    now,
                    now,
                    entry_url,
                    _context_value("locale"),
                    _context_value("timezone"),
                    _user_agent(),
                    _ip_hash(),
                ),
            )
        else:
            cursor.execute(
                """
                INSERT OR IGNORE INTO analytics_sessions (
                    session_id, started_at, last_seen_at, entry_url,
                    locale, timezone, user_agent, ip_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    now.isoformat(),
                    now.isoformat(),
                    entry_url,
                    _context_value("locale"),
                    _context_value("timezone"),
                    _user_agent(),
                    _ip_hash(),
                ),
            )

        if database_url():
            cursor.close()


def ensure_session() -> str:
    """Return the current browser-tab analytics session ID."""
    session_id = st.session_state.get("_analytics_session_id")
    if session_id:
        return str(session_id)

    session_id = str(uuid.uuid4())
    st.session_state["_analytics_session_id"] = session_id

    try:
        initialize_analytics()
        _insert_session(session_id)
    except Exception as error:
        st.session_state["_analytics_error"] = str(error)

    return session_id


def identify_session(
    *,
    tester_name: str | None,
    tester_email: str | None,
    current_user: dict[str, Any] | None,
) -> None:
    """Attach tester identity and the selected demo persona to a session."""
    session_id = ensure_session()
    now = _utcnow()
    current_user = current_user or {}

    try:
        with _connect() as connection:
            cursor = connection.cursor() if database_url() else connection
            cursor.execute(
                _sql(
                    """
                    UPDATE analytics_sessions
                    SET tester_name = ?,
                        tester_email = ?,
                        demo_user_id = ?,
                        demo_display_name = ?,
                        demo_role = ?,
                        last_seen_at = ?
                    WHERE session_id = ?
                    """
                ),
                (
                    (tester_name or "").strip() or None,
                    (tester_email or "").strip().lower() or None,
                    current_user.get("user_id"),
                    current_user.get("display_name"),
                    current_user.get("role"),
                    now if database_url() else now.isoformat(),
                    session_id,
                ),
            )
            if database_url():
                cursor.close()
    except Exception as error:
        st.session_state["_analytics_error"] = str(error)


def _touch_session(
    session_id: str,
    current_user: dict[str, Any] | None = None,
) -> None:
    now = _utcnow()
    current_user = current_user or {}

    with _connect() as connection:
        cursor = connection.cursor() if database_url() else connection
        cursor.execute(
            _sql(
                """
                UPDATE analytics_sessions
                SET last_seen_at = ?,
                    demo_user_id = COALESCE(?, demo_user_id),
                    demo_display_name = COALESCE(?, demo_display_name),
                    demo_role = COALESCE(?, demo_role)
                WHERE session_id = ?
                """
            ),
            (
                now if database_url() else now.isoformat(),
                current_user.get("user_id"),
                current_user.get("display_name"),
                current_user.get("role"),
                session_id,
            ),
        )
        if database_url():
            cursor.close()


def track_event(
    event_type: str,
    *,
    current_user: dict[str, Any] | None = None,
    page_name: str | None = None,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Record one analytics event. Returns False instead of breaking the app."""
    try:
        initialize_analytics()
        session_id = ensure_session()
        _touch_session(session_id, current_user)

        now = _utcnow()
        current_user = current_user or {}
        payload = json.dumps(
            metadata or {},
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

        with _connect() as connection:
            cursor = connection.cursor() if database_url() else connection
            cursor.execute(
                _sql(
                    """
                    INSERT INTO analytics_events (
                        session_id, demo_user_id, event_type, page_name,
                        entity_type, entity_id, duration_ms,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    session_id,
                    current_user.get("user_id"),
                    event_type.strip(),
                    page_name,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    float(duration_ms) if duration_ms is not None else None,
                    payload,
                    now if database_url() else now.isoformat(),
                ),
            )
            if database_url():
                cursor.close()

        return True
    except Exception as error:
        st.session_state["_analytics_error"] = str(error)
        return False


_PAGE_LABELS = {
    "/": "Dashboard",
    "/dashboard": "Dashboard",
    "/plc-cycles": "PLC Cycles",
    "/plc_cycles": "PLC Cycles",
    "/assessments": "Assessments",
    "/cfa-results": "CFA Results",
    "/cfa_results": "CFA Results",
    "/cfa-entry": "CFA Data Entry",
    "/standards": "Standards",
    "/reports": "Reports",
    "/resources": "Resources",
    "/settings": "Settings",
    "/product-analytics": "Product Analytics",
    "/product_analytics": "Product Analytics",
}


def current_page_name() -> str:
    try:
        url = str(st.context.url)
        path = urlparse(url).path.rstrip("/") or "/"
    except Exception:
        path = "/"

    return _PAGE_LABELS.get(path, path.strip("/").replace("-", " ").title() or "Dashboard")


def track_page_from_context(
    current_user: dict[str, Any] | None,
) -> None:
    """Track navigation without counting every Streamlit rerun as a page view."""
    page_name = current_page_name()
    previous_page = st.session_state.get("_analytics_current_page")
    previous_started = st.session_state.get("_analytics_page_started_monotonic")

    if previous_page == page_name:
        return

    now_monotonic = time.perf_counter()

    if previous_page and previous_started is not None:
        duration_ms = max(
            0.0,
            (now_monotonic - float(previous_started)) * 1000,
        )
        track_event(
            "page_duration",
            current_user=current_user,
            page_name=str(previous_page),
            duration_ms=duration_ms,
        )

    track_event(
        "page_view",
        current_user=current_user,
        page_name=page_name,
    )
    st.session_state["_analytics_current_page"] = page_name
    st.session_state["_analytics_page_started_monotonic"] = now_monotonic


@contextmanager
def measure(
    operation: str,
    *,
    current_user: dict[str, Any] | None = None,
    page_name: str | None = None,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Time an operation and capture failures while re-raising the exception."""
    started = time.perf_counter()
    try:
        yield
    except Exception as error:
        elapsed = (time.perf_counter() - started) * 1000
        track_event(
            "error",
            current_user=current_user,
            page_name=page_name or current_page_name(),
            entity_type=entity_type,
            entity_id=entity_id,
            duration_ms=elapsed,
            metadata={
                **(metadata or {}),
                "operation": operation,
                "exception_type": type(error).__name__,
                "message": str(error)[:1000],
            },
        )
        raise
    else:
        elapsed = (time.perf_counter() - started) * 1000
        track_event(
            "performance",
            current_user=current_user,
            page_name=page_name or current_page_name(),
            entity_type=entity_type,
            entity_id=entity_id,
            duration_ms=elapsed,
            metadata={
                **(metadata or {}),
                "operation": operation,
            },
        )


def fetch_all(
    statement: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Read analytics rows for the admin repository."""
    initialize_analytics()
    with _connect() as connection:
        cursor = connection.cursor() if database_url() else connection
        query_params = params
        if not database_url():
            query_params = tuple(
                value.isoformat() if isinstance(value, datetime) else value
                for value in params
            )
        rows = cursor.execute(_sql(statement), query_params).fetchall()
        output = [dict(row) for row in rows]
        if database_url():
            cursor.close()
        return output
