"""Safe server-side retrieval and content reduction for public property listing pages.

Supports any public listing site (PropertyGuru, 99.co, EdgeProp, etc.) — there is no
per-domain allowlist. Requests are still bounded (size, redirects, timeout) and blocked
from targeting loopback/private/link-local network destinations to avoid the server being
used to reach internal services (SSRF).

Fetching is a two-step strategy: a fast, lightweight `httpx` GET is tried first, and a
headless browser (Playwright/Chromium) is only spun up as a fallback when that plain
fetch is blocked (e.g. a Cloudflare "Just a moment..." JS challenge, which a scripted
HTTP client can never pass since it never executes page JavaScript).
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_URL_LENGTH = 2_048
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 3
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_CONTENT_LENGTH = 50_000
# Framework hydration payloads (e.g. Next.js `__NEXT_DATA__`) can be huge and are
# mostly page chrome (nav menus, footers, feature flags) unrelated to the listing.
# `ld+json` structured data is normally a few KB; anything far larger than that is
# dropped rather than truncated, since a truncated JSON blob would just fail to
# parse anyway (see `_parse_structured_scripts`).
MAX_STRUCTURED_SCRIPT_LENGTH = 20_000

_BLOCKED_HOSTNAME_SUFFIXES = (".local", ".localhost", ".internal")
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}

# This deliberately fixed URL is used only by the protected egress experiment.
# It is not accepted from callers and therefore cannot turn the endpoint into a
# general-purpose fetching proxy.
DIAGNOSTIC_PROPERTYGURU_URL = (
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-217-bishan-street-23-60027295"
)
DIAGNOSTIC_IP_ECHO_URLS = ("https://api.ipify.org", "https://checkip.amazonaws.com")


class ListingRetrievalError(Exception):
    """A safe, user-facing error while retrieving a public listing page."""

    def __init__(self, message: str, code: str = "LISTING_PAGE_UNAVAILABLE", status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class RetrievedListingContent:
    source_url: str
    cleaned_text: str
    content_bytes: int
    redirect_count: int


class _ListingHTMLParser(HTMLParser):
    """Collect metadata, JSON data and visible text without executing page JavaScript."""

    _IGNORED_TAGS = {"style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"}
    _BLOCK_TAGS = {"article", "aside", "br", "div", "h1", "h2", "h3", "h4", "li", "p", "section", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self.title_parts: list[str] = []
        self.visible_lines: list[str] = []
        self._line_buffer: list[str] = []
        self._ignored_depth = 0
        self._in_title = False
        self._script_kind: str | None = None
        self._script_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}

        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content", "").strip()
            if key and content:
                self.metadata[key.lower()] = content
            return

        if tag == "title":
            self._in_title = True
            return

        if tag == "script":
            script_type = attributes.get("type", "").lower()
            script_id = attributes.get("id", "").lower()
            if "ld+json" in script_type or script_id == "__next_data__":
                self._script_kind = "json"
                self._script_buffer = []
            else:
                self._script_kind = "ignored"
            return

        if tag in self._IGNORED_TAGS:
            self._flush_line()
            self._ignored_depth += 1
            return

        if tag in self._BLOCK_TAGS:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if tag == "script":
            if self._script_kind == "json" and self._script_buffer:
                content = "".join(self._script_buffer)
                if len(content) <= MAX_STRUCTURED_SCRIPT_LENGTH:
                    self.json_scripts.append(content)
            self._script_kind = None
            self._script_buffer = []
            return
        if tag in self._IGNORED_TAGS:
            self._flush_line()
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if tag in self._BLOCK_TAGS:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._script_kind:
            if self._script_kind == "json":
                self._script_buffer.append(data)
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._ignored_depth == 0:
            self._line_buffer.append(data)

    def close(self) -> None:
        self._flush_line()
        super().close()

    def _flush_line(self) -> None:
        value = re.sub(r"\s+", " ", " ".join(self._line_buffer)).strip()
        if value:
            self.visible_lines.append(value)
        self._line_buffer = []


def _is_blocked_hostname(hostname: str) -> bool:
    """Reject obvious loopback/private/link-local destinations to prevent SSRF.

    This is a lightweight, dependency-free check: literal IP addresses are parsed
    directly (no network access required), and a short list of well-known local
    hostnames/suffixes is blocked. It does not perform DNS resolution, so it will
    not catch DNS-rebinding attacks — an acceptable trade-off for this feature.
    """

    host = hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOSTNAME_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_listing_url(value: str) -> str:
    """Validate a public listing URL and reject unsupported/unsafe destinations."""

    candidate = value.strip()
    if len(candidate) > MAX_URL_LENGTH:
        raise ListingRetrievalError(
            "That listing URL is too long. Copy and paste the listing text instead.",
            code="INVALID_LISTING_URL",
            status_code=400,
        )

    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ListingRetrievalError(
            "That listing URL is invalid. Check it and try again.",
            code="INVALID_LISTING_URL",
            status_code=400,
        ) from exc

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ListingRetrievalError(
            "Use an HTTP or HTTPS property listing URL.",
            code="INVALID_LISTING_URL",
            status_code=400,
        )
    if parsed.username or parsed.password or port is not None:
        raise ListingRetrievalError(
            "That listing URL contains unsupported connection details.",
            code="INVALID_LISTING_URL",
            status_code=400,
        )
    if _is_blocked_hostname(hostname):
        raise ListingRetrievalError(
            "That listing URL points to an unsupported destination. Copy and paste the listing text instead.",
            code="UNSUPPORTED_LISTING_URL",
            status_code=400,
        )
    return candidate


def _is_blocked_page(html: str, title: str, visible_text: str) -> bool:
    # A valid page may include a Cloudflare CSP entry such as
    # `https://challenges.cloudflare.com` even after Chromium has completed
    # the challenge and rendered the listing. Inspect page-facing content for
    # challenge markers; do not reject a listing merely because its raw HTML
    # mentions Cloudflare.
    sample = f"{title}\n{visible_text}".lower()
    blocked_markers = (
        "access denied",
        "captcha",
        "checking your browser",
        "enable javascript and cookies",
        "verify you are human",
        "unusual traffic",
        "robot check",
        "just a moment",
    )
    return any(marker in sample for marker in blocked_markers)


def _clean_visible_lines(lines: list[str]) -> list[str]:
    noise_patterns = (
        r"^recommended listings?$",
        r"^similar properties?$",
        r"^mortgage calculator$",
        r"^contact agent$",
        r"^sign in$",
        r"^log in$",
        r"^cookie preferences?$",
    )
    cleaned: list[str] = []
    previous = ""
    for line in lines:
        normalised = re.sub(r"\s+", " ", line).strip()
        if not normalised or normalised == previous:
            continue
        if any(re.search(pattern, normalised, re.IGNORECASE) for pattern in noise_patterns):
            continue
        cleaned.append(normalised)
        previous = normalised
    return cleaned


def _parse_structured_scripts(scripts: list[str]) -> list[object]:
    parsed: list[object] = []
    for script in scripts:
        try:
            parsed.append(json.loads(script))
        except (TypeError, json.JSONDecodeError):
            continue
    return parsed


def extract_listing_content(html: str, source_url: str) -> str:
    """Extract bounded listing evidence from HTML without executing any page code."""

    parser = _ListingHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser should be tolerant, but never fail the API with parser internals.
        raise ListingRetrievalError(
            "NearHome could not read this listing page. Copy the listing details and paste the text instead.",
        ) from exc

    title = " ".join(parser.title_parts).strip() or parser.metadata.get("og:title", "")
    visible_lines = _clean_visible_lines(parser.visible_lines)
    visible_text = "\n".join(visible_lines)
    if _is_blocked_page(html, title, visible_text):
        raise ListingRetrievalError(
            "NearHome could not access this listing page. Copy the listing details "
            "and paste the text instead.",
            code="LISTING_PAGE_UNAVAILABLE",
            status_code=422,
        )

    structured = _parse_structured_scripts(parser.json_scripts)
    parts = [f"Source URL: {source_url}"]
    if title:
        parts.append(f"Page title: {title}")
    for key in ("description", "og:title", "og:description", "og:url"):
        if parser.metadata.get(key):
            parts.append(f"{key}: {parser.metadata[key]}")
    if structured:
        parts.append("Structured page data (untrusted listing evidence):")
        parts.append(json.dumps(structured, ensure_ascii=False, separators=(",", ":")))
    if visible_text:
        parts.append("Visible listing-page text:")
        parts.append(visible_text)

    content = "\n".join(parts)
    meaningful = re.search(
        r"(?:S?\$|price|sq\.?\s*ft|sqft|sqm|room|bedroom|hdb|condo|address)",
        content,
        re.IGNORECASE,
    )
    if len(content) < 80 or not meaningful:
        raise ListingRetrievalError(
            "NearHome could not read usable listing details from this page. Copy the "
            "listing details and paste the text instead.",
            code="LISTING_PAGE_UNAVAILABLE",
            status_code=422,
        )

    return content[:MAX_CONTENT_LENGTH]


def _fetch_via_http(source_url: str) -> RetrievedListingContent:
    """Fetch a public property listing page with bounded redirects and response size."""

    current_url = validate_listing_url(source_url)
    if settings.app_env == "development":
        logger.info("SMART_PASTE_URL_VALIDATED", host=urlsplit(current_url).hostname)

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "NearHome Listing Importer/1.0",
    }
    redirect_count = 0

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            while True:
                with client.stream("GET", current_url) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location or redirect_count >= MAX_REDIRECTS:
                            raise ListingRetrievalError(
                                "NearHome could not follow this listing page. Copy the "
                                "listing details and paste the text instead.",
                            )
                        current_url = validate_listing_url(urljoin(current_url, location))
                        redirect_count += 1
                        continue

                    if response.status_code in {401, 403, 429}:
                        raise ListingRetrievalError(
                            "NearHome could not access this listing page. Copy the listing "
                            "details and paste the text instead.",
                            code="LISTING_PAGE_UNAVAILABLE",
                            status_code=422,
                        )
                    if response.status_code >= 400:
                        raise ListingRetrievalError(
                            "NearHome could not retrieve this listing page. Copy the listing "
                            "details and paste the text instead.",
                        )

                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and "html" not in content_type and "text/plain" not in content_type:
                        raise ListingRetrievalError(
                            "NearHome received an unsupported listing-page response. Copy the "
                            "listing details and paste the text instead.",
                        )

                    chunks: list[bytes] = []
                    content_bytes = 0
                    for chunk in response.iter_bytes():
                        content_bytes += len(chunk)
                        if content_bytes > MAX_RESPONSE_BYTES:
                            raise ListingRetrievalError(
                                "This listing page is too large to process. Copy the listing "
                                "details and paste the text instead.",
                                code="LISTING_PAGE_TOO_LARGE",
                                status_code=422,
                            )
                        chunks.append(chunk)
                    html = b"".join(chunks).decode("utf-8", errors="replace")
                    break
    except ListingRetrievalError:
        raise
    except httpx.TimeoutException as exc:
        raise ListingRetrievalError(
            "NearHome timed out while retrieving this listing page. Copy the listing "
            "details and paste the text instead.",
            code="LISTING_PAGE_TIMEOUT",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ListingRetrievalError(
            "NearHome could not reach this listing page. Copy the listing details and paste the text instead.",
        ) from exc

    cleaned_text = extract_listing_content(html, current_url)
    if settings.app_env == "development":
        logger.info(
            "SMART_PASTE_PAGE_FETCHED",
            status=response.status_code,
            content_type=content_type or "unknown",
            content_bytes=content_bytes,
            redirect_count=redirect_count,
        )
        logger.info("SMART_PASTE_CONTENT_EXTRACTED", character_count=len(cleaned_text))
    return RetrievedListingContent(current_url, cleaned_text, content_bytes, redirect_count)


# Error codes from a plain HTTP fetch that plausibly mean "a bot-detection challenge
# or slow anti-bot gate stood in the way" — worth retrying with a real browser engine.
# Codes that mean the URL itself is invalid/unsafe, or that the response was already
# too large, are not retried since a browser fetch would not change that outcome.
_HEADLESS_FALLBACK_CODES = {"LISTING_PAGE_UNAVAILABLE", "LISTING_PAGE_TIMEOUT"}

HEADLESS_NAVIGATION_TIMEOUT_MS = 15_000
HEADLESS_SETTLE_MS = 1_500
HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_playwright_slots = threading.BoundedSemaphore(value=settings.playwright_max_concurrency)


def _playwright_timeout_ms() -> int:
    return settings.playwright_timeout_seconds * 1_000


def _fetch_via_headless_browser(source_url: str) -> RetrievedListingContent:
    """Render a page with a real browser engine, as a fallback when a plain HTTP
    fetch is blocked (e.g. by a Cloudflare JS challenge that a scripted request
    can never pass because it never executes any page JavaScript).

    This is slower and heavier than `_fetch_via_http`, so it is only attempted
    after that fetch fails with a bot-detection-shaped error.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    if not _playwright_slots.acquire(timeout=settings.playwright_timeout_seconds):
        raise ListingRetrievalError(
            "NearHome is already checking another listing page. Copy the listing details and paste the text instead.",
            code="LISTING_BROWSER_BUSY",
            status_code=503,
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                downloads_path="/tmp",
                # Cloud Run's container sandbox requires this Chromium flag.
                # URL validation still runs before browser navigation.
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = None
            try:
                context = browser.new_context(
                    user_agent=HEADLESS_USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                )
                if hasattr(context, "route"):
                    context.route(
                        "**/*",
                        lambda route: route.abort()
                        if route.request.resource_type in {"image", "media", "font"}
                        else route.continue_(),
                    )
                page = context.new_page()
                response = page.goto(
                    source_url,
                    timeout=_playwright_timeout_ms(),
                    wait_until="domcontentloaded",
                )
                # Give a short grace period for a client-side challenge/redirect to settle.
                page.wait_for_timeout(HEADLESS_SETTLE_MS)
                final_url = validate_listing_url(page.url)
                html = page.content()
                status_code = response.status if response else None
            finally:
                if context is not None and hasattr(context, "close"):
                    context.close()
                browser.close()
    except PlaywrightError as exc:
        logger.warning(
            "smart_paste_headless_failed",
            error_category="browser",
            error_type=type(exc).__name__,
        )
        raise ListingRetrievalError(
            "NearHome could not render this listing page. Copy the listing details and paste the text instead.",
        ) from exc
    finally:
        _playwright_slots.release()

    if status_code is not None and status_code >= 400 and status_code not in {401, 403, 429}:
        raise ListingRetrievalError(
            "NearHome could not retrieve this listing page. Copy the listing details and paste the text instead.",
        )
    if len(html.encode("utf-8", errors="ignore")) > MAX_RESPONSE_BYTES:
        raise ListingRetrievalError(
            "This listing page is too large to process. Copy the listing details and paste the text instead.",
            code="LISTING_PAGE_TOO_LARGE",
            status_code=422,
        )

    cleaned_text = extract_listing_content(html, final_url)
    if settings.app_env == "development":
        logger.info("SMART_PASTE_HEADLESS_FETCH_SUCCEEDED", character_count=len(cleaned_text))
    return RetrievedListingContent(final_url, cleaned_text, len(html), 0)


def retrieve_listing_content(source_url: str) -> RetrievedListingContent:
    """Fetch a public property listing page.

    Tries a fast, lightweight HTTP fetch first. If that is blocked by what looks
    like bot detection (or times out), automatically retries once with a headless
    browser, which can pass basic JS-based challenges that a plain HTTP client
    never can.
    """

    try:
        return _fetch_via_http(source_url)
    except ListingRetrievalError as http_error:
        if http_error.code not in _HEADLESS_FALLBACK_CODES:
            raise
        if not settings.enable_playwright_fallback:
            logger.info("smart_paste_headless_disabled", error_category="browser")
            raise
        if settings.app_env == "development":
            logger.info("SMART_PASTE_HTTP_FETCH_BLOCKED_RETRYING_HEADLESS", code=http_error.code)
        try:
            return _fetch_via_headless_browser(source_url)
        except ListingRetrievalError:
            # The plain-fetch error is generally the more informative one to show the user.
            raise http_error from None


def run_egress_diagnostic() -> dict[str, object]:
    """Return bounded, content-free facts for the fixed egress experiment.

    This function intentionally has no caller-supplied URL. It never returns
    HTML, cookies, headers, credentials, or extracted listing text, and it does
    not call Groq. The normal Smart Paste endpoint remains responsible for the
    real extraction flow.
    """

    ip_results: list[dict[str, str | None]] = []
    for endpoint in DIAGNOSTIC_IP_ECHO_URLS:
        try:
            response = httpx.get(endpoint, timeout=5.0, follow_redirects=False)
            value = response.text.strip()
            ip_results.append(
                {
                    "endpoint": endpoint,
                    "ip": value if response.status_code == 200 and len(value) <= 64 else None,
                }
            )
        except httpx.HTTPError:
            ip_results.append({"endpoint": endpoint, "ip": None})

    started = time.monotonic()
    http_result: dict[str, object] = {
        "attempted": True,
        "succeeded": False,
        "status": None,
        "final_url": None,
        "content_type": None,
        "page_title": None,
        "body_length": None,
        "challenge_detected": None,
        "usable_text_length": 0,
    }
    html = ""
    try:
        current_url = DIAGNOSTIC_PROPERTYGURU_URL
        redirects = 0
        headers = {"Accept": "text/html,application/xhtml+xml", "User-Agent": "NearHome Listing Importer/1.0"}
        with httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS), follow_redirects=False, headers=headers
        ) as client:
            while True:
                with client.stream("GET", current_url) as response:
                    if (
                        300 <= response.status_code < 400
                        and response.headers.get("location")
                        and redirects < MAX_REDIRECTS
                    ):
                        current_url = validate_listing_url(urljoin(current_url, response.headers["location"]))
                        redirects += 1
                        continue
                    raw = b"".join(chunk for chunk in response.iter_bytes())[:MAX_RESPONSE_BYTES]
                    html = raw.decode("utf-8", errors="replace")
                    parser = _ListingHTMLParser()
                    parser.feed(html)
                    parser.close()
                    title = " ".join(parser.title_parts).strip() or parser.metadata.get("og:title", "")
                    visible_text = "\n".join(_clean_visible_lines(parser.visible_lines))
                    challenge = response.status_code in {401, 403, 429} or _is_blocked_page(html, title, visible_text)
                    http_result.update(
                        {
                            "status": response.status_code,
                            "final_url": current_url,
                            "content_type": response.headers.get("content-type", "").split(";", 1)[0] or None,
                            "page_title": title[:200] or None,
                            "body_length": len(raw),
                            "challenge_detected": challenge,
                        }
                    )
                    if response.status_code < 400 and not challenge:
                        try:
                            cleaned = extract_listing_content(html, current_url)
                        except ListingRetrievalError:
                            pass
                        else:
                            http_result["succeeded"] = True
                            http_result["usable_text_length"] = len(cleaned)
                    break
    except (httpx.HTTPError, ListingRetrievalError, ValueError):
        # The public result intentionally reports only the failed outcome; it
        # never includes network exception internals or response payloads.
        pass

    headless_result: dict[str, object] = {"attempted": False, "succeeded": False, "usable_text_length": 0}
    if not http_result["succeeded"] and settings.enable_playwright_fallback:
        headless_result["attempted"] = True
        try:
            rendered = _fetch_via_headless_browser(DIAGNOSTIC_PROPERTYGURU_URL)
        except ListingRetrievalError:
            pass
        else:
            headless_result.update({"succeeded": True, "usable_text_length": len(rendered.cleaned_text)})

    usable = int(http_result["usable_text_length"]) or int(headless_result["usable_text_length"])
    return {
        "outbound_ip_checks": ip_results,
        "propertyguru": {
            "source": "fixed_allowlisted_test_listing",
            "http": http_result,
            "playwright": headless_result,
            "groq_attempted": False,
            "groq_eligible": usable >= 30,
            "total_latency_ms": round((time.monotonic() - started) * 1000, 1),
        },
    }
