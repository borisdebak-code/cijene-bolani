import os
from pathlib import Path

import pytest

# Testovi s bazom koriste TEST_DATABASE_URL; ako baza nije dostupna, preskaču se.
TEST_DB = os.environ.get("TEST_DATABASE_URL",
                         "postgresql+psycopg://cijene:cijene@localhost:5432/cijene_test")
os.environ["DATABASE_URL"] = TEST_DB

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture()
def db_engine():
    from sqlalchemy import text

    from app.db import engine, init_db
    from app.models import Base

    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL nije dostupan ({TEST_DB}): {e}")
    Base.metadata.drop_all(engine)
    init_db(engine)
    yield engine
