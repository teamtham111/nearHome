"""Listing-page retrieval tests with mocked HTTP responses."""

from uuid import uuid4

import httpx
import pytest

from app.adapters.base import LLMExtractionResult
from app.schemas.comparison import SmartPasteInput
from app.services.smart_paste import retrieval
from app.services.smart_paste import service as smart_paste_service

VALID_URL = "https://www.propertyguru.com.sg/listing/12345678"


def test_smart_paste_request_accepts_url_and_text_contracts():
    url_request = SmartPasteInput.model_validate({"sourceType": "url", "sourceUrl": VALID_URL})
    text_request = SmartPasteInput.model_validate({"sourceType": "text", "rawText": "A copied listing"})

    assert url_request.source_type == "url"
    assert url_request.source_url == VALID_URL
    assert text_request.source_type == "text"
    assert text_request.raw_text == "A copied listing"


def test_url_orchestration_retrieves_content_before_calling_grok(monkeypatch):
    class FakeDB:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

        def flush(self):
            self.added[0].id = uuid4()

        def commit(self):
            return None

        def refresh(self, _item):
            return None

    class FakeLLM:
        def __init__(self):
            self.received = ""

        def extract(self, cleaned_text):
            self.received = cleaned_text
            return LLMExtractionResult(
                candidates={
                    "address": [
                        {
                            "value": "217 Bishan Street 23",
                            "raw_text": "217 Bishan Street 23",
                            "source_snippet": "217 Bishan Street 23",
                            "source_section": "listing",
                            "model_confidence": "HIGH",
                            "final_confidence": "HIGH",
                            "verification_state": "UNVERIFIED",
                            "status": "AVAILABLE",
                        }
                    ]
                },
                extraction_warnings=[],
                agent_claims=[],
                property_category="HDB",
                model_name="test-model",
            )

    fake_llm = FakeLLM()
    monkeypatch.setattr(
        smart_paste_service,
        "retrieve_listing_content",
        lambda _url: retrieval.RetrievedListingContent(
            source_url=VALID_URL,
            cleaned_text="Page title: 4-room HDB\n217 Bishan Street 23\nS$928,000",
            content_bytes=80,
            redirect_count=0,
        ),
    )
    monkeypatch.setattr(smart_paste_service, "get_llm_adapter", lambda: fake_llm)
    monkeypatch.setattr(smart_paste_service, "listing_input_from_orm", lambda row: row)

    db = FakeDB()
    result, used_fallback = smart_paste_service.SmartPasteService(db).extract(
        uuid4(),
        "",
        source_url=VALID_URL,
        source_type="url",
    )

    assert used_fallback is False
    assert "217 Bishan Street 23" in fake_llm.received
    listing_input = db.added[-1]
    assert listing_input.raw_text == VALID_URL
    assert listing_input.source_url == VALID_URL
    assert result is listing_input


def _mock_client(monkeypatch, handler):
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(retrieval.httpx, "Client", factory)


def test_valid_listing_page_extracts_metadata_and_visible_listing_text(monkeypatch):
    html = """
    <html><head>
      <title>4-room HDB for sale</title>
      <meta name="description" content="217 Bishan Street 23" />
      <script type="application/ld+json">
        {"@type":"Offer","price":"928000","priceCurrency":"SGD"}
      </script>
    </head><body>
      <nav>Recommended listings</nav>
      <main><h1>217 Bishan Street 23</h1><p>S$928,000</p>
      <p>1,108 sqft · 4-room HDB</p></main>
      <footer>Contact agent</footer>
    </body></html>
    """

    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert str(request.url) == VALID_URL
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_client(monkeypatch, handler)
    result = retrieval.retrieve_listing_content(VALID_URL)

    assert result.source_url == VALID_URL
    assert "217 Bishan Street 23" in result.cleaned_text
    assert "928000" in result.cleaned_text
    assert "Recommended listings" not in result.cleaned_text
    assert result.content_bytes > 0


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/listing/123",
        "https://www.propertyguru.com.sg/listing/12345678",
        "https://www.99.co/singapore/sale/hdb-bishan-123456",
        "https://www.edgeprop.sg/property-listing/123",
        "https://www.propertyguru.com.sg/search",
    ],
)
def test_any_public_listing_site_url_is_accepted(url):
    """Smart Paste is no longer restricted to a single site — any public HTTP(S) URL is allowed."""
    assert retrieval.validate_listing_url(url) == url


