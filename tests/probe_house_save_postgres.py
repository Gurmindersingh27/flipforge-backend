"""Opt-in local PostgreSQL probe, never production and never part of CI.

Create a throwaway local database named flipforge_save_probe. Run on the fixed
checkout (dependencies from requirements.txt):
  HOUSE_SAVE_PROBE_DATABASE_URL=postgresql://localhost/flipforge_save_probe \
    PYTHONPATH=. python tests/probe_house_save_postgres.py --expect fixed

To reproduce the defect, copy this script outside the repository, then from an
unmodified checkout of 6e0d3a349cf0771919678bdb7ea2c34b1affdf1a run:
  HOUSE_SAVE_PROBE_DATABASE_URL=postgresql://localhost/flipforge_save_probe \
    PYTHONPATH=. python /path/to/probe_house_save_postgres.py --expect baseline

Each run creates and removes its own random schema. Auth is overridden; no
providers are called. Do not use python -I: it drops the documented PYTHONPATH.
"""
import argparse
import json
import os
import subprocess
from uuid import uuid4

from fastapi import Header
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.auth import get_current_user_id
from app.db.base import Base
from app.db.models.saved_deal import SavedDeal
from app.db.models.deal_revision import DealRevision
from app.db.session import get_db
from app.main import app


def run_probe(expect):
    raw_url = os.environ.get("HOUSE_SAVE_PROBE_DATABASE_URL")
    if not raw_url:
        raise ValueError("Set HOUSE_SAVE_PROBE_DATABASE_URL to the local throwaway database.")
    url = make_url(raw_url)
    if (url.drivername not in ("postgresql", "postgresql+psycopg2")
            or url.host not in ("localhost", "127.0.0.1", "::1")
            or url.database != "flipforge_save_probe" or url.query):
        raise ValueError("Use local PostgreSQL database flipforge_save_probe, without URL query parameters.")
    schema = "house_save_probe_" + uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    engine = admin.execution_options(schema_translate_map={None: schema})
    Session = sessionmaker(bind=engine, autoflush=False)
    old_overrides = app.dependency_overrides.copy()
    created = False
    output = {"mode": expect, "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(), "cases": []}
    try:
        with admin.begin() as connection:
            output["postgresql_version"] = connection.execute(text("SHOW server_version")).scalar_one()
            connection.execute(CreateSchema(schema))
        created = True
        Base.metadata.create_all(engine)
        # Keep this exact session through each rejected request and SELECT 1.
        # Fixed mode must not require a rollback to make it usable again.
        with Session() as db:
            errors = []
            def database():
                try:
                    yield db
                except Exception as error:
                    if not isinstance(error, RequestValidationError):
                        original = getattr(error, "orig", error)
                        errors.append((type(original).__name__, str(original), getattr(original, "pgcode", None)))
                    raise
            def owner(x_test_user: str = Header(default="house-probe-owner")):
                return x_test_user
            app.dependency_overrides[get_db] = database
            app.dependency_overrides[get_current_user_id] = owner
            def counts():
                return tuple(db.scalar(select(func.count()).select_from(model))
                             for model in (SavedDeal, DealRevision))

            # No lifespan: app startup must not touch its default database.
            client = TestClient(app, raise_server_exceptions=False)
            try:
                normal = {"address": "Café ਪੰਜਾਬੀ 🏠", "revision_note": "Unicode ✓",
                          "analysis_result": {"notes": ["Original"]}}
                saved = client.post("/api/deals/save", json=normal)
                assert saved.status_code == 200, saved.text
                original = saved.json()
                before = counts()
                cases = [
                    ("address", {"address": "Bad\x00address"}, None),
                    ("revision_note", {"revision_note": "Bad\x00note"}, None),
                    ("draft_input.notes[0]", {"draft_input": {"notes": ["Bad\x00note"]}}, "22P05"),
                    ("analysis_result.notes[0]", {"analysis_result": {"notes": ["Bad\x00note"]}}, "22P05"),
                    ("draft_input.nested['bad\\x00key'] (key)", {"draft_input": {"nested": {"bad\x00key": "value"}}}, "22P05"),
                ]
                for path, changes, expected_code in cases:
                    errors.clear()
                    response = client.post("/api/deals/save", json={**normal, **changes})
                    evidence = {"path": path, "status": response.status_code}
                    if expect == "baseline":
                        assert response.status_code == 500, response.text
                        assert len(errors) == 1, errors
                        kind, message, code = errors[0]
                        assert any(marker in message for marker in ("NUL", "0x00", "\\u0000")), errors
                        if expected_code:
                            assert code == expected_code, errors
                        evidence.update(error_type=kind, sqlstate=code)
                        db.rollback()  # Baseline failed transactions need recovery.
                    else:
                        assert response.status_code == 422, response.text
                        assert path in response.json()["detail"][0]["msg"], response.text
                        assert not errors, errors
                    assert db.scalar(text("SELECT 1")) == 1
                    assert counts() == before
                    assert client.get(f'/api/deals/{original["id"]}').json() == original
                    evidence.update(no_rows_written=True, same_session_healthy=True)
                    output["cases"].append(evidence)
                # A valid write still works after all rejected requests.
                followup = client.post("/api/deals/save", json=normal)
                assert followup.status_code == 200, followup.text
                assert counts() == tuple(count + 1 for count in before)
                output["valid_save_after_rejections"] = True
            finally:
                client.close()
        output["result"] = "PASS"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
                assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"),
                                         {"name": schema}) == 0
            output["schema_removed"] = True
        admin.dispose()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", choices=("baseline", "fixed"), required=True)
    run_probe(parser.parse_args().expect)
