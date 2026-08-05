"""Smart Paste Groq extraction tests without external network calls."""

import json

import pytest

from app.adapters.base import AdapterError
from app.adapters.live import groq as groq_module
from app.adapters.mock.groq import MockGroqAdapter
from app.core.config import settings
from app.services.smart_paste.flat_attributes import (
    normalise_listing_subtype,
    normalize_flat_type,
    resolve_flat_property_details,
)


def _field(value, raw_text=None, status="AVAILABLE"):
    return {
        "value": value,
        "raw_text": raw_text,
        "source_snippet": raw_text,
        "source_section": "listing",
        "model_confidence": "HIGH",
        "status": status,
    }


def _valid_payload():
    return {
        "asking_price": _field(650000, "$650,000"),
        "floor_area_sqm": _field(91, "91 sqm"),
        "address": _field("Blk 123 Bishan St 12", "Blk 123 Bishan St 12"),
        "flat_type": _field("4 ROOM", "4 ROOM"),
        "listing_flat_subtype": None,
        "flat_model": None,
        "remaining_lease_years": _field(None, None, "NOT_FOUND_IN_SOURCE_TEXT"),
        "lease_commencement_year": _field(None, None, "NOT_FOUND_IN_SOURCE_TEXT"),
        "property_category": "HDB",
        "extraction_warnings": [],
        "agent_claims": [],
    }


class FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error

        class Message:
            def __init__(self, content):
                self.content = content

        class Choice:
            def __init__(self, content):
                self.message = Message(content)

        class Response:
            def __init__(self, content):
                self.choices = [Choice(content)]

        return Response(self.content)


class FakeGroq:
    completions = None

    def __init__(self, **_kwargs):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeGroq.completions


class SchemaRejected(Exception):
    status_code = 400

    class Response:
        @staticmethod
        def json():
            return {
                "error": {
                    "message": (
                        "Generated JSON does not match the expected schema. "
                        "missing properties: 'flat_type'"
                    )
                }
            }

    response = Response()


class RetryCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = 0
        self.kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.calls == 1:
            raise SchemaRejected()
        return FakeCompletions(content=self.content).create(**kwargs)


def test_mock_groq_extracts_hdb_without_storey_field():
    result = MockGroqAdapter().extract(
        "4 ROOM FLAT FOR SALE\nBlk 123 Bishan St 12\nAsking Price: $650,000\n"
        "Floor Area: 91 sqm\nStorey: 10-12\nHigh floor, unblocked view"
    )

    assert result.candidates["asking_price"][0]["value"] == 650000
    assert result.candidates["floor_area_sqm"][0]["value"] == 91
    assert "storey_band" not in result.candidates


@pytest.mark.parametrize(
    ("raw_value", "flat_type", "subtype"),
    [
        ("4A", "4 ROOM", "4A"),
        ("4 Room HDB", "4 ROOM", None),
        ("5 Room (5STD) HDB", "5 ROOM", "5STD"),
    ],
)
def test_flat_type_normalizer_separates_room_category_and_listing_subtype(raw_value, flat_type, subtype):
    result = normalize_flat_type(raw_value)

    assert result.flat_type == flat_type
    assert result.listing_flat_subtype == subtype
    assert result.raw_value == raw_value


def test_mock_groq_extracts_propertyguru_style_flat_subtype_without_storey():
    result = MockGroqAdapter().extract(
        "4A HDB for sale\nBlk 123 Bishan St 12\nAsking Price: $650,000\n"
        "Floor Area: 91 sqm\nHigh floor, unblocked view"
    )

    assert result.candidates["flat_type"][0]["value"] == "4 ROOM"
    assert result.candidates["listing_flat_subtype"][0]["value"] == "4A"
    assert "storey_range" not in result.candidates


@pytest.mark.parametrize(
    ("raw_value", "flat_type", "flat_model"),
    [
        ("4A", "4 ROOM", "Model A"),
        ("4NG", "4 ROOM", "New Generation"),
        ("4S", "4 ROOM", "Simplified"),
        ("4I", "4 ROOM", "Improved"),
        ("4STD", "4 ROOM", "Standard"),
        ("4A2", "4 ROOM", "Model A2"),
        ("4PA", "4 ROOM", "Premium Apartment"),
        ("EA", "EXECUTIVE", "Apartment"),
        ("EM", "EXECUTIVE", "Maisonette"),
    ],
)
def test_listing_subtype_mapping_is_explicit_and_canonical(raw_value, flat_type, flat_model):
    result = normalise_listing_subtype(raw_value)

    assert result.status == "known"
    assert result.flat_type == flat_type
    assert result.flat_model == flat_model


def test_listing_subtype_normalization_handles_case_spacing_and_ambiguity():
    assert normalise_listing_subtype("  4 std ").flat_model == "Standard"
    assert normalise_listing_subtype("A").status == "ambiguous"
    unknown = normalise_listing_subtype("4ZZ")
    assert unknown.status == "unknown"
    assert unknown.flat_type == "4 ROOM"
    assert unknown.flat_model is None