@pytest.mark.parametrize(
    "url,code",
    [
        ("not-a-url", "INVALID_LISTING_URL"),
        ("ftp://example.com/listing/123", "INVALID_LISTING_URL"),
        ("http://user:pass@example.com/listing/123", "INVALID_LISTING_URL"),
        ("http://127.0.0.1/listing/123", "UNSUPPORTED_LISTING_URL"),
        ("http://localhost/listing/123", "UNSUPPORTED_LISTING_URL"),
        ("http://169.254.169.254/latest/meta-data/", "UNSUPPORTED_LISTING_URL"),
        ("http://192.168.1.5/listing/123", "UNSUPPORTED_LISTING_URL"),
        ("http://10.0.0.5/listing/123", "UNSUPPORTED_LISTING_URL"),
        ("http://[::1]/listing/123", "UNSUPPORTED_LISTING_URL"),
        ("http://service.internal/listing/123", "UNSUPPORTED_LISTING_URL"),
    ],
)
def test_invalid_or_unsafe_urls_are_rejected(url, code):
    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval.validate_listing_url(url)
    assert exc_info.value.code == code


def test_listing_page_403_returns_copy_text_fallback(monkeypatch):
    _mock_client(
        monkeypatch,
        lambda _request: httpx.Response(403, headers={"content-type": "text/html"}, text="Access denied"),
    )

    # Exercise the plain HTTP fetch directly — the orchestrator's headless-browser
    # fallback behavior for this same error code is covered separately below.
    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval._fetch_via_http(VALID_URL)
    assert exc_info.value.code == "LISTING_PAGE_UNAVAILABLE"
    assert exc_info.value.status_code == 422


def test_listing_page_timeout_returns_retryable_fallback(monkeypatch):
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("timed out", request=request)

    _mock_client(monkeypatch, handler)
    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval._fetch_via_http(VALID_URL)
    assert exc_info.value.code == "LISTING_PAGE_TIMEOUT"
    assert exc_info.value.status_code == 504


def test_captcha_page_is_not_treated_as_success(monkeypatch):
    html = "<html><head><title>Verify you are human</title></head><body>CAPTCHA</body></html>"
    _mock_client(
        monkeypatch,
        lambda _request: httpx.Response(200, headers={"content-type": "text/html"}, text=html),
    )

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval._fetch_via_http(VALID_URL)
    assert exc_info.value.code == "LISTING_PAGE_UNAVAILABLE"


def test_redirect_to_private_ip_is_rejected(monkeypatch):
    _mock_client(
        monkeypatch,
        lambda _request: httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/admin"},
        ),
    )

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval.retrieve_listing_content(VALID_URL)
    assert exc_info.value.code == "UNSUPPORTED_LISTING_URL"


def test_redirect_to_another_public_listing_site_is_followed(monkeypatch):
    html = """
    <html><head><title>4-room HDB for sale</title></head>
    <body><h1>217 Bishan Street 23</h1><p>S$928,000</p><p>1,108 sqft · 4-room HDB</p></body></html>
    """
    redirected_url = "https://www.99.co/singapore/sale/hdb-bishan-123456"

    def handler(request: httpx.Request):
        if str(request.url) == VALID_URL:
            return httpx.Response(302, headers={"location": redirected_url})
        assert str(request.url) == redirected_url
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_client(monkeypatch, handler)
    result = retrieval.retrieve_listing_content(VALID_URL)

    assert result.source_url == redirected_url
    assert result.redirect_count == 1
    assert "217 Bishan Street 23" in result.cleaned_text


def test_headless_fallback_is_used_when_http_fetch_is_blocked(monkeypatch):
    """A bot-challenge-shaped HTTP failure should trigger the headless-browser retry."""
    calls: list[str] = []

    def fake_http(_url):
        calls.append("http")
        raise retrieval.ListingRetrievalError("blocked", code="LISTING_PAGE_UNAVAILABLE", status_code=422)

    def fake_headless(_url):
        calls.append("headless")
        return retrieval.RetrievedListingContent(
            source_url=VALID_URL,
            cleaned_text="Rendered content: 217 Bishan Street 23, S$928,000",
            content_bytes=42,
            redirect_count=0,
        )

    monkeypatch.setattr(retrieval, "_fetch_via_http", fake_http)
    monkeypatch.setattr(retrieval, "_fetch_via_headless_browser", fake_headless)

    result = retrieval.retrieve_listing_content(VALID_URL)

    assert calls == ["http", "headless"]
    assert "217 Bishan Street 23" in result.cleaned_text


@pytest.mark.parametrize("code", ["INVALID_LISTING_URL", "UNSUPPORTED_LISTING_URL", "LISTING_PAGE_TOO_LARGE"])
def test_headless_fallback_is_skipped_for_non_bot_errors(monkeypatch, code):
    """Invalid/unsafe URLs and oversized responses should not trigger a browser launch."""
    calls: list[str] = []

    def fake_http(_url):
        calls.append("http")
        raise retrieval.ListingRetrievalError("not eligible for fallback", code=code, status_code=400)

    def fake_headless(_url):
        calls.append("headless")
        raise AssertionError("headless fallback should not run for this error code")

    monkeypatch.setattr(retrieval, "_fetch_via_http", fake_http)
    monkeypatch.setattr(retrieval, "_fetch_via_headless_browser", fake_headless)

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval.retrieve_listing_content(VALID_URL)

    assert calls == ["http"]
    assert exc_info.value.code == code


