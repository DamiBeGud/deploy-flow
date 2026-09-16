import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.main import app
from app.persistence.database import engine
from app.persistence.models import Base


@pytest.fixture(scope="session", autouse=True)
def test_database():
    url = make_url(settings().database_url)
    # All destructive test cleanup is confined to an explicit test database.
    if url.database != "deployflow_test":
        raise RuntimeError("Tests require the dedicated deployflow_test database")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname='deployflow_test'")
        )
        if not exists:
            connection.execute(text("CREATE DATABASE deployflow_test"))
    admin.dispose()
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(test_database):
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
