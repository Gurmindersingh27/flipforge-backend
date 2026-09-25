from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings

# SQLite needs this flag for multi-thread FastAPI dev
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    future=True,
    # Remote servers can close idle sockets. Check before checkout, not by
    # replaying transactions; SQLite retains its existing pool behavior.
    pool_pre_ping=not settings.DATABASE_URL.startswith("sqlite"),
    pool_recycle=300 if not settings.DATABASE_URL.startswith("sqlite") else -1,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
