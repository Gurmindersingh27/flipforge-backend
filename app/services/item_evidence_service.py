"""Deterministic evidence selection and Decimal quartiles, never AI offer math."""
from decimal import Decimal, ROUND_HALF_UP
from posixpath import normpath
import re
from urllib.parse import unquote, urlsplit

from pydantic import HttpUrl

from app.saved_item_models import SaveItemRequest
from app.item_assessment_models import clean_text


BLOCKED_RETAIL_DOMAINS = ("homedepot.com", "wayfair.com", "lowes.com", "ikea.com", "walmart.com", "amazon.com")
_EBAY_MARKETPLACES = (
    "ebay.com", "ebay.at", "ebay.com.au", "ebay.be", "ebay.ca", "ebay.ch", "ebay.de",
    "ebay.es", "ebay.fr", "ebay.co.uk", "ebay.com.hk", "ebay.ie", "ebay.it",
    "ebay.com.my", "ebay.nl", "ebay.ph", "ebay.pl", "ebay.com.sg", "ebay.com.tw",
)
_EBAY_ITEM_PATH = re.compile(r"/itm/(?:(?P<title>[^/]+)/)?[1-9][0-9]*/?")
_NAVIGATION_ROUTES = {
    "aptdeco.com": r"catalog",
    "chairish.com": r"collection",
    "craigslist.org": r"(?:d/[^/]+/)?search",
    "mercari.com": r"search|us/(?:search|shop|brand)",
    "offerup.com": r"search|c|explore",
}
MIN_RANGE_LISTINGS = 3
# These reasons follow the shared price/comparability/shipping gates. They let
# optional diagnostics count BOTH unknown flags without repeating eligibility.
NATIONAL_CLASSIFICATION_REASONS = frozenset({
    "condition_new", "condition_unknown", "shipping_included", "price_basis_unknown",
})


def _on_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def blocked_listing_url(value):
    try:
        # Use the browser-equivalent host and path only for classifying the route.
        parsed = HttpUrl(canonical_url(value))
        host = parsed.host.rstrip(".")
    except (ValueError, TypeError, AttributeError):
        return False  # Existing URL validation handles malformed URLs.
    # The ebay.us share host redirects without exposing the destination item.
    if any(_on_domain(host, domain) for domain in (*BLOCKED_RETAIL_DOMAINS, "ebay.us")):
        return True
    if any(_on_domain(host, domain) for domain in _EBAY_MARKETPLACES):
        item = _EBAY_ITEM_PATH.fullmatch(parsed.path)
        if not item:
            return True
        title = unquote(item["title"] or "")
        return title in (".", "..") or "/" in title or "\\" in title
    for domain, routes in _NAVIGATION_ROUTES.items():
        if _on_domain(host, domain):
            path = normpath("/" + unquote(parsed.path).lstrip("/")).lower()
            return path == "/" or re.match(rf"/(?:{routes})(?:/|$)", path) is not None
    return False


DOLLAR_PRICE_PATTERN = re.compile(r"(?:\$|USD\s*)([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)(?![0-9]|\.[0-9])")

# Match labels only in the gap around one dollar occurrence. A label from a
# previous clause or a previous price must not classify the next amount.
_CLAUSE_BREAK = re.compile(r"[,;!?()\n|—]|\.(?=\s|$)")
_TO_AMOUNT = re.compile(
    r"(?:\s+(?:and\s+handling|available|starting|starts?|fees?|charges?|costs?|price|"
    r"at|from|of|for|is|will|be|an?|extra|additional))*[\s:~=-]*", re.I)
_LEADING_LABEL = re.compile(
    rf"""\b(?:
        (?P<other>
            retail(?:s|ed)? | msrp | originally | original\s+(?:(?:asking\s+)?price|cost|value) |
            original(?=[\s:~=-]*$) | paid | bought | compare[\s-]+at | valued\s+at |
            list[\s-]+price | new\s+for | for\s+reference |
            (?:another|separate|other)\s+(?:item|listing|post) | fees? | deposits? |
            was(?=(?:\s+(?:asking|price|priced|listed|at|for|about|around))*[\s:~=-]*$) |
            (?:deliver(?:y|ed|s)?|ship(?:ping|s)?)(?={_TO_AMOUNT.pattern}$)
        )\b(?:\s+(?:asking(?:\s+price)?|price)\b)? |
        (?P<ask>asking\b | price\s*: | buy\s+it\s+now\b | now\b | obo\b | firm\b)
            (?=(?:\s+(?:price|is|at|for|only|just|about|around))*[\s:~=-]*$)
    )""", re.I | re.X)
_TRAILING_LABEL = re.compile(
    r"""^[ \t]*(?:\([ \t]*(?=[^()\n]*\)))?(?:
        (?P<other>
            retail | msrp | list[\s-]+price | originally | (?:was\s+the\s+)?original\s+(?:price|cost) |
            new | fees? | deposits? |
            (?:for\s+)?(?:shipping|delivery)(?!\s+(?:included|excluded|available|extra|separately)\b) |
            (?:(?:shown|listed|sold)\s+(?:in|on)\s+|see\s+)(?:a\s+)?separate\s+(?:post|listing)
        )\b | (?P<ask>obo|firm)\b
    )""", re.I | re.X)


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


