from __future__ import annotations

import os
from collections.abc import Iterable

from sqlalchemy import inspect, text
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

os.makedirs("data", exist_ok=True)

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_connect_args = {"timeout": 30} if _is_sqlite else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_legacy_schema)


def _migrate_legacy_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    required_columns: dict[str, Iterable[tuple[str, str]]] = {
        "projects": (("user_id", "VARCHAR"),),
        "pipeline_runs": (
            ("user_id", "VARCHAR"),
            ("session_id", "VARCHAR"),
            ("artifacts_snapshot", "TEXT"),
        ),
        "materials": (("user_id", "VARCHAR"),),
        "prompt_messages": (("user_id", "VARCHAR"),),
        "prompts": (("user_id", "VARCHAR"),),
        "video_uploads": (("session_id", "VARCHAR"),),
        "video_deliveries": (
            ("social_account_id", "VARCHAR"),
            ("draft_payload_json", "TEXT"),
            ("external_status", "VARCHAR"),
            ("platform_error_code", "VARCHAR"),
            ("submitted_at", "DATETIME"),
            ("published_at", "DATETIME"),
        ),
    }

    for table_name, columns in required_columns.items():
        if table_name not in existing_tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
        for column_name, ddl in columns:
            if column_name in existing_cols:
                continue
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
