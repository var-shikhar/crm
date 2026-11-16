from sqlalchemy.orm import DeclarativeBase, sessionmaker

from crm_common.db import make_engine

from .config import settings

engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
