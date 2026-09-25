"""Exercise production pool options with a real, isolated DBAPI connection.

SQLite supplies the test transport, not a simulation of Neon's sleep behavior.
The production module receives a dummy Postgres URL; only its connection target
is replaced. No production credentials or remote database are used.
"""
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from app.core import config


class PoolRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = f"sqlite:///{self.temp.name}/pool.db"

    def load_session(self, remote=True):
        captured = {}
        def isolated_engine(url, **options):
            captured.update(options)
            return create_engine(self.url, **options)
        settings = SimpleNamespace(DATABASE_URL="postgresql://unused.invalid/test" if remote else self.url)
        with patch.object(config, "settings", settings), patch("sqlalchemy.create_engine", isolated_engine):
            module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app/db/session.py"))
        self.addCleanup(module["engine"].dispose)
        return module, captured

    def test_closed_idle_connection_is_replaced_before_next_query(self):
        module, options = self.load_session()
        self.assertTrue(options["pool_pre_ping"])
        self.assertEqual(options["pool_recycle"], 300)
        engine = module["engine"]
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE probe (value INTEGER)"))
            connection.execute(text("INSERT INTO probe VALUES (42)"))
            stale = connection.connection.driver_connection
        # The pool thinks this returned connection is healthy; its socket closes.
        stale.close()
        with module["SessionLocal"]() as session:
            self.assertEqual(session.execute(text("SELECT value FROM probe")).scalar_one(), 42)
            self.assertIsNot(session.connection().connection.driver_connection, stale)

    def test_aged_connection_is_recycled_on_checkout(self):
        module, _ = self.load_session()
        engine = module["engine"]
        records = []
        event.listen(engine, "connect", lambda connection, record: records.append(record))
        with engine.connect() as connection:
            original = connection.connection.driver_connection
            self.assertEqual(connection.execute(text("SELECT 1")).scalar_one(), 1)
        # Age the connection record deterministically instead of sleeping 5 min.
        records[0].starttime -= 301
        with engine.connect() as connection:
            self.assertIsNot(connection.connection.driver_connection, original)
            self.assertEqual(connection.execute(text("SELECT 1")).scalar_one(), 1)
        self.assertEqual(len(records), 2)

    def test_sqlite_keeps_existing_connection_options(self):
        module, options = self.load_session(remote=False)
        self.assertEqual(options["connect_args"], {"check_same_thread": False})
        self.assertFalse(options["pool_pre_ping"])
        self.assertEqual(options["pool_recycle"], -1)
        with module["SessionLocal"]() as session:
            self.assertEqual(session.execute(text("SELECT 1")).scalar_one(), 1)
