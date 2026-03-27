#!/usr/bin/env python

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chanakya.config import get_database_url, load_local_env
from chanakya.model import Base


def get_sync_url(database_url: str) -> str:
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "")
    return database_url


def clear_database() -> None:
    load_local_env()
    database_url = get_sync_url(get_database_url())

    print("WARNING: This will delete all Chanakya database data.")
    print(f"Database: {database_url}")
    confirm1 = input("Are you sure you want to continue? (yes/no): ").strip().lower()
    if confirm1 != "yes":
        print("Aborting.")
        return

    print("This is your final warning. This action cannot be undone.")
    confirm2 = input("Type 'delete all chanakya data' to confirm: ").strip()
    if confirm2 != "delete all chanakya data":
        print("Aborting.")
        return

    engine = create_engine(get_sync_url(get_database_url()), future=True)

    print("Dropping Chanakya tables...")
    Base.metadata.drop_all(engine)

    print("Recreating Chanakya schema...")
    Base.metadata.create_all(engine)

    print("Chanakya database cleared and schema recreated successfully.")


if __name__ == "__main__":
    clear_database()
