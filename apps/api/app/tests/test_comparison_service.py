from app.services.comparison_service import _buyer_fair_price


def test_buyer_fair_price_caps_contextual_rows_and_removes_audit_collection() -> None:
    rows = [{"transaction_id": str(index)} for index in range(25)]
    result = _buyer_fair_price(
        {
            "central_estimate": 800000,
            "comparables": rows,
            "all_comparables": rows * 10,
            "comparable_evidence": {"eligible_comparable_count": 4032},
        }
    )
    assert result["eligible_transaction_count"] == 4032
    assert len(result["displayed_comparables"]) == 10
    assert len(result["comparables"]) == 10
    assert "all_comparables" not in result


def test_buyer_fair_price_handles_short_and_empty_evidence() -> None:
    short = _buyer_fair_price({"displayed_comparables": [{"transaction_id": "one"}], "eligible_transaction_count": 1})
    empty = _buyer_fair_price({})
    assert len(short["displayed_comparables"]) == 1
    assert short["eligible_transaction_count"] == 1
    assert empty["displayed_comparables"] == []
    assert empty["eligible_transaction_count"] == 0
