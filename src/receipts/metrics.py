"""Finding one official yearly number (like revenue) in a company's SEC data.

The SEC's data is messy, so this file follows written rules. When the data doesn't
give one clear answer, it raises CantVerify instead of guessing: for a fact-checker,
a wrong number is much worse than no number.
"""

from collections import Counter
from datetime import date, timedelta

from receipts import sec

METRICS = {
    "revenue": {
        "label": "Revenue",
        "definition": "Total revenue for the fiscal year, as reported in the annual report (10-K).",
        "unit": "USD",
        # Companies label the same thing differently (the SEC calls a label a "tag"), so we
        # try these, best first. "Revenues" is the total. Walmart, for example, also reports
        # net sales under the second tag, which leaves out membership income.
        "tags": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        # This tag includes taxes the company collects and passes on (like alcohol tax),
        # so it is naturally a bit bigger than the others. It is only used as a last resort.
        "includes_pass_through_taxes": ["RevenueFromContractWithCustomerIncludingAssessedTax"],
        # For banks, insurers and real-estate trusts, the contract-revenue tag covers only
        # fees (M&T Bank: $1.5B of fees, out of about $9.3B), so we accept only a true total.
        "financial_company_tags": ["Revenues", "RevenuesNetOfInterestExpense"],
    },
}

ANNUAL_FORMS = {"10-K", "10-K/A"}
# A fiscal year is 52 or 53 weeks long, so we accept periods of roughly one year.
MIN_DAYS, MAX_DAYS = 350, 380
# A year's own annual report is filed within a few months after the year ends.
OWN_REPORT_MAX_DAYS = 200
# How many recent annual reports vote on the fiscal-year naming, and what share must agree.
VOTING_REPORTS = 5
VOTES_NEEDED = 0.8
# If a lower tag shows a number more than 2% bigger than the one we picked, the picked one probably isn't the total.
DISAGREEMENT_LIMIT = 0.02


class CantVerify(Exception):
    """The data doesn't give one clear answer, so we refuse instead of guessing."""


def is_financial_company(sic):
    """Banks, insurers and real-estate trusts have SEC industry codes (SIC) from 6000 to 6799."""
    return bool(sic) and 6000 <= int(sic) <= 6799


def same_number(a, b):
    """True if a and b are equal, or differ only because one of them was rounded to thousands or millions.

    NVIDIA reported fiscal 2014 revenue as $4,130,162 thousand, and later reports
    as $4,130 million. That is the same number, not a correction.
    """
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    for unit in (1_000_000, 1_000):
        if a % unit == 0 or b % unit == 0:
            return abs(a - b) < unit / 2
    return False


def _date(text):
    return date.fromisoformat(text)


