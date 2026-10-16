"""Database engine, sessions and non-destructive initialization."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from stocksense.exceptions import DatabaseError, DuplicateRecordError, StockSenseError
from stocksense.models import AppSetting, Base

log = logging.getLogger("stocksense.database")

# Database-level guarantee that decision history and audit history are append-only.
_APPEND_ONLY_TRIGGERS = [
    f"""CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{op.lower()}
        BEFORE {op} ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"""
    for table in ("audit_events", "approval_decisions")
    for op in ("UPDATE", "DELETE")
]


class Database:
    """Owns the engine and hands out transactional sessions."""

    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            url, future=True,
            connect_args={"timeout": 15, "check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A unit of work: commit on success, roll back on any error."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except StockSenseError:
            session.rollback()
            raise
        except IntegrityError as exc:
            session.rollback()
            log.warning("Integrity error: %s", exc.orig)
            raise DuplicateRecordError(
                "The change conflicts with an existing record (duplicate or invalid reference).",
                detail=str(exc.orig)) from None
        except OperationalError as exc:
            session.rollback()
            log.error("Database operational error: %s", exc.orig)
            msg = "The database is busy or locked. Wait a moment and try again." \
                if "locked" in str(exc.orig).lower() else DatabaseError.default_message
            raise DatabaseError(msg, detail=str(exc.orig)) from None
        except SQLAlchemyError as exc:
            session.rollback()
            log.error("Database error: %s", type(exc).__name__)
            raise DatabaseError(detail=str(exc)) from None
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def init_schema(self) -> bool:
        """Create missing tables. Never drops or replaces data.

        Returns True when this call initialized a brand-new database.
        """
        try:
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as conn:
                for statement in _APPEND_ONLY_TRIGGERS:
                    conn.exec_driver_sql(statement)
        except SQLAlchemyError as exc:
            log.error("Schema creation failed: %s", exc)
            raise DatabaseError("The database could not be initialized. Check that the data "
                                "folder is writable and the file is not corrupt.",
                                detail=str(exc)) from None
        with self.session() as s:
            marker = s.get(AppSetting, "_db_initialized_at")
            if marker is not None:
                return False
            from stocksense.utils.dates import utcnow
            s.add(AppSetting(key="_db_initialized_at", value=utcnow().isoformat()))
            from stocksense.services import audit_service
            audit_service.record(s, actor=None, action="DATABASE_INITIALIZED",
                                 entity_type="database", entity_id="main",
                                 message="Database schema created.")
            return True

    def has_users(self) -> bool:
        from stocksense.models import User
        with self.session() as s:
            return s.execute(select(User.id).limit(1)).first() is not None
