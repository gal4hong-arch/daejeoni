from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import ensure_data_dir, get_database_url, get_settings
from app.db.migrate_sqlite import apply_sqlite_migrations
from app.db.models import Base

settings = get_settings()
db_url = get_database_url()
ensure_data_dir(db_url)

connect_args: dict = {}
engine_kwargs: dict = {}
if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
else:
    # Supabase / 클라우드 Postgres: 끊긴 연결 감지 후 재연결
    engine_kwargs["pool_pre_ping"] = True

engine = create_engine(db_url, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    apply_sqlite_migrations(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
