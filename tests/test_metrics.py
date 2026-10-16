"""Tests for the rules that pick an official number. Run them with:  uv run pytest

The expected values were checked by hand against each company's annual report.
"""

import json
from pathlib import Path

import pytest

from receipts.metrics import available_years, find_annual_value

FIXTURES = Path(__file__).parent / "fixtures"


def load(ticker):
    return json.loads((FIXTURES / f"{ticker}.json").read_text())


def test_apple_fiscal_year_ends_in_september():
    found = find_annual_value(load("AAPL"), "revenue", 2024)
    assert found["value"] == 391_035_000_000
    assert (found["period_start"], found["period_end"]) == ("2023-10-01", "2024-09-28")
    # Apple stopped using the "Revenues" tag after 2018, so the next tag on the list is used.
    assert found["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_total_revenue_is_preferred_over_net_sales():
    # Walmart's fiscal 2025: total revenue $680.985B, of which net sales $674.538B.
    found = find_annual_value(load("WMT"), "revenue", 2025)
    assert found["value"] == 680_985_000_000
    assert found["tag"] == "Revenues"


def test_year_named_after_the_year_it_ends_in():
    # Walmart's fiscal 2025 ended in January 2025.
    assert find_annual_value(load("WMT"), "revenue", 2025)["period_end"] == "2025-01-31"


def test_year_named_after_the_year_it_starts_in():
    # Home Depot's fiscal 2024 ended in February 2025.
    found = find_annual_value(load("HD"), "revenue", 2024)
    assert found["value"] == 159_514_000_000
    assert found["period_end"] == "2025-02-02"
    assert found["year_name_source"] == "own report"


def test_two_fiscal_years_ending_in_the_same_calendar_year():
    # Wendy's fiscal 2022 ended on 2023-01-01 and fiscal 2023 ended on 2023-12-31.
    wendys = load("WEN")
    assert find_annual_value(wendys, "revenue", 2022)["value"] == 2_095_505_000
    assert find_annual_value(wendys, "revenue", 2023)["value"] == 2_181_578_000


def test_tags_are_tried_year_by_year():
    # Alphabet has no "Revenues" value for 2022, but has one under the next tag.
    found = find_annual_value(load("GOOGL"), "revenue", 2022)
    assert found["value"] == 282_836_000_000
    assert found["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_restated_numbers_show_the_original_too():
    # Dollar Tree first reported $30.6B for fiscal 2023, then restated it to $16.8B after selling Family Dollar.
    found = find_annual_value(load("DLTR"), "revenue", 2023)
    assert found["value"] == 16_781_100_000
    assert found["originally_reported"]["value"] == 30_603_800_000
    assert found["originally_reported"]["filed"] == "2024-03-20"


def test_unchanged_numbers_have_no_original():
    assert find_annual_value(load("AAPL"), "revenue", 2024)["originally_reported"] is None


def test_old_years_without_their_own_report_are_marked_estimated():
    # Apple's fiscal 2008 only appears as a comparison figure in later reports.
    found = find_annual_value(load("AAPL"), "revenue", 2008)
    assert found["period_end"] == "2008-09-27"
    assert found["year_name_source"] == "estimated"


@pytest.mark.parametrize("year", [1990, 2035])
def test_missing_years_are_none_not_zero(year):
    assert find_annual_value(load("AAPL"), "revenue", year) is None


def test_quarterly_numbers_are_ignored():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        # A three-month number from a quarterly report (10-Q)...
        {"start": "2024-01-01", "end": "2024-03-31", "val": 10, "accn": "a", "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"},
        # ...and a three-month number inside an annual report. Neither is a full year.
        {"start": "2024-10-01", "end": "2024-12-31", "val": 20, "accn": "b", "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
    ]}}}}}
    assert find_annual_value(facts, "revenue", 2024) is None


def test_available_years():
    years = available_years(load("AAPL"), "revenue")
    assert 2024 in years and 2025 in years
    assert years == sorted(years)
