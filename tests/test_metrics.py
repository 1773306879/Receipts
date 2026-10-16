"""Tests for the rules that pick an official number. Run them with:  uv run pytest

The test data in tests/fixtures/ is real SEC data, cut down (see make_fixtures.py).
The expected numbers were checked against the companies' actual 10-K filings.
"""

import json
from pathlib import Path

import pytest

from receipts.metrics import (
    CantVerify,
    available_years,
    calendar_year,
    describe_years,
    find_annual_value,
    is_financial_company,
    same_number,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(ticker):
    return json.loads((FIXTURES / f"{ticker}.json").read_text())


def revenue(ticker, year, **options):
    return find_annual_value(load(ticker), "revenue", year, **options)


# --- Picking the right number ---------------------------------------------------------

def test_apple_fiscal_year_ends_in_september():
    found = revenue("AAPL", 2024)
    assert found["value"] == 391_035_000_000
    assert (found["period_start"], found["period_end"]) == ("2023-10-01", "2024-09-28")
    # Apple stopped using the "Revenues" tag after 2018, so the next tag on the list is used.
    assert found["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_total_revenue_is_preferred_over_net_sales():
    # Walmart's fiscal 2025: total revenue $680.985B, of which net sales $674.538B.
    found = revenue("WMT", 2025)
    assert found["value"] == 680_985_000_000
    assert found["tag"] == "Revenues"


def test_tags_are_tried_year_by_year():
    # Alphabet has no "Revenues" value for 2022, but has one under the next tag.
    found = revenue("GOOGL", 2022)
    assert found["value"] == 282_836_000_000
    assert found["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_a_zero_under_one_tag_does_not_hide_the_real_revenue():
    # Flowserve uses "Revenues" for an empty cell in a table (0). Its real 2024 sales were $4.56B.
    assert revenue("FLS", 2024)["value"] == 4_557_806_000


def test_tags_that_disagree_are_refused():
    # In Virtual Systems' 2025 report, "Revenues" is attached to a $185,971 expense note,
    # while its real revenue is about $22.4M. We can't tell which one is meant, so we refuse.
    with pytest.raises(CantVerify):
        revenue("VTSI", 2025)


def test_financial_companies_need_a_total_revenue_tag():
    # M&T Bank only reports fee revenue under the contract tag ($1.5B of about $9.3B),
    # so for a bank that tag is never treated as total revenue.
    assert revenue("MTB", 2024, financial_company=True) is None


def test_quarterly_numbers_are_ignored():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        # A three-month number from a quarterly report (10-Q)...
        {"start": "2024-01-01", "end": "2024-03-31", "val": 10, "accn": "a", "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"},
        # ...and a three-month number inside an annual report. Neither is a full year.
        {"start": "2024-10-01", "end": "2024-12-31", "val": 20, "accn": "b", "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
    ]}}}}}
    assert find_annual_value(facts, "revenue", 2024) is None


@pytest.mark.parametrize("year", [1990, 2035])
def test_missing_years_are_none_not_zero(year):
    assert revenue("AAPL", year) is None


# --- Fiscal-year names ---------------------------------------------------------------

def test_year_named_after_the_year_it_ends_in():
    # Walmart's fiscal 2025 ended in January 2025.
    assert revenue("WMT", 2025)["period_end"] == "2025-01-31"


def test_year_named_after_the_year_it_starts_in():
    # Home Depot's fiscal 2024 ended in February 2025.
    found = revenue("HD", 2024)
    assert found["value"] == 159_514_000_000
    assert found["period_end"] == "2025-02-02"
    assert found["naming"]["subtract"] == 1


def test_two_fiscal_years_ending_in_the_same_calendar_year():
    # Wendy's fiscal 2022 ended on 2023-01-01 and fiscal 2023 ended on 2023-12-31.
    assert revenue("WEN", 2022)["value"] == 2_095_505_000
    assert revenue("WEN", 2023)["value"] == 2_181_578_000


def test_wrong_year_labels_in_old_reports_are_ignored():
    # The SEC data labels several of Walmart's and NVIDIA's older reports one year off.
    # Walmart's fiscal 2013 ended on 2013-01-31; NVIDIA's fiscal 2010 ended on 2010-01-31.
    walmart = revenue("WMT", 2013)
    assert (walmart["value"], walmart["period_end"]) == (468_651_000_000, "2013-01-31")
    assert revenue("NVDA", 2010)["value"] == 3_326_445_000


def test_recent_reports_decide_the_naming():
    # Most of TJX's reports carry start-year labels, but TJX calls the year ended
    # 2022-01-29 "fiscal 2022", as its 5 most recent reports do.
    found = revenue("TJX", 2022)
    assert (found["value"], found["period_end"]) == (48_550_000_000, "2022-01-29")


def test_unclear_naming_is_refused():
    # The Cheesecake Factory's recent reports disagree with each other about year labels.
    with pytest.raises(CantVerify):
        revenue("CAKE", 2024)


def test_two_periods_with_the_same_name_are_refused():
    # Global Payments changed its year end in 2016: the year ended 2016-05-31 and
    # calendar 2016 could both be called "2016".
    with pytest.raises(CantVerify):
        revenue("GPN", 2016)


@pytest.mark.parametrize("end, year", [("2024-09-28", 2024), ("2025-01-31", 2025), ("2023-01-01", 2022), ("2009-01-03", 2008)])
def test_calendar_year_steps_back_one_week(end, year):
    assert calendar_year(end) == year


# --- Restatements ---------------------------------------------------------------------

def test_restated_numbers_show_the_original_too():
    # Dollar Tree first reported $30.6B for fiscal 2023, then $16.8B after selling Family Dollar.
    found = revenue("DLTR", 2023)
    assert found["value"] == 16_781_100_000
    assert found["originally_reported"]["value"] == 30_603_800_000
    assert found["originally_reported"]["filed"] == "2024-03-20"


def test_restatement_is_found_even_when_the_tag_changed():
    # Microsoft reported $89.95B for fiscal 2017 (tag SalesRevenueNet), then $96.57B after
    # a 2018 accounting rule change (under a different tag).
    found = revenue("MSFT", 2017)
    assert found["value"] == 96_571_000_000
    assert found["originally_reported"]["value"] == 89_950_000_000


def test_unchanged_numbers_have_no_original():
    assert revenue("AAPL", 2024)["originally_reported"] is None


def test_rounding_is_not_a_restatement():
    # NVIDIA's fiscal 2014: $4,130,162 thousand at first, later shown as $4,130 million.
    assert revenue("NVDA", 2014)["originally_reported"] is None


@pytest.mark.parametrize("a, b, same", [
    (4_130_162_000, 4_130_000_000, True),
    (48_549_982_000, 48_550_000_000, True),
    (89_950_000_000, 96_571_000_000, False),
    (0, 185_971, False),
    (1_234_567, 1_234_568, False),
])
def test_same_number(a, b, same):
    assert same_number(a, b) is same


# --- Small helpers --------------------------------------------------------------------

def test_available_years():
    years = available_years(load("AAPL"), "revenue")
    assert 2024 in years and 2025 in years
    assert years == sorted(years)


def test_describe_years():
    assert describe_years([2008, 2009, 2010, 2012, 2014, 2015]) == "2008-2010, 2012, 2014-2015"
    assert describe_years([]) == "none"


@pytest.mark.parametrize("sic, financial", [("6021", True), ("6798", True), ("3571", False), ("", False), (None, False)])
def test_is_financial_company(sic, financial):
    assert is_financial_company(sic) is financial
