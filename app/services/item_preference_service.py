"""One atomic replacement per account; retries use ordinary last-write-wins PUT."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.item_preference import ItemPreference
from app.item_preference_models import ItemPreferencesResponse


def preferences_response(row):
    return ItemPreferencesResponse(**{name: row[name] for name in ItemPreferencesResponse.model_fields})


def get_preferences(db, user_id):
    table = ItemPreference.__table__
    row = db.execute(select(table).where(table.c.user_id == user_id)).mappings().one_or_none()
    return None if row is None else preferences_response(row)


def save_preferences(db, user_id, body):
    table = ItemPreference.__table__
    dialect = db.get_bind().dialect.name
    insert = {"postgresql": postgres_insert, "sqlite": sqlite_insert}[dialect]
    values = {**body.model_dump(), "updated_at": datetime.now(timezone.utc)}
    statement = insert(table).values(user_id=user_id, **values).on_conflict_do_update(
        index_elements=[table.c.user_id], set_=values,
    ).returning(*table.c)
    # Upsert also handles simultaneous first saves without a read/insert race.
    row = db.execute(statement).mappings().one()
    response = preferences_response(row)
    db.commit()
    return response
