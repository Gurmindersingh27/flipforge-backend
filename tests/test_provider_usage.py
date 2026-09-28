"""Exercise real routes with provider stubs; no credentials or paid calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import app.main as main
from app.core.provider_usage import ProviderUsage, SHARED_CLIENT, client_key, _limit
from app.models import EnrichAddressResponse, PhotoRehabAnalysisResponse


class Clock:
    def __init__(self):
        self.tick = 1000.0
        self.utc = datetime(2026, 9, 28, 23, 59, 30, tzinfo=timezone.utc)

    def limiter(self, client=3, daily=20, cls=ProviderUsage):
        return cls("Test", client, daily, monotonic=lambda: self.tick, utcnow=lambda: self.utc)


def key(header=None):
    headers = [] if header is None else [(b"x-forwarded-for", header.encode())]
    return client_key(Request({"type": "http", "headers": headers}))


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def reject(self, action, retry):
        with self.assertRaises(HTTPException) as error:
            action()
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], str(retry))

    def test_client_key_normalizes_leftmost_and_fails_closed(self):
        self.assertEqual(key(" 192.0.2.1 , 10.0.0.1"), "192.0.2.1")
        self.assertEqual(key("2001:0db8:0:0:0:0:0:1"), "2001:db8::1")
        self.assertEqual(key("::ffff:192.0.2.1"), "192.0.2.1")
        for value in [None, "", "unknown", "192.0.2.1, junk", "192.0.2.1,", "192.0.2.1:80",
                      "fe80::1%eth0", "x" * 4097, ",".join(["192.0.2.1"] * 33)]:
            with self.subTest(value=str(value)[:50]):
                self.assertEqual(key(value), SHARED_CLIENT)
        self.assertEqual(client_key(Request({"type": "http", "headers": [
            (b"x-forwarded-for", b"192.0.2.1"), (b"x-forwarded-for", b"192.0.2.2")]})), SHARED_CLIENT)

    def test_precheck_does_not_charge_and_window_retry_does_not_extend(self):
        limiter = self.clock.limiter(client=1)
        for _ in range(10):
            limiter.check("a")
        limiter.consume("a")
        self.clock.tick += 12.2
        self.reject(lambda: limiter.check("a"), 588)
        self.reject(lambda: limiter.consume("a"), 588)
        limiter.consume("b")
        self.clock.tick = 1600
        limiter.consume("a")

    def test_daily_retry_midnight_reset_and_client_window_survives(self):
        limiter = self.clock.limiter(client=1, daily=1)
        limiter.consume("a")
        self.reject(lambda: limiter.consume("b"), 30)
        self.clock.utc += timedelta(seconds=30)
        self.clock.tick += 30
        self.reject(lambda: limiter.consume("a"), 570)
        limiter.consume("b")
        self.reject(lambda: limiter.check("c"), 86400)

    def test_clock_rollback_does_not_replenish_daily_cap(self):
        limiter = self.clock.limiter(daily=1)
        limiter.consume("a")
        self.clock.utc -= timedelta(days=1)
        self.reject(lambda: limiter.consume("b"), 86430)

    def test_state_bound_does_not_evict_active_clients_and_expired_keys_clear(self):
        limiter = self.clock.limiter(client=1, daily=10)
        with patch("app.core.provider_usage.MAX_CLIENTS", 2):
            limiter.consume("a")
            limiter.consume("b")
            self.reject(lambda: limiter.consume("c"), 600)
            self.reject(lambda: limiter.consume("a"), 600)
            self.clock.tick += 600
            limiter.consume("c")
            limiter.consume("d")

    def test_invalid_environment_falls_back_with_warning_without_echoing_value(self):
        for value in ["", "0", "-1", "1.5", "secret-invalid-value", "1000001"]:
            with self.subTest(value=value), patch.dict("os.environ", {"TEST_USAGE_LIMIT": value}):
                with self.assertLogs("app.core.provider_usage", level="WARNING") as logs:
                    self.assertEqual(_limit("TEST_USAGE_LIMIT", 3), 3)
                self.assertIn("TEST_USAGE_LIMIT", logs.output[0])
                self.assertNotIn("secret-invalid-value", logs.output[0])
        with patch.dict("os.environ", {"TEST_USAGE_LIMIT": "7"}):
            self.assertEqual(_limit("TEST_USAGE_LIMIT", 3), 7)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_limit("TEST_USAGE_LIMIT", 3), 3)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.previous_overrides = main.app.dependency_overrides.copy()
        main.app.dependency_overrides[main.get_db] = lambda: None
        self.addCleanup(self.restore_overrides)
        self.photo = patch.object(main, "analyze_photos", return_value=PhotoRehabAnalysisResponse().model_dump()).start()
        self.address = patch.object(main, "enrich_address", return_value=EnrichAddressResponse(
            property_facts={}, value_signal={}, rent_signal={})).start()
        self.addCleanup(patch.stopall)
        patch.object(main, "photo_usage", self.clock.limiter()).start()
        patch.object(main, "address_usage", self.clock.limiter(client=10, daily=30)).start()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def restore_overrides(self):
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.previous_overrides)

    def call(self, route, headers=None, client=None):
        client = client or self.client
        if route == "photo":
            return client.post("/api/photo-rehab-analysis", headers=headers,
                               files={"photos": ("fixture.jpg", b"fixture", "image/jpeg")})
        return client.post("/api/enrich-address", headers=headers, json={"address": "Fixture address"})

    def test_photo_validation_failures_never_consume_usage(self):
        cases = [
            {"files": {"photos": ("bad.heic", b"x", "image/heic")}},
            {"files": {"photos": ("large.jpg", b"x" * (main.MAX_PHOTO_SIZE + 1), "image/jpeg")}},
            {"files": [("photos", ("x.jpg", b"x", "image/jpeg"))] * 9},
            {},
            {"files": {"photos": ("x.jpg", b"x", "image/jpeg")}, "data": {"sqft": "bad"}},
        ]
        for kwargs in cases:
            self.assertEqual(self.client.post("/api/photo-rehab-analysis", **kwargs).status_code, 422)
        self.photo.assert_not_called()
        for _ in range(3):
            self.assertEqual(self.call("photo").status_code, 200)
        blocked = self.call("photo")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["retry-after"], "600")
        self.assertEqual(self.photo.call_count, 3)

    def test_blank_or_invalid_address_does_not_consume(self):
        main.address_usage = self.clock.limiter(client=1)
        for body in [{"address": " "}, {"address": ""}, {}, {"address": None}]:
            self.assertEqual(self.client.post("/api/enrich-address", json=body).status_code, 422)
        self.address.assert_not_called()
        self.assertEqual(self.call("address").status_code, 200)
        self.assertEqual(self.call("address").status_code, 429)
        self.assertEqual(self.address.call_count, 1)

    def test_missing_and_garbled_headers_share_bucket_for_both_routes(self):
        for route in ["photo", "address"]:
            setattr(main, "photo_usage" if route == "photo" else "address_usage", self.clock.limiter(client=1))
            self.assertEqual(self.call(route).status_code, 200)
            self.assertEqual(self.call(route, {"X-Forwarded-For": "garbled"}).status_code, 429)
            self.assertEqual(self.call(route, {"X-Forwarded-For": "192.0.2.1"}).status_code, 200)

    def test_spoofed_clients_cannot_escape_global_caps_and_errors_have_cors(self):
        for route, stub in [("photo", self.photo), ("address", self.address)]:
            setattr(main, "photo_usage" if route == "photo" else "address_usage", self.clock.limiter(daily=2))
            for i in range(2):
                self.assertEqual(self.call(route, {"X-Forwarded-For": f"192.0.2.{i+1}"}).status_code, 200)
            response = self.call(route, {"X-Forwarded-For": "198.51.100.1", "Origin": "https://flipforge-frontend.vercel.app"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "30")
            self.assertEqual(response.headers["access-control-allow-origin"], "https://flipforge-frontend.vercel.app")
            self.assertIn("00:00 UTC", response.json()["detail"])
            self.assertEqual(stub.call_count, 2)

    def test_cache_hits_and_provider_failures_count_without_refunds(self):
        main.address_usage = self.clock.limiter(client=1)
        self.address.return_value.from_cache = True
        self.address.return_value.provider_status = "cache_hit"
        self.assertTrue(self.call("address").json()["from_cache"])
        self.assertEqual(self.call("address").status_code, 429)
        main.photo_usage = self.clock.limiter(client=1)
        self.photo.side_effect = HTTPException(503, "provider unavailable")
        self.assertEqual(self.call("photo").status_code, 503)
        self.assertEqual(self.call("photo").status_code, 429)
        self.assertEqual(self.photo.call_count, 1)

    def test_concurrent_routes_recheck_atomically_before_exactly_n_provider_calls(self):
        for route in ["photo", "address"]:
            for mode in ["client", "daily"]:
                with self.subTest(route=route, mode=mode):
                    barrier = Barrier(12)
                    class RacingUsage(ProviderUsage):
                        def check(self, key):
                            super().check(key)
                            barrier.wait(timeout=15)
                    limiter = self.clock.limiter(client=3 if mode == "client" else 20,
                                                 daily=3 if mode == "daily" else 20, cls=RacingUsage)
                    setattr(main, "photo_usage" if route == "photo" else "address_usage", limiter)
                    reached = []
                    lock = Lock()
                    def provider(*args, **kwargs):
                        with lock:
                            reached.append(1)
                        return (PhotoRehabAnalysisResponse().model_dump() if route == "photo" else
                                EnrichAddressResponse(property_facts={}, value_signal={}, rent_signal={}))
                    stub = self.photo if route == "photo" else self.address
                    stub.side_effect = provider
                    def worker(i):
                        client = TestClient(main.app)
                        try:
                            headers = {"X-Forwarded-For": f"192.0.2.{i+1}"} if mode == "daily" else None
                            return self.call(route, headers, client).status_code
                        finally:
                            client.close()
                    with ThreadPoolExecutor(max_workers=12) as pool:
                        statuses = list(pool.map(worker, range(12)))
                    self.assertEqual(statuses.count(200), 3)
                    self.assertEqual(statuses.count(429), 9)
                    self.assertEqual(len(reached), 3)

    def test_non_provider_routes_remain_available_when_both_caps_exhausted(self):
        main.photo_usage = self.clock.limiter(daily=1)
        main.address_usage = self.clock.limiter(daily=1)
        self.call("photo")
        self.call("address")
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        response = self.client.post("/api/analyze", json={"purchase_price": 150000, "arv": 270000, "rehab_budget": 50000})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["net_profit"], 34900)
        self.assertEqual(response.json()["max_safe_offer"], 155600)


if __name__ == "__main__":
    unittest.main()
