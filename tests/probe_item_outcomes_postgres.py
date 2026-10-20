"""Opt-in real PostgreSQL probe; never production, never part of backend CI.

Create a local throwaway database named flipforge_save_probe, then run:
  ITEM_OUTCOME_PROBE_DATABASE_URL=postgresql://localhost/flipforge_save_probe \
    PYTHONPATH=. python tests/probe_item_outcomes_postgres.py

Uses a random schema, removes it afterwards, overrides auth, and never calls a
provider. No lifespan: application startup must not touch its default database.
Do not use python -I, which removes the documented PYTHONPATH.
"""
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from fastapi import Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.models.item_outcome import ItemOutcomeRevision
from app.db.models.saved_item import SavedItem
from app.db.session import get_db
from app.item_outcome_models import SaveItemOutcomeRequest
from app.main import app
from app.services import item_outcome_service as outcomes


def run_probe():
    raw_url = os.environ.get("ITEM_OUTCOME_PROBE_DATABASE_URL")
    if not raw_url:
        raise ValueError("Set ITEM_OUTCOME_PROBE_DATABASE_URL to the local throwaway database.")
    url = make_url(raw_url)
    if (url.drivername not in ("postgresql", "postgresql+psycopg2")
            or url.host not in ("localhost", "127.0.0.1", "::1")
            or url.database != "flipforge_save_probe" or url.query):
        raise ValueError("Use local PostgreSQL database flipforge_save_probe, without URL query parameters.")
    schema = "outcome_probe_" + uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    connections = create_engine(url, pool_size=10, max_overflow=0, connect_args={
        "connect_timeout": 5, "application_name": schema,
        "options": "-c statement_timeout=20000 -c lock_timeout=15000",
    })
    engine = connections.execution_options(schema_translate_map={None: schema})
    Session = sessionmaker(bind=engine, autoflush=False)
    old_overrides = app.dependency_overrides.copy()
    created = False
    output = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    try:
        with admin.begin() as connection:
            output["postgresql_version"] = connection.execute(text("SHOW server_version")).scalar_one()
            connection.execute(CreateSchema(schema))
        created = True
        Base.metadata.create_all(engine)
        def database():
            with Session() as db:
                yield db
        def owner(x_test_user: str = Header(default="outcome-probe-owner")):
            return x_test_user
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_id] = owner

        def request(root, body, user="outcome-probe-owner"):
            client = TestClient(app)
            try:
                response = client.post(f"/api/items/{root}/outcomes", json=body, headers={"X-Test-User": user})
                return response.status_code, response.json()
            finally:
                client.close()

        def new_root():
            client = TestClient(app)
            try:
                response = client.post("/api/items/save", json={"inputs": {"item_name": "Probe chair"}})
                assert response.status_code == 201, response.text
                return response.json()["id"]
            finally:
                client.close()

        def count_outcomes():
            with Session() as db:
                return db.scalar(select(func.count()).select_from(ItemOutcomeRevision))

        def body(**changes):
            return {"request_id": str(uuid4()), "expected_previous_id": None, "status": "bought", **changes}

        def concurrent_same_root(root, bodies):
            barrier = Barrier(8, timeout=20)
            real_root, real_calculate = outcomes._owned_root, outcomes.calculate_outcome
            observation = {}
            def synchronized_root(*args, **kwargs):
                if kwargs.get("lock"):
                    barrier.wait()
                return real_root(*args, **kwargs)
            def calculate_while_others_wait(facts):
                # The first writer holds the root lock here. Prove all seven
                # contenders really wait inside PostgreSQL, not sequential replay.
                deadline = monotonic() + 10
                waiters = 0
                while monotonic() < deadline:
                    with admin.connect() as connection:
                        waiters = connection.scalar(text("SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name = :name AND wait_event_type = 'Lock'"), {"name": schema})
                    if waiters == 7:
                        break
                    sleep(0.02)
                assert waiters == 7, f"Expected 7 actual PostgreSQL lock waiters, got {waiters}"
                observation["postgres_lock_waiters"] = waiters
                return real_calculate(facts)
            with patch.object(outcomes, "_owned_root", side_effect=synchronized_root), \
                    patch.object(outcomes, "calculate_outcome", side_effect=calculate_while_others_wait), \
                    ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(lambda payload: request(root, payload), bodies))
            return responses, observation

        root = new_root()
        identical = body()
        before = count_outcomes()
        responses, observation = concurrent_same_root(root, [identical] * 8)
        assert all(status == 201 for status, _ in responses), responses
        original = responses[0][1]
        assert all(record == original for _, record in responses), responses
        assert count_outcomes() == before + 1
        output["eight_identical"] = dict(success=8, new_rows=1, same_record=True, **observation)

        before = count_outcomes()
        responses, observation = concurrent_same_root(root, [
            body(expected_previous_id=original["id"], purchase_price=i) for i in range(8)])
        statuses = [status for status, _ in responses]
        assert statuses.count(201) == 1 and statuses.count(409) == 7, responses
        assert count_outcomes() == before + 1
        assert all(set(record) == {"detail"} for status, record in responses if status == 409)
        output["eight_stale_edits"] = dict(success=1, conflicts=7, new_rows=1, **observation)

        root = new_root()
        key = str(uuid4())
        before = count_outcomes()
        responses, observation = concurrent_same_root(root, [body(request_id=key, purchase_price=i) for i in range(8)])
        statuses = [status for status, _ in responses]
        assert statuses.count(201) == 1 and statuses.count(409) == 7, responses
        assert count_outcomes() == before + 1
        output["eight_conflicting_payloads"] = dict(success=1, conflicts=7, new_rows=1, **observation)

        # Different root locks cannot serialize global UUID reuse. Exercise the
        # actual named 23505 handler, with eight distinct root rows and one key.
        roots = [new_root() for _ in range(8)]
        shared = body()
        barrier = Barrier(8, timeout=20)
        real_calculate = outcomes.calculate_outcome
        collisions = []
        collision_lock = Lock()
        def synchronized_calculate(facts):
            barrier.wait()
            return real_calculate(facts)
        def count_collision(context):
            original_error = context.original_exception
            if getattr(original_error, "pgcode", None) == "23505":
                with collision_lock:
                    collisions.append(getattr(original_error.diag, "constraint_name", None))
        before = count_outcomes()
        event.listen(connections, "handle_error", count_collision)
        try:
            with patch.object(outcomes, "calculate_outcome", side_effect=synchronized_calculate), \
                    ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(lambda target: request(target, shared), roots))
        finally:
            event.remove(connections, "handle_error", count_collision)
        statuses = [status for status, _ in responses]
        assert statuses.count(201) == 1 and statuses.count(409) == 7, responses
        assert count_outcomes() == before + 1
        assert collisions == ["pk_item_outcome_revisions"] * 7, collisions
        assert all(set(record) == {"detail"} for status, record in responses if status == 409)
        output["eight_roots_same_uuid"] = dict(success=1, conflicts=7, new_rows=1, real_key_collisions=len(collisions))

        root = new_root()
        lost_body = body()
        before = count_outcomes()
        committed = []
        real_save = outcomes.save_outcome
        def lose_response(*args):
            committed.append(real_save(*args).model_dump(mode="json"))
            raise ConnectionError("simulated response loss after commit")
        with patch("app.main.save_outcome", side_effect=lose_response):
            try:
                request(root, lost_body)
                raise AssertionError("Expected simulated lost response")
            except ConnectionError:
                pass
        with patch.object(outcomes, "calculate_outcome", side_effect=AssertionError("must not recalculate")):
            status, replay = request(root, lost_body)
        assert status == 201 and replay == committed[0]
        assert count_outcomes() == before + 1
        status, conflict = request(root, lost_body, user="other-owner")
        assert status == 409 and set(conflict) == {"detail"}
        output["lost_response"] = dict(status=201, same_record=True, new_rows=1, other_owner_status=409)

        # Keep the same session across stale rejection and a subsequent valid save.
        with Session() as db:
            stale = SaveItemOutcomeRequest.model_validate(body())
            try:
                real_save(db, "outcome-probe-owner", root, stale)
                raise AssertionError("Expected stale-write conflict")
            except HTTPException as error:
                assert error.status_code == 409
            assert db.scalar(text("SELECT 1")) == 1
            original_root = db.get(SavedItem, root)
            root_before = dict(original_root.inputs)
            revised = real_save(db, "outcome-probe-owner", root,
                SaveItemOutcomeRequest.model_validate(body(expected_previous_id=replay["id"], status="listed")))
            assert revised.revision_number == 2
            assert db.get(SavedItem, root).inputs == root_before
            assert outcomes.outcome_response(db.get(ItemOutcomeRevision, replay["id"])).model_dump(mode="json") == replay
        output["session_after_rejection"] = dict(healthy=True, valid_save=True, original_revision_unchanged=True)
        output["result"] = "PASS"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        connections.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
                assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"), {"name": schema}) == 0
            output["schema_removed"] = True
        admin.dispose()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    run_probe()
