"""Opt-in real PostgreSQL HTTP probe; not part of backend CI.

Create a local throwaway database named flipforge_save_probe, then run:
  ITEM_SAVE_PROBE_DATABASE_URL=postgresql://localhost/flipforge_save_probe \
    PYTHONPATH=. python tests/probe_item_save_postgres.py

Uses a random schema, removes only that schema, overrides auth with fixtures,
and never calls a provider. Never point this at a production database.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.models.item_save_request import ItemSaveRequest
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.main import app
from app.services import saved_item_service as saves


def run_probe():
    raw_url = os.environ.get("ITEM_SAVE_PROBE_DATABASE_URL")
    if not raw_url:
        raise ValueError("Set ITEM_SAVE_PROBE_DATABASE_URL to the local throwaway database.")
    url = make_url(raw_url)
    if (url.drivername not in ("postgresql", "postgresql+psycopg2")
            or url.host not in ("localhost", "127.0.0.1", "::1")
            or url.database != "flipforge_save_probe" or url.query):
        raise ValueError("Use local PostgreSQL database flipforge_save_probe, without URL query parameters.")
    schema = "save_probe_" + uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 5}, pool_size=10, max_overflow=0)
    engine = admin.execution_options(schema_translate_map={None: schema})
    Session = sessionmaker(bind=engine, autoflush=False)
    created = False
    old_overrides = app.dependency_overrides.copy()
    output = {}
    try:
        with admin.begin() as connection:
            output["postgresql_version"] = connection.execute(text("SHOW server_version")).scalar_one()
            connection.execute(CreateSchema(schema))
        created = True
        Base.metadata.create_all(engine)
        def database():
            with Session() as db:
                yield db
        def owner(x_test_user: str = Header(default="save-probe-owner")):
            return x_test_user
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_id] = owner

        def count_rows():
            with Session() as db:
                return tuple(db.scalar(select(func.count()).select_from(model))
                             for model in (SavedItem, ItemSaveRequest))

        def concurrent(bodies):
            # Every request passes the initial mapping lookup before any insert.
            # This exercises the real unique-key race/rollback, not sequential replay.
            barrier = Barrier(len(bodies), timeout=20)
            analyze = saves.analyze_item
            def synchronized_analyze(inputs):
                barrier.wait()
                return analyze(inputs)
            # Do not enter TestClient's lifespan: that would run app startup
            # against its default database instead of this isolated schema.
            def request(body):
                client = TestClient(app)
                try:
                    response = client.post("/api/items/save", json=body)
                    return response.status_code, response.json()
                finally:
                    client.close()
            with patch.object(saves, "analyze_item", side_effect=synchronized_analyze), \
                    ThreadPoolExecutor(max_workers=len(bodies)) as pool:
                return list(pool.map(request, bodies))

        identical = dict(request_id=str(uuid4()), inputs={"item_name": "Concurrent chair", "repairs": 0})
        before = count_rows()
        responses = concurrent([identical] * 8)
        assert all(status == 201 for status, _ in responses), responses
        original = responses[0][1]
        assert all(body == original for _, body in responses)
        delta = tuple(after - old for after, old in zip(count_rows(), before))
        assert delta == (1, 1), delta
        output["eight_identical"] = dict(statuses=[status for status, _ in responses],
                                        saved_rows=delta[0], mapping_rows=delta[1], same_record=True)

        different_id = str(uuid4())
        before = count_rows()
        responses = concurrent([dict(request_id=different_id, inputs={"item_name": f"Different {i}"}) for i in range(8)])
        statuses = [status for status, _ in responses]
        assert statuses.count(201) == 1 and statuses.count(409) == 7, responses
        assert all(set(body) == {"detail"} for status, body in responses if status == 409)
        delta = tuple(after - old for after, old in zip(count_rows(), before))
        assert delta == (1, 1), delta
        output["eight_different"] = dict(success=1, conflicts=7, saved_rows=delta[0], mapping_rows=delta[1])

        # Simulate transport failure only after the endpoint's transaction committed.
        lost_body = dict(request_id=str(uuid4()), inputs={"item_name": "Lost response", "repairs": None})
        before = count_rows()
        committed = []
        real_save = saves.save_item
        def lose_response(*args):
            committed.append(real_save(*args).model_dump(mode="json"))
            raise ConnectionError("probe lost response after commit")
        client = TestClient(app)
        try:
            with patch("app.main.save_item", side_effect=lose_response):
                try:
                    client.post("/api/items/save", json=lost_body)
                except ConnectionError:
                    pass
                else:
                    raise AssertionError("Lost-response injection did not run")
            with patch.object(saves, "analyze_item", side_effect=AssertionError("Replay must not calculate")):
                replay = client.post("/api/items/save", json=lost_body)
            assert replay.status_code == 201
            assert replay.json() == committed[0]
            conflict = client.post("/api/items/save", json=lost_body, headers={"X-Test-User": "other-owner"})
            assert conflict.status_code == 409 and set(conflict.json()) == {"detail"}
        finally:
            client.close()
        delta = tuple(after - old for after, old in zip(count_rows(), before))
        assert delta == (1, 1), delta
        output["lost_response"] = dict(same_record=True, saved_rows=delta[0], mapping_rows=delta[1], foreign_owner_status=409)
        output["result"] = "PASS"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    run_probe()
