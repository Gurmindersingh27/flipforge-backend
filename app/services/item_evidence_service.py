"""Deterministic evidence selection and Decimal quartiles, never AI offer math."""
from decimal import Decimal, ROUND_HALF_UP
import re
from urllib.parse import urlsplit

from app.saved_item_models import SaveItemRequest
from app.item_assessment_models import clean_text


BLOCKED_RETAIL_DOMAINS = ("homedepot.com", "wayfair.com", "lowes.com", "ikea.com", "walmart.com", "amazon.com")


def blocked_listing_url(value):
    try:
        host = urlsplit(canonical_url(value)).hostname.lower().rstrip(".")
    except (ValueError, TypeError, AttributeError):
        return False  # Existing URL validation handles malformed URLs.
    return any(host == domain or host.endswith("." + domain) for domain in BLOCKED_RETAIL_DOMAINS)


DOLLAR_PRICE_PATTERN = re.compile(r"(?:\$|USD\s*)([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)(?![0-9]|\.[0-9])")

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
        for value in DOLLAR_PRICE_PATTERN.findall(quote):
            if Decimal(value.replace(",", "")) == price:
                return True
    return False


def evidence_diagnostics(response, listings, blocked_listing_count=0):
    """Internal counts only; exact matching and eligibility remain unchanged.

    Citation counts count occurrences, including repeated citations. Matched URL
    count counts distinct retained listing URLs. Near misses count accepted
    citations with a matching hostname/path but no exact retained listing URL.
    """
    sources = cited_sources(response)
    urls = {row["url"] for row in listings}
    paths = {(urlsplit(url).hostname.lower(), urlsplit(url).path.rstrip("/")) for url in urls}
    quotes = [quote for values in sources.values() for quote in values]
    native = sum(1 for block in response.get("content", []) if block.get("type") == "text"
                 for citation in block.get("citations") or []
                 if citation.get("type") == "web_search_result_location")
    return dict(
        blocked_listing_count=blocked_listing_count,
        native_citation_count=native,
        accepted_citation_count=len(quotes),
        matched_url_count=len(urls.intersection(sources)),
        verified_price_listing_count=sum(
            quote_supports_price(sources.get(row["url"], []), Decimal(str(row["price"])))
            for row in listings),
        near_miss_citation_count=sum(len(values) for url, values in sources.items()
            if url not in urls and (urlsplit(url).hostname.lower(), urlsplit(url).path.rstrip("/")) in paths),
        no_dollar_price_citation_count=sum(not DOLLAR_PRICE_PATTERN.search(quote)
            for quote in quotes),
    )


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
        if blocked_listing_url(listing.url):
            continue
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
