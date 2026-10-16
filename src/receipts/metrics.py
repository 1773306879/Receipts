"""Finding one official yearly number (like revenue) in a company's SEC data.

Companies don't all use the same label (the SEC calls it a "tag") for the same
thing, so each metric has a short list of tags to try, in order of preference.
"""

from collections import Counter
from datetime import date

from receipts import sec

METRICS = {
    "revenue": {
        "label": "Revenue",
        "definition": "Total revenue for the fiscal year, as reported in the annual report (10-K).",
        "unit": "USD",
        # "Revenues" comes first because it is the total. Walmart, for example, also
        # reports net sales under the second tag, which leaves out membership income.
        "tags": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ],
    },
}

ANNUAL_FORMS = {"10-K", "10-K/A"}
# A fiscal year is 52 or 53 weeks long, so we accept periods of roughly one year.
MIN_DAYS, MAX_DAYS = 350, 380
# A company's own annual report for a year is filed within a few months after that year ends.
OWN_REPORT_MAX_DAYS = 200


def _days_between(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _is_annual(fact):
    """True for a number from an annual report (10-K) that covers about one year."""
    return (
        fact.get("form") in ANNUAL_FORMS
        and "start" in fact
        and MIN_DAYS <= _days_between(fact["start"], fact["end"]) + 1 <= MAX_DAYS
    )


def _annual_facts(facts, metric, tag):
    """All the yearly values the company ever reported under one tag."""
    reported = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(METRICS[metric]["unit"], [])
    return [fact for fact in reported if _is_annual(fact)]


def fiscal_year_names(facts, metric):
    """Work out what the company itself calls each fiscal year, keyed by the date the year ends.

    Companies name fiscal years differently. Walmart calls the year that ended on
    2025-01-31 "fiscal 2025", but Target calls the year that ended on 2025-02-01
    "fiscal 2024". So we don't guess: each annual report says which fiscal year it
    is about (the SEC's "fy" field), and we take the name from the year's own report.

    Older years that never had their own report in this data get the company's usual
    naming pattern, and are marked as estimated.

    Returns {end date: (fiscal year, "own report" or "estimated")}.
    """
    names = {}
    for tag in METRICS[metric]["tags"]:
        for fact in _annual_facts(facts, metric, tag):
            end_year = date.fromisoformat(fact["end"]).year
            filed_soon_after = 0 < _days_between(fact["end"], fact["filed"]) <= OWN_REPORT_MAX_DAYS
            if filed_soon_after and fact.get("fy") in (end_year - 1, end_year):
                names[fact["end"]] = (fact["fy"], "own report")

    # The usual pattern: does this company name a year after the calendar year it ends in (0), or the one before (1)?
    gaps = Counter(date.fromisoformat(end).year - year for end, (year, _) in names.items())
    usual_gap = gaps.most_common(1)[0][0] if gaps else 0
    for tag in METRICS[metric]["tags"]:
        for fact in _annual_facts(facts, metric, tag):
            if fact["end"] not in names:
                names[fact["end"]] = (date.fromisoformat(fact["end"]).year - usual_gap, "estimated")
    return names


def find_annual_value(facts, metric, fiscal_year):
    """Return the official number for one fiscal year, or None if the company didn't report it.

    The rules (each one has a test in tests/test_metrics.py):
    1. Only annual reports (form 10-K) count, and the period must be about one year long.
    2. "Fiscal year N" means what the company itself calls fiscal year N (see fiscal_year_names).
    3. Each number is repeated in later reports for comparison, and is sometimes
       corrected ("restated"). We use the most recent version, and also return the
       originally reported one when it is different.
    4. Tags are tried in order. The first tag that has this year wins.
    Missing data is never treated as zero: we return None instead.
    """
    names = fiscal_year_names(facts, metric)
    for tag in METRICS[metric]["tags"]:
        matches = [fact for fact in _annual_facts(facts, metric, tag) if names[fact["end"]][0] == fiscal_year]
        if not matches:
            continue
        latest = max(matches, key=lambda fact: fact["filed"])
        same_period = [fact for fact in matches if (fact["start"], fact["end"]) == (latest["start"], latest["end"])]
        original = min(same_period, key=lambda fact: fact["filed"])
        return {
            "value": latest["val"],
            "unit": METRICS[metric]["unit"],
            "period_start": latest["start"],
            "period_end": latest["end"],
            "days": _days_between(latest["start"], latest["end"]) + 1,
            "year_name_source": names[latest["end"]][1],
            "form": latest["form"],
            "filed": latest["filed"],
            "accession_number": latest["accn"],
            "tag": tag,
            "originally_reported": None if original["val"] == latest["val"] else {
                "value": original["val"],
                "filed": original["filed"],
                "accession_number": original["accn"],
            },
        }
    return None


def available_years(facts, metric):
    """The fiscal years for which we can find this metric, oldest first."""
    return sorted({year for year, _ in fiscal_year_names(facts, metric).values()})


def lookup(ticker, metric, fiscal_year):
    """Download what we need from the SEC and return a "receipt": the number plus where it came from."""
    if metric not in METRICS:
        raise sec.SecError(f"Unknown metric {metric!r}. Available: {', '.join(METRICS)}")
    company = sec.find_company(ticker)
    facts = sec.company_facts(company["cik"])
    found = find_annual_value(facts, metric, fiscal_year)
    if found is None:
        years = available_years(facts, metric)
        known = f"{years[0]}-{years[-1]}" if years else "none"
        raise sec.SecError(
            f"{company['name']} has no {METRICS[metric]['label'].lower()} figure for fiscal {fiscal_year} "
            f"in its annual reports. Years we can find: {known}."
        )
    if found["originally_reported"]:
        found["originally_reported"]["link"] = sec.filing_link(company["cik"], found["originally_reported"]["accession_number"])
    return {
        **company,
        "metric": metric,
        "label": METRICS[metric]["label"],
        "definition": METRICS[metric]["definition"],
        "fiscal_year": fiscal_year,
        **found,
        "link": sec.filing_link(company["cik"], found["accession_number"]),
    }
