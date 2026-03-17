from app.db.base import Base
from app.db.session import engine
from app.db.models import Deal, DealAnalysis, SavedDeal  # noqa: F401 — registers tables


def init_db():
    """Create all registered tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