def test_original_http_error_surfaces_when_headless_fallback_also_fails(monkeypatch):
    def fake_http(_url):
        raise retrieval.ListingRetrievalError(
            "NearHome could not access this listing page.", code="LISTING_PAGE_UNAVAILABLE", status_code=422
        )

    def fake_headless(_url):
        raise retrieval.ListingRetrievalError("browser render failed", code="LISTING_PAGE_UNAVAILABLE")

    monkeypatch.setattr(retrieval, "_fetch_via_http", fake_http)
    monkeypatch.setattr(retrieval, "_fetch_via_headless_browser", fake_headless)

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval.retrieve_listing_content(VALID_URL)

    assert "could not access this listing page" in str(exc_info.value)


class _FakePlaywrightPage:
    def __init__(self, html: str, url: str, status: int):
        self._html = html
        self.url = url
        self._status = status

    def goto(self, _url, timeout=None, wait_until=None):  # noqa: ARG002
        return type("Response", (), {"status": self._status})()

    def wait_for_timeout(self, _ms):
        return None

    def content(self):
        return self._html


class _FakePlaywrightContext:
    def __init__(self, page: _FakePlaywrightPage):
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        return None


class _FakePlaywrightBrowser:
    def __init__(self, context: _FakePlaywrightContext):
        self._context = context

    def new_context(self, **_kwargs):
        return self._context

    def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser: _FakePlaywrightBrowser):
        self._browser = browser

    def launch(self, **_kwargs):
        return self._browser


class _FakeSyncPlaywright:
    def __init__(self, chromium: _FakeChromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_headless_browser_fetch_extracts_rendered_listing_content(monkeypatch):
    html = (
        "<html><head><title>4-room HDB for sale</title></head>"
        "<body><h1>217 Bishan Street 23</h1><p>S$928,000</p><p>1,108 sqft · 4-room HDB</p></body></html>"
    )
    page = _FakePlaywrightPage(html, url=VALID_URL, status=200)
    chromium = _FakeChromium(_FakePlaywrightBrowser(_FakePlaywrightContext(page)))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _FakeSyncPlaywright(chromium),
    )

    result = retrieval._fetch_via_headless_browser(VALID_URL)

    assert result.source_url == VALID_URL
    assert "217 Bishan Street 23" in result.cleaned_text
    assert "S$928,000" in result.cleaned_text


def test_headless_browser_allows_listing_with_normal_cloudflare_csp(monkeypatch):
    html = (
        "<html><head>"
        '<meta http-equiv="content-security-policy" '
        'content="default-src \'none\'; script-src https://challenges.cloudflare.com">'
        "<title>4 Room HDB for Sale in 529 Serangoon North Avenue 4</title>"
        "</head><body><h1>529 Serangoon North Avenue 4</h1>"
        "<p>S$670,000</p><p>1,184 sqft · HDB 4 Rooms · 99-year leasehold</p>"
        "</body></html>"
    )
    page = _FakePlaywrightPage(html, url="https://www.99.co/singapore/sale/529", status=200)
    chromium = _FakeChromium(_FakePlaywrightBrowser(_FakePlaywrightContext(page)))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _FakeSyncPlaywright(chromium),
    )

    result = retrieval._fetch_via_headless_browser(page.url)

    assert "529 Serangoon North Avenue 4" in result.cleaned_text
    assert "S$670,000" in result.cleaned_text


def test_headless_browser_fetch_rejects_blocked_page(monkeypatch):
    html = "<html><head><title>Just a moment...</title></head><body>Checking your browser, cloudflare</body></html>"
    page = _FakePlaywrightPage(html, url=VALID_URL, status=200)
    chromium = _FakeChromium(_FakePlaywrightBrowser(_FakePlaywrightContext(page)))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _FakeSyncPlaywright(chromium),
    )

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval._fetch_via_headless_browser(VALID_URL)
    assert exc_info.value.code == "LISTING_PAGE_UNAVAILABLE"


def test_playwright_disabled_keeps_copy_and_paste_fallback(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "enable_playwright_fallback", False)
    monkeypatch.setattr(
        retrieval,
        "_fetch_via_http",
        lambda _url: (_ for _ in ()).throw(
            retrieval.ListingRetrievalError("blocked", code="LISTING_PAGE_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        retrieval,
        "_fetch_via_headless_browser",
        lambda _url: (_ for _ in ()).throw(AssertionError("browser should remain disabled")),
    )

    with pytest.raises(retrieval.ListingRetrievalError) as exc_info:
        retrieval.retrieve_listing_content(VALID_URL)

    assert exc_info.value.code == "LISTING_PAGE_UNAVAILABLE"