def test_subtype_resolution_prefers_explicit_model_and_records_conflict():
    resolved = resolve_flat_property_details(
        flat_type="4 ROOM",
        raw_listing_subtype="4A",
        flat_model="Improved",
        flat_model_source="user_confirmed",
    )

    assert resolved.flat_model == "Improved"
    assert resolved.flat_model_source == "user_confirmed"
    assert resolved.subtype_conflicts == [{
        "field": "flat_model",
        "confirmed_value": "Improved",
        "derived_from_subtype": "Model A",
        "raw_listing_subtype": "4A",
        "status": "conflict",
    }]


def test_subtype_resolution_derives_missing_canonical_fields():
    resolved = resolve_flat_property_details(
        flat_type=None,
        raw_listing_subtype="4A",
        flat_model=None,
    )

    assert resolved.flat_type == "4 ROOM"
    assert resolved.flat_model == "Model A"
    assert resolved.flat_model_source == "derived_from_subtype"


def test_mock_groq_extracts_99co_style_flat_subtype():
    result = MockGroqAdapter().extract(
        "5 Room (5STD) HDB\nBlk 201 Tampines St 21\nAsking Price: $700,000\nFloor Area: 110 sqm"
    )

    assert result.candidates["flat_type"][0]["value"] == "5 ROOM"
    assert result.candidates["listing_flat_subtype"][0]["value"] == "5STD"


def test_mock_groq_does_not_infer_condo_from_recommendations():
    result = MockGroqAdapter().extract(
        "HDB 4 ROOM FLAT FOR SALE\nBlk 123 Bishan St 12\nRecommended condo nearby"
    )
    assert result.property_category == "HDB"


def test_groq_response_is_strictly_validated(monkeypatch):
    completions = FakeCompletions(content=json.dumps(_valid_payload()))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("HDB listing text")

    assert result.candidates["asking_price"][0]["value"] == 650000
    assert completions.kwargs["model"] == settings.groq_model
    assert completions.kwargs["response_format"]["type"] == "json_schema"
    assert completions.kwargs["response_format"]["json_schema"]["strict"] is True
    assert "storey_band" not in completions.kwargs["response_format"]["json_schema"]["schema"]["properties"]


def test_groq_markdown_fence_is_parsed_and_common_values_are_normalised(monkeypatch):
    payload = _valid_payload()
    payload["asking_price"]["value"] = "S$1,050,000"
    payload["floor_area_sqm"]["value"] = 1108
    payload["floor_area_sqm"]["raw_text"] = "1,108 sqft"
    payload["flat_type"]["value"] = "4-room"
    completions = FakeCompletions(content=f"```json\n{json.dumps(payload)}\n```")
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert result.candidates["asking_price"][0]["value"] == 1_050_000
    assert result.candidates["floor_area_sqm"][0]["value"] == 102.9
    assert result.candidates["flat_type"][0]["value"] == "4 ROOM"


def test_live_groq_normalises_flat_type_and_preserves_subtype(monkeypatch):
    payload = _valid_payload()
    payload["flat_type"] = _field("4A", "4A HDB",)
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("PropertyGuru listing text")

    assert result.candidates["flat_type"][0]["value"] == "4 ROOM"
    assert result.candidates["listing_flat_subtype"][0]["value"] == "4A"
    assert "storey_range" not in result.candidates


def test_live_groq_supports_subtype_only_extraction(monkeypatch):
    payload = _valid_payload()
    payload["flat_type"] = None
    payload["listing_flat_subtype"] = _field("4A", "4A")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert result.candidates["listing_flat_subtype"][0]["value"] == "4A"
    assert result.candidates["flat_type"][0]["value"] == "4 ROOM"
    assert result.candidates["flat_type"][0]["extraction_method"] == "derived_from_subtype"
    assert result.candidates["flat_model"][0]["value"] == "Model A"
    assert result.candidates["flat_model"][0]["extraction_method"] == "derived_from_subtype"


def test_groq_remaining_lease_years_extracts_number_from_wordy_value(monkeypatch):
    payload = _valid_payload()
    payload["remaining_lease_years"] = _field("99-year lease", "99-year lease")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert result.candidates["remaining_lease_years"][0]["value"] == 99


def test_remaining_lease_is_calculated_from_lease_commencement_year(monkeypatch):
    """The official-method calculation (99 - years elapsed) takes priority over any stated value."""
    payload = _valid_payload()
    payload["lease_commencement_year"] = _field(1990, "Lease Commencement Date: 1990")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    from datetime import date

    expected = max(0, 99 - (date.today().year - 1990))
    candidate = result.candidates["remaining_lease_years"][0]
    assert candidate["value"] == expected
    assert candidate["extraction_method"] == "calculated_from_lease_commencement"
    assert candidate["raw_text"] == "Lease Commencement Date: 1990"


