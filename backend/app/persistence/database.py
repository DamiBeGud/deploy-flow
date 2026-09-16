from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(
    settings().database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
)
Session = sessionmaker(engine, expire_on_commit=False)
