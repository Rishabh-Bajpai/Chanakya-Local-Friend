#!/usr/bin/env python

from __future__ import annotations

import logging
import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chanakya.config import get_database_url, load_local_env
from chanakya.model import (
    Base,
    AgentProfileModel,
    AppEventModel,
    ChatMessageModel,
    ChatSessionModel,
    RequestModel,
    TaskEventModel,
    TaskModel,
    TemporaryAgentModel,
    ToolInvocationModel,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TARGET_MODELS = [
    ChatSessionModel,
    ChatMessageModel,
    AppEventModel,
    RequestModel,
    TaskModel,
    TaskEventModel,
    ToolInvocationModel,
    AgentProfileModel,
    TemporaryAgentModel,
]


def get_sync_url(database_url: str) -> str:
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "")
    return database_url


def build_column_sql(column, engine) -> str:
    pieces = [f'"{column.name}"', column.type.compile(dialect=engine.dialect)]
    if not column.nullable:
        pieces.append("NOT NULL")
    default = column.server_default
    if default is not None and getattr(default, "arg", None) is not None:
        pieces.append(f"DEFAULT {default.arg}")
    return " ".join(str(piece) for piece in pieces)


def update_database() -> None:
    load_local_env()
    database_url = get_sync_url(get_database_url())
    logger.info("Starting Chanakya database update...")
    logger.info("Database: %s", database_url)

    engine = create_engine(database_url, future=True)
    session = sessionmaker(bind=engine)()

    try:
        logger.info("Ensuring Chanakya tables exist...")
        Base.metadata.create_all(engine)

        logger.info("Checking Chanakya schema for missing columns...")
        inspector = inspect(engine)

        for model in TARGET_MODELS:
            table_name = model.__tablename__
            logger.info("Inspecting table: %s", table_name)

            try:
                existing_columns = inspector.get_columns(table_name)
            except Exception as exc:
                logger.warning("  -> Could not inspect %s: %s", table_name, exc)
                continue

            existing_col_map = {column["name"]: column for column in existing_columns}

            for column in model.__table__.columns:
                if column.name in existing_col_map:
                    continue

                logger.info("  [+] Adding missing column: %s", column.name)
                try:
                    sql = text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN {build_column_sql(column, engine)}'
                    )
                    session.execute(sql)
                    session.commit()
                    logger.info("      -> Added successfully.")
                except Exception as exc:
                    session.rollback()
                    logger.error("      -> Failed to add column: %s", exc)

        logger.info("Chanakya database update complete.")
    except Exception as exc:
        logger.error("Update failed: %s", exc)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    update_database()