def annual_facts(facts, tags, unit):
    """Every yearly value the company reported under these tags, as (tag, fact) pairs."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    found = []
    for tag in tags:
        for fact in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
            if fact.get("form") not in ANNUAL_FORMS or "start" not in fact:
                continue
            length = (_date(fact["end"]) - _date(fact["start"])).days + 1
            if MIN_DAYS <= length <= MAX_DAYS:
                found.append((tag, fact))
    return found


def calendar_year(end):
    """The calendar year a fiscal year belongs to, judged by its end date.

    Some 52/53-week years run a few days into January (Wendy's fiscal 2022 ended on
    2023-01-01), so we step back one week before reading the year.
    """
    return (_date(end) - timedelta(days=7)).year


def naming_rule(reported):
    """Work out how this company names its fiscal years, from its own annual reports.

    Walmart calls the year that ended 2025-01-31 "fiscal 2025": the calendar year minus 0.
    Target calls the year that ended 2025-02-01 "fiscal 2024": the calendar year minus 1.
    Each year's own annual report has a fiscal-year number in the SEC's data (the "fy"
    field). In older reports it is often off by one (TJX's reports before 2022 are), so
    only the most recent reports vote, and nearly all of them must agree.
    """
    vote_per_report = {}
    for _, fact in reported:
        days_until_filed = (_date(fact["filed"]) - _date(fact["end"])).days
        if 0 < days_until_filed <= OWN_REPORT_MAX_DAYS and fact.get("fy"):
            vote_per_report[fact["accn"]] = (fact["end"], calendar_year(fact["end"]) - fact["fy"])
    newest_first = sorted(vote_per_report.values(), reverse=True)[:VOTING_REPORTS]
    votes = Counter(vote for _, vote in newest_first)
    if not votes:
        return {"subtract": 0, "agreeing": 0, "reports": 0}
    subtract, agreeing = votes.most_common(1)[0]
    if subtract not in (-1, 0, 1) or agreeing < VOTES_NEEDED * len(newest_first):
        raise CantVerify("The company's recent annual reports disagree about how its fiscal years are named.")
    return {"subtract": subtract, "agreeing": agreeing, "reports": len(newest_first)}


def fiscal_year_name(end, rule):
    return calendar_year(end) - rule["subtract"]


def _one_report(this_year, newest):
    """The (tag, fact) pairs that come from the newest (or the oldest) annual report covering this year."""
    pick = max if newest else min
    _, fact = pick(this_year, key=lambda pair: (pair[1]["filed"], pair[1]["accn"]))
    return [(tag, f) for tag, f in this_year if f["accn"] == fact["accn"]]


def _choose(one_report, spec, tags, check=True):
    """Pick the value to use from one annual report, following the tag order.

    - A zero doesn't count if another tag in the same report has a real number.
      Flowserve uses the "Revenues" tag for an empty cell in a table, which reads as 0.
    - If a lower tag shows a clearly bigger number than the one picked, the picked tag
      probably isn't the total (or is junk), so we refuse instead of guessing.
    """
    by_tag = {}
    for tag, fact in one_report:
        by_tag.setdefault(tag, []).append(fact)
    anything_nonzero = any(fact["val"] != 0 for _, fact in one_report)
    usable = [
        tag for tag in tags
        if tag in by_tag and not (anything_nonzero and all(fact["val"] == 0 for fact in by_tag[tag]))
    ]
    chosen = usable[0]
    fact = by_tag[chosen][0]
    if not check:
        return chosen, fact
    if len({f["val"] for f in by_tag[chosen]}) > 1:
        raise CantVerify(f"The same report gives two different values under the tag {chosen}.")
    for other_tag in usable[1:]:
        if other_tag in spec["includes_pass_through_taxes"]:
            continue
        other = max(f["val"] for f in by_tag[other_tag])
        if other > fact["val"] * (1 + DISAGREEMENT_LIMIT):
            raise CantVerify(
                f"The report gives two different figures for this year: ${fact['val']:,} (tag {chosen}) "
                f"and ${other:,} (tag {other_tag}). We can't tell which one is the total, so we won't guess."
            )
    return chosen, fact


def find_annual_value(facts, metric, fiscal_year, financial_company=False):
    """Return the official number for one fiscal year, or None if the company didn't report it.

    The rules (each one has a test in tests/test_metrics.py):
    1. Only annual reports (form 10-K) count, and the period must be about one year long.
    2. "Fiscal year N" means what the company itself calls fiscal year N (see naming_rule).
       If two different periods would both get that name, we refuse.
    3. We use the newest report that covers the year, because numbers are sometimes
       corrected or re-presented later ("restated"). If the first report said something
       different, we return that too.
    4. Within a report, tags are tried in order, with the checks in _choose.
    Missing data is never treated as zero: we return None instead.
    """
    spec = METRICS[metric]
    tags = spec["financial_company_tags"] if financial_company else spec["tags"]
    reported = annual_facts(facts, tags, spec["unit"])
    rule = naming_rule(reported)
    ends = sorted({fact["end"] for _, fact in reported if fiscal_year_name(fact["end"], rule) == fiscal_year})
    if not ends:
        return None
    if len(ends) > 1:
        raise CantVerify(
            f"Two different years could be called fiscal {fiscal_year} (ending {' and '.join(ends)}), "
            "probably because the company changed its fiscal year. We won't guess which one is meant."
        )

    this_year = [(tag, fact) for tag, fact in reported if fact["end"] == ends[0]]
    tag, fact = _choose(_one_report(this_year, newest=True), spec, tags)
    _, first = _choose(_one_report(this_year, newest=False), spec, tags, check=False)
    originally_reported = None
    if not same_number(first["val"], fact["val"]):
        originally_reported = {"value": first["val"], "filed": first["filed"], "accession_number": first["accn"]}

    return {
        "value": fact["val"],
        "unit": spec["unit"],
        "period_start": fact["start"],
        "period_end": fact["end"],
        "days": (_date(fact["end"]) - _date(fact["start"])).days + 1,
        "naming": rule,
        "form": fact["form"],
        "filed": fact["filed"],
        "accession_number": fact["accn"],
        "tag": tag,
        "originally_reported": originally_reported,
    }


def available_years(facts, metric, financial_company=False):
    """The fiscal years for which we can find this metric, oldest first."""
    spec = METRICS[metric]
    reported = annual_facts(facts, spec["financial_company_tags"] if financial_company else spec["tags"], spec["unit"])
    rule = naming_rule(reported)
    return sorted({fiscal_year_name(fact["end"], rule) for _, fact in reported})


def describe_years(years):
    """[2008, 2009, 2010, 2012] -> "2008-2010, 2012"."""
    runs = []
    for year in years:
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs) or "none"


def lookup(ticker, metric, fiscal_year):
    """Download what we need from the SEC and return a "receipt": the number plus where it came from."""
    if metric not in METRICS:
        raise CantVerify(f"Unknown metric {metric!r}. Available: {', '.join(METRICS)}")
    company = sec.find_company(ticker)
    facts = sec.company_facts(company["cik"])
    if "us-gaap" not in facts.get("facts", {}):
        raise CantVerify(
            f"{company['name']} doesn't report under US accounting rules (US GAAP). Foreign companies "
            "usually file a form 20-F instead of a 10-K, and Receipts doesn't read those yet."
        )
    industry = sec.industry(company["cik"])
    financial = is_financial_company(industry["sic"])
    found = find_annual_value(facts, metric, fiscal_year, financial)
    if found is None:
        message = (
            f"{company['name']} has no {METRICS[metric]['label'].lower()} figure for fiscal {fiscal_year} "
            f"in its annual reports. Years we can find: {describe_years(available_years(facts, metric, financial))}."
        )
        if financial:
            message += (
                f"\nIt is a financial company ({industry['description']}). For banks, insurers and real-estate "
                "trusts we only accept a total revenue figure, because the other tags cover only part of it."
            )
        raise CantVerify(message)
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
