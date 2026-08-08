"""SQLite connection factory with WAL, foreign keys, and busy timeout.

One connection is opened per operation/transaction (SQLite in WAL mode handles
this cheaply and it avoids cross-thread connection sharing entirely).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent_runtime.persistence.errors import RuntimePersistenceError
from agent_runtime.persistence.migrations import migrate, schema_version

DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0


class Database:
    """Durable runtime database handle (path + connection policies only)."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self.path = Path(path)
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        """Create the database directory and apply migrations.

        Failures are explicit: the caller must not fall back to in-memory state.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimePersistenceError(
                f"cannot create runtime database directory {self.path.parent}: {exc}"
            ) from exc
        try:
            with self.connect() as connection:
                migrate(connection)
        except RuntimePersistenceError:
            raise
        except sqlite3.Error as exc:
            raise RuntimePersistenceError(
                f"cannot initialize runtime database {self.path}: {exc}"
            ) from exc

    def schema_version(self) -> int:
        try:
            with self.connect() as connection:
                return schema_version(connection)
        except sqlite3.Error as exc:
            raise RuntimePersistenceError(
                f"cannot read schema version from {self.path}: {exc}"
            ) from exc

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a configured connection; closes it on exit.

        All ``sqlite3.Error`` exceptions (connection, PRAGMA, and subsequent
        query errors) are wrapped in ``RuntimePersistenceError`` so that
        callers never see bare SQLite exceptions.
        """
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1000.0,
            )
        except sqlite3.Error as exc:
            raise RuntimePersistenceError(
                f"cannot open runtime database {self.path}: {exc}"
            ) from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        except sqlite3.Error as exc:
            raise RuntimePersistenceError(
                f"runtime database query failed on {self.path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a connection and run one committed transaction around the body."""
        with self.connect() as connection:
            try:
                with connection:
                    yield connection
            except RuntimePersistenceError:
                raise
            except sqlite3.Error as exc:
                raise RuntimePersistenceError(
                    f"runtime database transaction failed on {self.path}: {exc}"
                ) from exc

    @contextmanager
    def immediate_transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one ``BEGIN IMMEDIATE`` transaction around the body.

        The write lock is acquired before any statement runs, so a
        read-then-write sequence (e.g. lease acquire) is serialized against
        concurrent writers instead of failing with a stale-snapshot busy
        error in WAL mode.
        """
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise RuntimePersistenceError(
                    f"cannot begin immediate transaction on {self.path}: {exc}"
                ) from exc
            try:
                try:
                    yield connection
                except BaseException:
                    try:
                        connection.rollback()
                    except sqlite3.Error:
                        pass
                    raise
                connection.commit()
            except RuntimePersistenceError:
                raise
            except sqlite3.Error as exc:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise RuntimePersistenceError(
                    f"runtime database transaction failed on {self.path}: {exc}"
                ) from exc

    @contextmanager
    def immediate_fenced_transaction(self) -> Iterator[tuple[sqlite3.Connection, float]]:
        """Run one fenced transaction: write lock first, THEN read the clock.

        Yields ``(connection, db_now)`` where ``db_now`` is the SQLite
        database's own current Unix epoch read AFTER ``BEGIN IMMEDIATE``
        granted the write lock.  Every lease fence and timestamp inside the
        body therefore compares against the moment the lock was actually
        acquired — a lock wait that crosses a lease deadline can never be
        masked by a Python wall-clock value captured before the wait
        (PR3.3).
        """
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise RuntimePersistenceError(
                    f"cannot begin immediate transaction on {self.path}: {exc}"
                ) from exc
            try:
                try:
                    db_now = float(
                        connection.execute(
                            "SELECT (julianday('now') - 2440587.5) * 86400.0"
                        ).fetchone()[0]
                    )
                    yield connection, db_now
                except BaseException:
                    try:
                        connection.rollback()
                    except sqlite3.Error:
                        pass
                    raise
                connection.commit()
            except RuntimePersistenceError:
                raise
            except sqlite3.Error as exc:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise RuntimePersistenceError(
                    f"runtime database transaction failed on {self.path}: {exc}"
                ) from exc
