"""DATABASE_URL 정규화 (Postgres SSL)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import normalize_database_url  # noqa: E402


def test_sqlite_unchanged() -> None:
    assert normalize_database_url("sqlite:///./data/platform.db") == "sqlite:///./data/platform.db"


def test_postgres_appends_sslmode() -> None:
    u = "postgresql+psycopg2://u:p@host:5432/db"
    assert "sslmode=require" in normalize_database_url(u)


def test_postgres_preserves_existing_sslmode() -> None:
    u = "postgresql+psycopg2://u:p@host:5432/db?sslmode=disable"
    assert normalize_database_url(u) == u
