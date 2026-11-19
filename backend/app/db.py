from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://cijene:cijene@localhost:5432/cijene"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    eng = eng or engine
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(eng)
    # jednostavne migracije za postojeće baze (create_all ne dodaje stupce)
    with eng.begin() as conn:
        for table, col, typ in MIGRATIONS:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}"))


MIGRATIONS = [
    ("price", "sale_type", "varchar(60)"), ("price", "valid_until", "date"),
    ("price", "card_price", "numeric(10,2)"), ("price", "card_name", "varchar(40)"),
    ("chain_daily", "deal_label", "varchar(60)"), ("chain_daily", "valid_until", "date"),
    ("chain_daily", "card_price", "numeric(10,2)"), ("chain_daily", "card_name", "varchar(40)"),
]


@contextmanager
def session_scope():
    s: Session = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