def _trailing_price_label(gap, has_next_amount):
    """Return this price's suffix and the text still available to the next price."""
    label = _TRAILING_LABEL.match(gap)
    if not label:
        return None, gap
    rest = gap[label.end():]
    if has_next_amount and _TO_AMOUNT.fullmatch(rest):
        # "$80 delivery starts at $15" labels only the next price. A bare
        # intervening label is ambiguous, so both prices must account for it.
        return (None if rest.strip() else label.lastgroup), gap
    # "$15 shipping and $80" attaches "shipping" only to $15.
    return label.lastgroup, rest


def quote_supports_price(quotes, price):
    """Resolve one asking price across all the accepted excerpts for this URL."""
    candidates, cued = set(), set()
    for quote in quotes:
        # The split preserves the existing dollar/decimal boundaries, including
        # commas inside prices. Even entries are the text between amounts.
        parts = DOLLAR_PRICE_PATTERN.split(quote)
        before = parts[0]
        for i in range(1, len(parts), 2):
            amount = Decimal(parts[i].replace(",", ""))
            clause = _CLAUSE_BREAK.split(before.rstrip(" \t\r\n("))[-1]
            leading = None
            for label in _LEADING_LABEL.finditer(clause):
                leading = label.lastgroup
            trailing, before = _trailing_price_label(parts[i + 1], i + 2 < len(parts))
            if "other" in (leading, trailing):
                continue
            candidates.add(amount)
            if "ask" in (leading, trailing):
                cued.add(amount)

    return (cued or candidates) == {price}


def evidence_diagnostics(response, listings, blocked_listing_count=0):
    """Internal counts only; exact matching and eligibility remain unchanged.

    Citation counts count occurrences, including repeated citations. Matched URL
    count counts distinct retained listing URLs. Near misses count accepted
    citations with a matching hostname/path but no exact retained listing URL.
    National unknown counts are independent, and may overlap: their denominator
    is retained, supported, positive, comparable single shipped listings, before
    checking condition_type and price_basis. Blocked/duplicate rows never count.
    """
    sources = cited_sources(response)
    urls = {row["url"] for row in listings}
    paths = {(urlsplit(url).hostname.lower(), urlsplit(url).path.rstrip("/")) for url in urls}
    quotes = [quote for values in sources.values() for quote in values]
    native = sum(1 for block in response.get("content", []) if block.get("type") == "text"
                 for citation in block.get("citations") or []
                 if citation.get("type") == "web_search_result_location")
    national_candidates = [row for row in listings if row.get("national_eligible") is True
                           or row.get("national_exclusion_reason") in NATIONAL_CLASSIFICATION_REASONS]
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
        national_unknown_condition_count=sum(row["condition_type"] == "unknown" for row in national_candidates),
        national_unknown_price_basis_count=sum(row["price_basis"] == "unknown" for row in national_candidates),
    )


def quartile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def price_range(prices, label):
    if len(prices) < MIN_RANGE_LISTINGS:
        return None
    return dict(low=float(quartile(prices, Decimal(".25"))),
                high=float(quartile(prices, Decimal(".75"))),
                source="online_asking_prices", eligible_count=len(prices),
                method="linear_quartiles_v1", label=label)


def national_exclusion_reason(listing, supported):
    # Independent of the local first-failure reason: local eligibility and its
    # ordering are unchanged. Report the first failed national rule for the UI.
    if not supported:
        return "unsupported_price"
    if listing.price <= 0:
        return "zero_price"
    if not listing.comparable or not listing.single_item:
        return "not_comparable"
    if listing.market != "national_shipping":
        return "not_shipped"
    if listing.condition_type != "used":
        return "condition_new" if listing.condition_type == "new" else "condition_unknown"
    if listing.price_basis != "item_only":
        return "shipping_included" if listing.price_basis == "shipping_included" else "price_basis_unknown"
    return None


def national_reference(listings, local_resale):
    # Separate pools, never silently applied. A qualifying local range wins.
    if local_resale is not None:
        return None
    prices = [Decimal(str(row["price"])) for row in listings if row["national_eligible"]]
    return price_range(prices, "National used asking prices; shipping excluded. Not confirmed sales or verified local prices.")


def local_exclusion_reason(listing, supported):
    """Pickup listings with unconfirmed condition can qualify for the local range."""
    if not supported:
        return "unsupported_price"
    if not listing.comparable or not listing.single_item:
        return "not_comparable"
    if listing.market != "local_pickup":
        return "not_local_pickup"
    if listing.price <= 0:
        return "zero_price"
    if listing.condition_type == "new":
        return "condition_new"
    return None


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
        reason = local_exclusion_reason(listing, supported)
        eligible = reason is None
        national_reason = national_exclusion_reason(listing, supported)
        listings.append(dict(title=listing.title, url=url, price=float(listing.price), currency="USD",
                             source=parsed.hostname.lower(), condition=listing.condition,
                             condition_type=listing.condition_type, price_basis=listing.price_basis,
                             market=listing.market, location=listing.location,
                             retrieved_at=retrieved_at, price_type="asking", eligible=eligible,
                             exclusion_reason=reason, supporting_quotes=citations.get(url, []),
                             national_eligible=national_reason is None, national_exclusion_reason=national_reason))
        if eligible:
            prices.append(listing.price)
    return listings, price_range(prices, "Similar items listed online; not confirmed sales or verified nearby prices")