def test_bare_tenure_statement_alone_does_not_fill_remaining_lease(monkeypatch):
    """No commencement year and no explicit remaining-lease figure -> leave it unfilled."""
    payload = _valid_payload()
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert "remaining_lease_years" not in result.candidates


def test_generic_flat_type_label_is_not_surfaced(monkeypatch):
    payload = _valid_payload()
    payload["flat_type"] = _field("HDB Flat", "HDB Flat")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert "flat_type" not in result.candidates


def test_floor_area_parses_even_when_value_has_extra_text(monkeypatch):
    payload = _valid_payload()
    payload["floor_area_sqm"] = _field("904 sqft", "904 sqft")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert result.candidates["floor_area_sqm"][0]["value"] == 84.0


def test_floor_area_parses_value_with_thousands_separator(monkeypatch):
    """A comma-thousands value (e.g. "1,345 sqft") must not be truncated to "1"."""
    payload = _valid_payload()
    payload["floor_area_sqm"] = _field("1,345 sqft", "1,345 sqft")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Property listing text")

    assert result.candidates["floor_area_sqm"][0]["value"] == round(1345 * 0.092903, 1)


def test_groq_partial_fields_are_returned_without_inventing_missing_values(monkeypatch):
    payload = _valid_payload()
    for field in ("asking_price", "floor_area_sqm", "flat_type"):
        payload[field] = _field(None, None, "NOT_FOUND_IN_SOURCE_TEXT")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("Address only")

    assert set(result.candidates) == {"address"}


def test_groq_accepts_null_lease_fields_for_listing_without_lease_data(monkeypatch):
    payload = _valid_payload()
    payload["remaining_lease_years"] = None
    payload["lease_commencement_year"] = None
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("99.co listing without lease information")

    assert "remaining_lease_years" not in result.candidates


def test_groq_recovers_subtype_from_flat_type_raw_evidence(monkeypatch):
    payload = _valid_payload()
    payload["flat_type"] = _field("5-Room", "5 Room (5STD) HDB")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("99.co listing")

    assert result.candidates["flat_type"][0]["value"] == "5 ROOM"
    assert result.candidates["listing_flat_subtype"][0]["value"] == "5STD"


def test_groq_normalises_noncanonical_subtype_output_from_raw_evidence(monkeypatch):
    payload = _valid_payload()
    payload["flat_type"] = _field("5-Room", "5 Room (5STD) HDB")
    payload["listing_flat_subtype"] = _field("5 Rooms", "5 Room (5STD) HDB")
    completions = FakeCompletions(content=json.dumps(payload))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("99.co listing")

    assert result.candidates["listing_flat_subtype"][0]["value"] == "5STD"


def test_groq_invalid_output_is_safe_error(monkeypatch):
    completions = FakeCompletions(content=json.dumps({"unexpected": True}))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    with pytest.raises(AdapterError, match="invalid structured extraction"):
        groq_module.LiveGroqAdapter().extract("HDB listing text")


def test_groq_retries_transient_schema_generation_error(monkeypatch):
    completions = RetryCompletions(json.dumps(_valid_payload()))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    result = groq_module.LiveGroqAdapter().extract("HDB listing text")

    assert completions.calls == 2
    assert result.candidates["flat_type"][0]["value"] == "4 ROOM"
    assert groq_module.SCHEMA_REPAIR_INSTRUCTION in completions.kwargs["messages"][0]["content"]


def test_groq_missing_key_is_configuration_error(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")

    with pytest.raises(AdapterError, match="GROQ_API_KEY") as exc_info:
        groq_module.LiveGroqAdapter()

    assert exc_info.value.error_code == "configuration"


def test_groq_rate_limit_maps_to_retryable_error():
    class RateLimited:
        status_code = 429

    error = groq_module.LiveGroqAdapter._provider_error(RateLimited())
    assert error.error_code == "rate_limit"
    assert error.retryable is True


def test_groq_request_too_large_maps_to_retryable_rate_limit_error():
    """Groq's tokens-per-minute cap check surfaces as a 413, not a 429."""

    class RequestTooLarge:
        status_code = 413

    error = groq_module.LiveGroqAdapter._provider_error(RequestTooLarge())
    assert error.error_code == "rate_limit"
    assert error.retryable is True


def test_groq_extract_sends_reasoning_effort_and_completion_cap(monkeypatch):
    completions = FakeCompletions(content=json.dumps(_valid_payload()))
    FakeGroq.completions = completions
    monkeypatch.setattr(groq_module, "Groq", FakeGroq)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    groq_module.LiveGroqAdapter().extract("x" * 20000)

    assert completions.kwargs["reasoning_effort"] == groq_module.GROQ_REASONING_EFFORT
    assert completions.kwargs["max_completion_tokens"] == groq_module.GROQ_MAX_COMPLETION_TOKENS
    assert len(completions.kwargs["messages"][1]["content"]) == groq_module.MAX_LLM_CONTENT_CHARS
