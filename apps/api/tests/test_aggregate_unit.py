"""Unit tests for analytics bucketing helpers (no DB)."""

from datetime import date

from app.analytics.aggregate import _bucket_key


def test_bucket_year_month():
    d = date(2024, 3, 15)
    assert _bucket_key("year", {}, d) == "2024"
    assert _bucket_key("month", {}, d) == "2024-03"


def test_bucket_component_empty():
    assert _bucket_key("component", {"Component": "-"}, None) == "(empty)"
    assert _bucket_key("component", {"Component": "기술지원"}, None) == "기술지원"
