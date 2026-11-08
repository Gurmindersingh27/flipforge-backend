"""Real PostgreSQL contract/race tests, enabled only on a local throwaway DB.

ITEM_INSPECTION_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1/flipforge_inspection_test
python -m unittest discover -s tests -p 'test_item_inspections_postgres.py' -v

Each test uses and removes a random schema. Never production, no app lifespan,
no provider calls. The regular suite skips these unless the variable is set.
"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app.item_inspection_models import ItemInspectionFacts
from app.main import app
from app.services import item_inspection_service as inspections
import test_item_inspections as contracts


@unittest.skipUnless(os.environ.get("ITEM_INSPECTION_TEST_DATABASE_URL"), "requires local throwaway PostgreSQL")
class ItemInspectionPostgresTests(contracts.ItemInspectionTests):
    def make_engine(self):
        url = make_url(os.environ["ITEM_INSPECTION_TEST_DATABASE_URL"])
        if (url.drivername not in ("postgresql", "postgresql+psycopg2")
                or url.host not in ("localhost", "127.0.0.1", "::1")
                or url.database != "flipforge_inspection_test" or url.query):
            raise ValueError("Use the local throwaway database flipforge_inspection_test, without query parameters.")
        self.pg_schema = "inspection_test_" + uuid4().hex
        self.pg_admin = create_engine(url, connect_args={"connect_timeout": 5})
        self.pg_connections = create_engine(url, pool_size=10, max_overflow=0, connect_args={
            "connect_timeout": 5, "application_name": self.pg_schema,
            "options": "-c statement_timeout=20000 -c lock_timeout=15000",
        })
        with self.pg_admin.begin() as connection:
            connection.execute(CreateSchema(self.pg_schema))
        self.addCleanup(self.remove_pg_schema, self.pg_admin, self.pg_connections, self.pg_schema)
        return self.pg_connections.execution_options(schema_translate_map={None: self.pg_schema})

    def make_upgrade_engine(self):
        return self.make_engine()

    def remove_pg_schema(self, admin, connections, schema):
        connections.dispose()
        try:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"),
                                                   {"name": schema}), 0)
        finally:
            admin.dispose()

    def concurrent_request(self, root, body):
        client = TestClient(app)
        try:
            response = client.post(f"/api/items/{root}/inspections", headers={"X-Test-User": "alice"}, json=body)
            return response.status_code, response.json()
        finally:
            client.close()

    def race_same_root(self, root, bodies):
        barrier = Barrier(8, timeout=20)
        real_root = inspections._owned_root
        real_validate = ItemInspectionFacts.model_validate
        observations = []
        def synchronized_root(*args, **kwargs):
            if kwargs.get("lock"):
                barrier.wait()
            return real_root(*args, **kwargs)
        def validate_while_others_wait(*args, **kwargs):
            # This is called only for the new write after the root lock and stale
            # check. Replayed/conflicting requests skip it. Prove actual lock wait.
            deadline, waiters = monotonic() + 10, 0
            while monotonic() < deadline:
                with self.pg_admin.connect() as connection:
                    waiters = connection.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name=:name AND wait_event_type='Lock'"), {"name": self.pg_schema})
                if waiters == 7:
                    break
                sleep(0.02)
            self.assertEqual(waiters, 7)
            observations.append(waiters)
            return real_validate(*args, **kwargs)
        with patch.object(inspections, "_owned_root", side_effect=synchronized_root), \
                patch.object(ItemInspectionFacts, "model_validate", side_effect=validate_while_others_wait), \
                ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda body: self.concurrent_request(root, body), bodies))
        self.assertEqual(observations, [7])
        return results

    def test_eight_identical_first_saves_create_one_record(self):
        body = contracts.inspection_body(notes="Same phone retry")
        results = self.race_same_root(self.root["id"], [body] * 8)
        self.assertTrue(all(status == 201 for status, _ in results), results)
        self.assertTrue(all(row == results[0][1] for _, row in results), results)
        self.assertEqual(self.count(), 1)

    def test_eight_simultaneous_edits_keep_one_winner_and_seven_conflicts(self):
        first = self.post().json()
        bodies = [contracts.inspection_body(expected_previous_id=first["id"], notes=f"Phone {i}") for i in range(8)]
        results = self.race_same_root(self.root["id"], bodies)
        statuses = [status for status, _ in results]
        self.assertEqual(statuses.count(201), 1, results)
        self.assertEqual(statuses.count(409), 7, results)
        self.assertEqual(self.count(), 2)
        self.assertTrue(all(set(row) == {"detail"} for status, row in results if status == 409))

    def test_eight_roots_reusing_one_uuid_hit_real_primary_key_conflicts(self):
        roots = [self.save_estimate()["id"] for _ in range(8)]
        body = contracts.inspection_body(notes="Do not leak between items")
        barrier = Barrier(8, timeout=20)
        real_validate = ItemInspectionFacts.model_validate
        collisions, guard = [], Lock()
        def synchronized_facts(*args, **kwargs):
            barrier.wait()
            return real_validate(*args, **kwargs)
        def record_collision(context):
            error = context.original_exception
            if getattr(error, "pgcode", None) == "23505":
                with guard:
                    collisions.append(getattr(error.diag, "constraint_name", None))
        event.listen(self.pg_connections, "handle_error", record_collision)
        try:
            with patch.object(ItemInspectionFacts, "model_validate", side_effect=synchronized_facts), \
                    ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda root: self.concurrent_request(root, body), roots))
        finally:
            event.remove(self.pg_connections, "handle_error", record_collision)
        statuses = [status for status, _ in results]
        self.assertEqual(statuses.count(201), 1, results)
        self.assertEqual(statuses.count(409), 7, results)
        self.assertEqual(self.count(), 1)
        self.assertEqual(collisions, ["pk_item_inspection_revisions"] * 7)
        self.assertTrue(all(set(row) == {"detail"} for status, row in results if status == 409))
