from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlalchemy.types import JSON

from app.db.base import Base


class RentCastCache(Base):
    __tablename__ = "rentcast_cache"

    cache_key = Column(Text, primary_key=True)   # normalized address
    payload = Column(JSON, nullable=False)        # serialized EnrichAddressResponse (no cache meta)
    cached_at = Column(DateTime, default=datetime.utcnow, nullable=False)
