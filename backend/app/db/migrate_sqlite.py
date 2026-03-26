"""기존 SQLite DB에 누락 컬럼·테이블 추가 (개발 편의)."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.models import AuditLog, Base, KbDocument, UserLawStat


def apply_sqlite_migrations(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    insp = inspect(engine)
    with engine.begin() as conn:
        tables = insp.get_table_names()
        if "kb_documents" not in tables:
            KbDocument.__table__.create(bind=engine, checkfirst=True)
        if "user_law_stats" not in tables:
            UserLawStat.__table__.create(bind=engine, checkfirst=True)
        if "audit_logs" not in tables:
            AuditLog.__table__.create(bind=engine, checkfirst=True)

        if "kb_chunks" in tables:
            cols = {c["name"] for c in insp.get_columns("kb_chunks")}
            if "document_id" not in cols:
                conn.execute(text("ALTER TABLE kb_chunks ADD COLUMN document_id VARCHAR(36)"))
            if "embedding_json" not in cols:
                conn.execute(text("ALTER TABLE kb_chunks ADD COLUMN embedding_json TEXT"))

        if "topic_classifications" in tables:
            cols = {c["name"] for c in insp.get_columns("topic_classifications")}
            if "entities_json" not in cols:
                conn.execute(text("ALTER TABLE topic_classifications ADD COLUMN entities_json TEXT"))

        if "user_model_preferences" in tables:
            up_cols = {c["name"] for c in insp.get_columns("user_model_preferences")}
            if "dual_api_reporter_sub_first" not in up_cols:
                conn.execute(
                    text(
                        "ALTER TABLE user_model_preferences ADD COLUMN dual_api_reporter_sub_first "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

        if "kb_documents" in tables:
            dcols = {c["name"] for c in insp.get_columns("kb_documents")}
            if "source_kind" not in dcols:
                conn.execute(
                    text("ALTER TABLE kb_documents ADD COLUMN source_kind VARCHAR(32) NOT NULL DEFAULT 'manual'")
                )
            if "source_url" not in dcols:
                conn.execute(text("ALTER TABLE kb_documents ADD COLUMN source_url TEXT"))
            if "shared_globally" not in dcols:
                conn.execute(
                    text("ALTER TABLE kb_documents ADD COLUMN shared_globally BOOLEAN NOT NULL DEFAULT 0")
                )

    Base.metadata.create_all(bind=engine)
