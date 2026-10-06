"""Deterministic evidence selection and Decimal quartiles, never AI offer math."""
from decimal import Decimal, ROUND_HALF_UP
import re
from urllib.parse import urlsplit

from app.saved_item_models import SaveItemRequest
from app.item_assessment_models import clean_text


def canonical_url(value):
    # Reuse the save-only safety rule; do not fetch user or provider URLs.
    value = SaveItemRequest.safe_listing_url(value)
    parsed = urlsplit(value)
    return parsed._replace(fragment="").geturl()


def cited_sources(response):
    sources = {}
    for block in response.get("content", []):
        if block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            if citation.get("type") != "web_search_result_location":
                continue
            try:
                url = canonical_url(citation["url"])
                quote = citation.get("cited_text", "")
                if not isinstance(quote, str):
                    continue
                clean_text(quote)
                sources.setdefault(url, []).append(quote[:1000])
            except (ValueError, KeyError, TypeError):
                continue
    return sources


def quote_supports_price(quotes, price):
    # Require a dollar-denominated price in an actual provider citation, not
    # merely a model-generated URL or a number copied from its JSON answer.
    for quote in quotes:
        for value in re.findall(r"(?:\$|USD\s*)([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)(?![0-9.])", quote):
            if Decimal(value.replace(",", "")) == price:
                return True
    return False


def quartile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def select_evidence(report, response, retrieved_at):
    citations = cited_sources(response)
    listings, prices, seen = [], [], set()
    for listing in report.listings:
        try:
            url = canonical_url(listing.url)
        except (ValueError, TypeError):
            continue
        # Ignore tracking/query variants of the same listing.
        parsed = urlsplit(url)
        key = (parsed.hostname.lower(), parsed.path.rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        supported = url in citations and quote_supports_price(citations[url], listing.price)
        eligible = (supported and listing.price > 0 and listing.comparable and listing.single_item
                    and listing.market == "local_pickup")
        reason = (None if eligible else "unsupported_price" if not supported else
                  "not_comparable" if not listing.comparable or not listing.single_item else
                  "not_local_pickup" if listing.market != "local_pickup" else "zero_price")
        listings.append(dict(title=listing.title, url=url, price=float(listing.price), currency="USD",
                             source=parsed.hostname.lower(), condition=listing.condition,
                             market=listing.market, location=listing.location,
                             retrieved_at=retrieved_at, price_type="asking", eligible=eligible,
                             exclusion_reason=reason, supporting_quotes=citations.get(url, [])))
        if eligible:
            prices.append(listing.price)
    if len(prices) < 3:
        return listings, None
    return listings, dict(low=float(quartile(prices, Decimal(".25"))),
                          high=float(quartile(prices, Decimal(".75"))),
                          source="online_asking_prices", eligible_count=len(prices),
                          method="linear_quartiles_v1", label="Similar items listed online; not confirmed sales or verified nearby prices")
