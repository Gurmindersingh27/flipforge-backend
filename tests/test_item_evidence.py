import json
import random
import unittest
from decimal import Decimal
from fractions import Fraction
from itertools import combinations, permutations

from app.item_analysis_engine import analyze_item
from app.item_assessment_models import ItemAssessmentRequest, ProviderAssessment
from app.item_models import ItemAnalyzeRequest
from app.services.item_assessment_service import build_result
from app.services.item_evidence_service import quartile, select_evidence, evidence_diagnostics, quote_supports_price
from app.services.item_repair_catalog import suggestions
from test_item_assessment import listing_search_response, national_response, provider_response, request_body


class EvidenceTests(unittest.TestCase):
    def repair_report(self, *jobs):
        report = json.loads(provider_response()["content"][0]["text"])
        report["repairs"] = [dict(job_id=job, reason=f"Observed {job}") for job in jobs]
        return ProviderAssessment.model_validate(report)

    def evidence(self, raw):
        report = ProviderAssessment.model_validate_json(raw["content"][0]["text"])
        return select_evidence(report, raw, "2026-10-06T12:00:00+00:00")

    def change_listing(self, raw, **updates):
        report = json.loads(raw["content"][0]["text"])
        report["listings"][0].update(updates)
        raw["content"][0]["text"] = json.dumps(report)

    def test_retail_backstop_dot_boundaries_and_quartiles(self):
        from app.services.item_evidence_service import BLOCKED_RETAIL_DOMAINS
        for host, blocked in [(d, True) for d in BLOCKED_RETAIL_DOMAINS] + [
                ("www.wayfair.com", True), ("WWW.WAYFAIR.COM.", True),
                ("notwayfair.com", False), ("wayfair.com.example.org", False)]:
            with self.subTest(host=host):
                raw = provider_response()
                url = f"https://{host}/chair/0?ref=wayfair.com"
                self.change_listing(raw, url=url)
                raw["content"][0]["citations"][0]["url"] = url
                listings, resale = self.evidence(raw)
                self.assertEqual(len(listings), 2 if blocked else 3)
                if blocked:
                    self.assertIsNone(resale)
                    self.assertNotIn(url, [row["url"] for row in listings])
                else:
                    self.assertEqual((resale["low"], resale["high"]), (70, 82.5))

    def test_uncited_price_or_url_cannot_drive_range(self):
        for updates in [dict(price=999), dict(url="https://invented.example/item"), dict(url="javascript:alert(1)")]:
            raw = provider_response()
            self.change_listing(raw, **updates)
            _, resale = self.evidence(raw)
            self.assertIsNone(resale)

    def test_local_condition_changes_the_range_without_discarding_verified_asks(self):
        prices = (160, 200, 240, 900)
        cases = [
            (("used", "used", "used"), 3),
            (("used", "used", "new"), 2),
            (("new", "new", "new"), 0),
            (("unknown", "unknown", "unknown"), 3),
            (("used", "used", "used", "new"), 3),
        ]
        for conditions, qualifying in cases:
            with self.subTest(conditions=conditions):
                asks = prices[:len(conditions)]
                raw = provider_response(asks)
                block = raw["content"][0]
                report = json.loads(block["text"])
                for listing, citation, condition in zip(report["listings"], block["citations"], conditions):
                    listing.update(title="Chair", condition=condition, condition_type=condition)
                    citation["cited_text"] = f'Chair asking ${listing["price"]}. Pickup available.'
                block["text"] = json.dumps(report)
                result = build_result(ItemAssessmentRequest.model_validate(request_body()), raw)
                rows = result["listings"]
                self.assertEqual([row["price"] for row in rows], list(asks))
                self.assertEqual([row["supporting_quotes"] for row in rows],
                                 [[citation["cited_text"]] for citation in block["citations"]])
                self.assertEqual([row["exclusion_reason"] for row in rows],
                                 ["condition_new" if condition == "new" else None for condition in conditions])
                self.assertEqual([row["eligible"] for row in rows], [condition != "new" for condition in conditions])
                self.assertEqual(sum(row["eligible"] for row in rows), qualifying)
                self.assertEqual(evidence_diagnostics(raw, rows)["verified_price_listing_count"], len(asks))
                self.assertIsNone(result["national_resale"])
                if qualifying == 3:
                    self.assertEqual((result["resale"]["low"], result["resale"]["high"],
                                      result["resale"]["eligible_count"]), (180, 220, 3))
                else:
                    self.assertIsNone(result["resale"])
                    self.assertIsNone(result["inputs"]["resale_low"])
                    self.assertIsNone(result["inputs"]["resale_high"])

    def test_new_condition_follows_other_local_exclusions_and_keeps_national_verdicts(self):
        cases = [
            (160, {}, False, "unsupported_price", "unsupported_price"),
            (160, {"comparable": False}, True, "not_comparable", "not_comparable"),
            (160, {"single_item": False}, True, "not_comparable", "not_comparable"),
            (160, {"market": "unknown"}, True, "not_local_pickup", "not_shipped"),
            (160, {"market": "national_shipping", "price_basis": "item_only"}, True, "not_local_pickup", "condition_new"),
            (0, {}, True, "zero_price", "zero_price"),
            (0, {"market": "national_shipping"}, True, "not_local_pickup", "zero_price"),
        ]
        for price, updates, supported, local, national in cases:
            with self.subTest(price=price, updates=updates, supported=supported):
                raw = provider_response((price,))
                self.change_listing(raw, title="Chair", condition="New", condition_type="new", **updates)
                raw["content"][0]["citations"][0]["cited_text"] = f"Asking ${price}." if supported else "Chair available."
                rows, resale = self.evidence(raw)
                row, = rows
                self.assertEqual((row["eligible"], row["exclusion_reason"]), (False, local))
                self.assertEqual((row["national_eligible"], row["national_exclusion_reason"]), (False, national))
                self.assertEqual(evidence_diagnostics(raw, rows)["verified_price_listing_count"], int(supported))
                self.assertIsNone(resale)

    def test_navigation_pages_cannot_supply_a_third_local_or_national_comp(self):
        urls = (
            "https://WWW.EBAY.COM./sch/i.html?_nkw=chair",
            "https://%65bay.com/sch/i.html?_nkw=chair",
            "https://www.ebay.co.uk/b/Office-Chairs/bn_123",
            "https://ebay.com/?item=123456789012", "https://ebay.com/itm/",
            "https://ebay.com/itm/chair", "https://ebay.com/itm/123456789012/more",
            "https://ebay.com/itm/%2e%2e/123456789012", "https://ebay.us/m/example",
            "https://atlanta.craigslist.org/search/fuo?query=chair",
            "https://atlanta.craigslist.org/d/furniture/search/fua",
            "https://atlanta.craigslist.org/?query=chair",
            "https://offerup.com/search?q=chair", "https://offerup.com/c/furniture",
            "https://offerup.com/explore/k/chair", "https://www.aptdeco.com/catalog/furniture",
            "https://www.chairish.com/collection/chairs", "https://www.mercari.com/us/shop/chairs/",
        )
        body = ItemAssessmentRequest.model_validate(request_body())
        for url in urls:
            for response in (provider_response, national_response):
                with self.subTest(url=url, pool=response.__name__):
                    raw = response()
                    self.change_listing(raw, url=url)
                    raw["content"][0]["citations"][0]["url"] = url  # Exact URL and a quoted ask.
                    counts = {}
                    result = build_result(body, raw, counts)
                    rows = result["listings"]
                    self.assertEqual(len(rows), 2)
                    self.assertNotIn(url, [row["url"] for row in rows])
                    self.assertIsNone(result["resale"])
                    self.assertIsNone(result["national_resale"])
                    self.assertEqual(counts, {"blocked_listing_count": 1})
                    diagnostics = evidence_diagnostics(raw, rows, **counts)
                    self.assertEqual(diagnostics["verified_price_listing_count"], 2)

    def test_direct_item_pages_still_require_the_exact_cited_url(self):
        urls = (
            "https://ebay.com/itm/123456789012?mkcid=1",
            "https://www.ebay.com/itm/Used-Wood-Chair/123456789012/?var=99",
            "https://www.ebay.com.au/itm/123456789012",
            "https://atlanta.craigslist.org/atl/fuo/d/example-chair/1234567890.html",
            "https://offerup.com/item/detail/example-chair",
            "https://www.mercari.com/us/item/m12345678901/",
            "https://www.aptdeco.com/product/catalog-chair",
            "https://www.chairish.com/product/123456/example-chair",
            "https://offerup.com/item/detail/example?ref=/search",
            "https://atlanta.craigslist.org/fuo/d/search-chair/1234567890.html",
            "https://ebay.com.example.org/listing/1", "https://example.org/search/one-chair",
        )
        for url in urls:
            with self.subTest(url=url):
                raw = provider_response()
                self.change_listing(raw, url=url)
                citation = raw["content"][0]["citations"][0]
                citation["url"] = url
                rows, resale = self.evidence(raw)
                self.assertEqual(rows[0]["url"], url)
                self.assertEqual(resale["eligible_count"], 3)
                citation["url"] = url + ("&" if "?" in url else "?") + "tracking=other"
                rows, resale = self.evidence(raw)
                self.assertEqual(rows[0]["exclusion_reason"], "unsupported_price")
                self.assertIsNone(resale)

    def test_tracked_item_can_complete_a_shipped_range_only_once(self):
        body = ItemAssessmentRequest.model_validate(request_body())
        url = "https://www.ebay.com/itm/123456789012"
        raw = national_response()
        self.change_listing(raw, url=url + "?ref=one")
        raw["content"][0]["citations"][0]["url"] = url + "?ref=one"
        result = build_result(body, raw)
        self.assertIsNone(result["resale"])
        self.assertEqual(result["national_resale"]["eligible_count"], 3)

        report = json.loads(raw["content"][0]["text"])
        report["listings"][1]["url"] = url + "?ref=two"
        raw["content"][0]["text"] = json.dumps(report)
        raw["content"][0]["citations"][1]["url"] = url + "?ref=two"
        result = build_result(body, raw)
        self.assertEqual(len(result["listings"]), 2)
        self.assertIsNone(result["national_resale"])

    def test_national_unknown_sets_and_unrelated_items_are_not_local_comps(self):
        for updates in [dict(market="national_shipping"), dict(market="unknown"), dict(single_item=False), dict(comparable=False)]:
            raw = provider_response()
            self.change_listing(raw, **updates)
            listings, resale = self.evidence(raw)
            self.assertFalse(listings[0]["eligible"])
            self.assertIsNone(resale)

    def test_duplicate_urls_do_not_supply_three_matches(self):
        raw = provider_response()
        self.change_listing(raw, url="https://example.com/chair/1?tracking=1")
        self.assertIsNone(self.evidence(raw)[1])

    def test_missing_citations_means_manual_estimate(self):
        raw = provider_response()
        raw["content"][0]["citations"] = []
        self.assertIsNone(self.evidence(raw)[1])

    def test_sentence_final_price_and_diagnostic_share_numeric_boundaries(self):
        for quote, price, supported in [("Buy It Now for $259.00.", "259.00", True),
                                        ("Asking $450.", "450", True),
                                        ("$450, pickup", "450", True),
                                        ("$12.345", "12.34", False)]:
            with self.subTest(quote=quote):
                self.assertEqual(quote_supports_price([quote], Decimal(price)), supported)
                raw = provider_response((float(price),))
                raw["content"][0]["citations"][0]["cited_text"] = quote
                listings, _ = self.evidence(raw)
                self.assertEqual(listings[0]["eligible"], supported)
                counts = evidence_diagnostics(raw, listings)
                self.assertEqual(counts["verified_price_listing_count"], int(supported))
                self.assertEqual(counts["no_dollar_price_citation_count"], int(not supported))

    def test_trailing_slash_near_misses_do_not_verify_price(self):
        raw = provider_response()
        for citation in raw["content"][0]["citations"]:
            citation["url"] += "/?tracking=1"
        listings, resale = self.evidence(raw)
        counts = evidence_diagnostics(raw, listings)
        self.assertEqual(counts["near_miss_citation_count"], 3)
        self.assertEqual(counts["matched_url_count"], 0)
        self.assertEqual(counts["verified_price_listing_count"], 0)
        self.assertIsNone(resale)

    def test_price_labels_apply_to_individual_amounts(self):
        # Invented excerpts. An empty accepted set means neither amount can be used.
        examples = [
            ("Like new $120", [120], [120]),
            ("Brand new in box $120", [120], [120]),
            ("$450, plus shipping", [450], [450]),
            ("Buy It Now for $259.00", [259], [259]),
            ("$120 + $25 shipping", [120, 25], [120]),
            ("Retail $449.99, asking $120", [449.99, 120], [120]),
            ("$120 (retails for $449.99)", [120, 449.99], [120]),
            ("Paid $300 new, asking $120", [300, 120], [120]),
            ("Delivery $25. $120", [25, 120], [120]),
            ("Bookcase $120. Matching nightstand $40", [120, 40], []),
            ("$120 $150", [120, 150], []),
            ("$240 retail. $15 delivery. $80 new.", [240, 15, 80], []),
            ("New for $185. List price: $200. MSRP $225.", [185, 200, 225], []),
            ("Originally $230. Was $190. Now $115", [230, 190, 115], [115]),
            ("Compare at $225. Valued at $200. Deposit $15. Fee $5.", [225, 200, 15, 5], []),
            ("Bought the lamp for $180. Price: $75.", [180, 75], [75]),
            ("Purchased for $400.", [400], []),
            ("Cost me $400.", [400], []),
            ("I spent $400 on it.", [400], []),
            ("Got mine for $400.", [400], []),
            ("Got this new for $400.", [400], []),
            ("I picked it up for $400.", [400], []),
            ("I picked one up new for $400.", [400], []),
            ("Picked up for $400 last year.", [400], []),
            ("You can pick it up for $240.", [240], [240]),
            ("Can be picked up for $240 in Decatur.", [240], [240]),
            ("Can be  picked up for $240 in Decatur.", [240], [240]),
            ("Can be purchased for $240.", [240], [240]),
            ("I got it for $400. Selling for $240.", [400, 240], [240]),
            ("For reference: the wider model is $230. $80 firm", [230, 80], [80]),
            ("Desk $95 OBO. Stand $40 (see separate listing).", [95, 40], [95]),
            ("$80 OBO. Lamp $35 firm.", [80, 35], []),
            ("$120 delivery starts at $25", [120, 25], [120]),
            ("Shipping and handling: $25", [25], []),
            ("$25 shipping and $120", [25, 120], [120]),
            ("$275 (MSRP). $120 originally.", [275, 120], []),
            ("Chair was recently refinished $120", [120], [120]),
            ("$220 retail $140", [220, 140], []),
            ("The original asking price was $190. Now asking $125.", [190, 125], [125]),
            ("Originally asking $160. Was asking $150.", [160, 150], []),
        ]
        for quote, prices, expected in examples:
            with self.subTest(quote=quote):
                accepted = [p for p in prices if quote_supports_price([quote], Decimal(str(p)))]
                self.assertEqual(accepted, expected)

    def test_all_quotes_for_one_url_must_resolve_to_one_ask(self):
        for quotes, expected in [
            (["Chair $95.", "Footrest $35."], []),
            (["Chair $95.", "Chair $95.00."], [95]),
            (["Chair $95.", "Chair $95 firm.", "Footrest $35."], [95]),
            (["Asking $95.", "Price: $35."], []),
            (["Paid $95.", "Asking $95."], [95]),
        ]:
            for order in (quotes, list(reversed(quotes))):
                with self.subTest(quotes=order):
                    self.assertEqual([p for p in (95, 35) if quote_supports_price(order, Decimal(p))], expected)

    def test_verified_count_includes_supported_reference_only_rows(self):
        raw = provider_response((86, 130, 24, 50))
        report = json.loads(raw["content"][0]["text"])
        report["listings"][1]["comparable"] = False
        raw["content"][0]["text"] = json.dumps(report)
        citations = raw["content"][0]["citations"]
        citations[2]["cited_text"] = "Shipping fee: $24."
        citations.append({**citations[3], "cited_text": "Side table $33."})
        rows, _ = self.evidence(raw)
        self.assertEqual([row["exclusion_reason"] for row in rows],
                         [None, "not_comparable", "unsupported_price", "unsupported_price"])
        counts = evidence_diagnostics(raw, rows)
        self.assertEqual(counts["verified_price_listing_count"],
                         sum(row["exclusion_reason"] != "unsupported_price" for row in rows))
        self.assertEqual(counts["no_dollar_price_citation_count"], 0)

    def test_purchase_history_and_current_ask_drive_different_local_decisions(self):
        body = ItemAssessmentRequest.model_validate(request_body(description="Used chair", asking_price=None))
        first_two = [(price, "local_pickup", f"Used chair. Asking ${price}. Local pickup.") for price in (160, 200)]
        for price, quote, reason in [
            (400, "I got it for $400.", "unsupported_price"),
            (240, "I got it for $400. Selling for $240.", None),
        ]:
            with self.subTest(quote=quote):
                raw = listing_search_response(first_two + [(price, "local_pickup", f"Used chair. {quote} Local pickup.")])
                result = build_result(body, raw)
                rows = result["listings"]
                self.assertEqual(rows[2]["exclusion_reason"], reason)
                self.assertEqual([row["eligible"] for row in rows], [True, True, reason is None])
                self.assertEqual(evidence_diagnostics(raw, rows)["verified_price_listing_count"], 2 if reason else 3)
                self.assertIsNone(result["national_resale"])
                inputs = result["inputs"]
                decision = analyze_item(ItemAnalyzeRequest.model_validate({**inputs, "repairs": 5, "pickup": 20}))
                if reason:
                    self.assertIsNone(result["resale"])
                    self.assertEqual((inputs["resale_low"], inputs["resale_high"]), (None, None))
                    self.assertEqual(decision.status, "needs_info")
                    self.assertIsNone(decision.low)
                else:
                    resale = result["resale"]
                    self.assertEqual((resale["low"], resale["high"], resale["eligible_count"]), (180, 220, 3))
                    self.assertEqual((inputs["resale_low"], inputs["resale_high"]), (180, 220))
                    self.assertEqual((decision.status, decision.low.max_offer), ("offer_only", 124))

    def test_retail_only_third_comp_cannot_supply_a_buying_ceiling(self):
        def confirm_repairs(result):
            return analyze_item(ItemAnalyzeRequest.model_validate({**result["inputs"], "repairs": 0}))

        body = ItemAssessmentRequest.model_validate(request_body())
        raw = provider_response((80, 100, 120))
        result = build_result(body, raw)
        self.assertEqual((result["resale"]["low"], result["resale"]["high"]), (90, 110))
        self.assertEqual(confirm_repairs(result).low.max_offer, 60)

        raw["content"][0]["citations"][2]["cited_text"] = "MSRP $120."
        result = build_result(body, raw)
        self.assertEqual([r["eligible"] for r in result["listings"]], [True, True, False])
        self.assertIsNone(result["resale"])
        self.assertIsNone(result["inputs"]["resale_low"])
        decision = confirm_repairs(result)
        self.assertEqual(decision.status, "needs_info")
        self.assertIsNone(decision.low)

    def test_counts_distinguish_absent_near_miss_and_unpriced_citations(self):
        for mode, expected in [
            ("none", (0, 0, 0, 0, 0, 0)),
            ("near", (3, 3, 0, 0, 3, 0)),
            ("unpriced", (3, 3, 3, 0, 0, 3)),
            ("wrong_price", (3, 3, 3, 0, 0, 0)),
            ("valid", (3, 3, 3, 3, 0, 0)),
            ("rejected", (3, 0, 0, 0, 0, 0)),
        ]:
            with self.subTest(mode=mode):
                raw = provider_response()
                if mode == "none":
                    raw["content"][0]["citations"] = []
                for citation in raw["content"][0]["citations"]:
                    if mode == "near":
                        citation["url"] += "?tracking=private"
                    elif mode == "unpriced":
                        citation["cited_text"] = "Used chair, pickup."
                    elif mode == "wrong_price":
                        citation["cited_text"] = "Price $999."
                    elif mode == "rejected":
                        citation["url"] = "javascript:private"
                listings, resale = self.evidence(raw)
                counts = evidence_diagnostics(raw, listings)
                self.assertEqual(tuple(counts.values()), (0, *expected, 0, 0))
                self.assertEqual(resale is not None, mode == "valid")
                self.assertTrue(all(type(value) is int for value in counts.values()))
                self.assertNotIn("private", json.dumps(counts))

    def test_unknown_repairs_stay_unknown_not_zero(self):
        raw = provider_response()
        report = json.loads(raw["content"][0]["text"])
        report["repairs"].append(dict(job_id="structural_rebuild", reason="Possible broken frame"))
        repairs, unknowns = suggestions(ProviderAssessment.model_validate(report))
        self.assertEqual(len(repairs), 1)
        self.assertEqual(unknowns, ["Possible broken frame"])

    def test_draft2_allowances_and_scopes_are_visible_without_rewriting_ai_reasons(self):
        expected = {
            "clean": (5, "Routine cleaning"),
            "scratch_touchup": (8, "Small cosmetic marks"),
            "sand_seat": (15, "This surface only"),
            "refinish_top": (30, "This surface only"),
            "paint_chair": (25, "Whole chair"),
            "paint_dresser": (50, "Whole small dresser"),
            "replace_knobs": (16, "Up to four basic knobs"),
            "glue_joint": (8, "One straightforward loose joint"),
            "seat_fabric": (25, "reuses sound foam and base"),
            "replace_glides": (6, "excludes replacement wooden feet"),
        }
        for job, (amount, scope) in expected.items():
            with self.subTest(job=job):
                repairs, unknowns = suggestions(self.repair_report(job))
                self.assertEqual(unknowns, [])
                self.assertEqual(repairs[0]["materials_cost"], amount)
                self.assertIn(scope, repairs[0]["label"])
                self.assertEqual(repairs[0]["reason"], f"Observed {job}")
                self.assertFalse(repairs[0]["confirmed"])

    def test_every_pair_in_both_orders_absorbs_only_four_whole_piece_paint_pairs(self):
        prices = dict(clean=5, scratch_touchup=8, sand_seat=15, refinish_top=30,
                      paint_chair=25, paint_dresser=50, replace_knobs=16,
                      glue_joint=8, seat_fabric=25, replace_glides=6)
        included = {
            frozenset(("clean", "paint_chair")): "clean",
            frozenset(("clean", "paint_dresser")): "clean",
            frozenset(("scratch_touchup", "paint_chair")): "scratch_touchup",
            frozenset(("scratch_touchup", "paint_dresser")): "scratch_touchup",
        }
        for pair in combinations(prices, 2):
            for jobs in permutations(pair):
                with self.subTest(jobs=jobs):
                    repairs, unknowns = suggestions(self.repair_report(*jobs))
                    self.assertEqual({r["job_id"] for r in repairs}, set(jobs))
                    absorbed = included.get(frozenset(jobs))
                    self.assertEqual(sum(r["materials_cost"] for r in repairs),
                                     sum(prices[job] for job in jobs) - (prices[absorbed] if absorbed else 0))
                    for row in repairs:
                        self.assertEqual(row["materials_cost"], 0 if row["job_id"] == absorbed else prices[row["job_id"]])
                        self.assertEqual("Included in" in row["label"], row["job_id"] == absorbed)
                        self.assertEqual(row["reason"], f'Observed {row["job_id"]}')
                    self.assertEqual(bool(unknowns), set(jobs) == {"paint_dresser", "refinish_top"})

    def test_two_paint_hosts_always_choose_catalog_order_and_keep_both_reasons(self):
        for jobs in permutations(("clean", "scratch_touchup", "paint_dresser", "paint_chair")):
            with self.subTest(jobs=jobs):
                report = self.repair_report(*jobs)
                before = report.model_dump()
                repairs, unknowns = suggestions(report)
                self.assertEqual(report.model_dump(), before)
                self.assertEqual(unknowns, [])
                self.assertEqual(sum(r["materials_cost"] for r in repairs), 75)
                for row in repairs:
                    self.assertEqual(row["reason"], f'Observed {row["job_id"]}')
                    if row["job_id"] in ("clean", "scratch_touchup"):
                        self.assertIn("Included in Prep and paint one chair", row["label"])
                        self.assertNotIn("Included in Prep and paint a small dresser", row["label"])
                        self.assertEqual(row["materials_cost"], 0)

    def test_painted_dresser_and_refinished_top_preserve_work_and_require_custom_total(self):
        for jobs in permutations(("clean", "paint_dresser", "refinish_top")):
            with self.subTest(jobs=jobs):
                report = self.repair_report(*jobs)
                report.repair_unknowns = ["Check the drawer runners"]
                repairs, unknowns = suggestions(report)
                self.assertEqual({r["job_id"]: r["materials_cost"] for r in repairs},
                                 dict(clean=0, paint_dresser=50, refinish_top=30))
                self.assertEqual(unknowns[0], "Check the drawer runners")
                self.assertEqual(len(unknowns), 2)
                self.assertIn("different surfaces or overlap", unknowns[1])
                self.assertIn("enter one total repair budget", unknowns[1])

    def test_duplicate_jobs_do_not_charge_twice_and_unsupported_work_stays_unknown(self):
        report = self.repair_report("clean", "paint_chair", "clean", "paint_chair", "new_legs")
        before = report.model_dump()
        repairs, unknowns = suggestions(report)
        self.assertEqual([(r["job_id"], r["materials_cost"]) for r in repairs],
                         [("clean", 0), ("paint_chair", 25)])
        self.assertEqual(unknowns, ["Observed new_legs"])
        self.assertEqual(report.model_dump(), before)

    def test_generated_quartiles_match_independent_fraction_oracle(self):
        rng = random.Random(20261006)
        for _ in range(1000):
            cents = sorted(rng.randrange(1, 100_000_000) for _ in range(rng.randrange(3, 13)))
            for fraction in [Fraction(1, 4), Fraction(3, 4)]:
                position = (len(cents) - 1) * fraction
                index = position.numerator // position.denominator
                expected = cents[index] + (cents[min(index + 1, len(cents) - 1)] - cents[index]) * (position - index)
                rounded_cents = (expected + Fraction(1, 2)).numerator // (expected + Fraction(1, 2)).denominator
                actual = quartile([Decimal(c) / 100 for c in cents], Decimal(fraction.numerator) / fraction.denominator)
                self.assertEqual(actual, Decimal(rounded_cents) / 100)


if __name__ == "__main__":
    unittest.main()
