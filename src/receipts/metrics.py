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
        # Coca-Cola reported its revenue only as "sales of goods" until 2017. For most companies
        # that is just one part of revenue, so it is used only when a report has no other
        # revenue tag, and never when the report also lists services separately.
        "last_resort_tags": ["SalesRevenueGoodsNet"],
        "part_tags": ["SalesRevenueServicesNet"],
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
# Fiscal-year naming is voted on by the most recent reports for years ending at the same time of year.
VOTING_REPORTS = 5
VOTES_NEEDED = 0.8
MIN_VOTES = 3
SAME_SEASON_DAYS = 20
# If a lower tag shows a number more than 2% bigger than the one we picked, the picked one probably isn't the total.
DISAGREEMENT_LIMIT = 0.02


class CantVerify(Exception):
    """The data doesn't give one clear answer, so we refuse instead of guessing."""


def is_financial_company(sic):
    """Banks, insurers and real-estate trusts have SEC industry codes (SIC) from 6000 to 6799."""
    return bool(sic) and 6000 <= int(sic) <= 6799


def same_number(a, b):
    """True if a and b are equal, or differ only because one of them was rounded.

    NVIDIA reported fiscal 2014 revenue as $4,130,162 thousand, and later reports as
    $4,130 million. Progressive went from $62,108.5 million to $62,109 million. Those
    are the same numbers, not corrections.
    """
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    for unit in (1_000_000, 100_000, 1_000):
        if a % unit == 0 or b % unit == 0:
            return abs(a - b) <= unit / 2
    return False


def _date(text):
    return date.fromisoformat(text)


def tags_for(metric, financial_company):
    """Which tags to read for this metric, by role."""
    spec = METRICS[metric]
    if financial_company:
        return {"main": spec["financial_company_tags"], "last_resort": [], "parts": [], "pass_through": []}
    return {
        "main": spec["tags"],
        "last_resort": spec["last_resort_tags"],
        "parts": spec["part_tags"],
        "pass_through": spec["includes_pass_through_taxes"],
    }


def annual_facts(facts, tags, unit):
    """Every yearly value the company reported under these tags, as (tag, fact) pairs."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    found = []
    for tag in tags["main"] + tags["last_resort"] + tags["parts"]:
        for fact in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
            if fact.get("form") not in ANNUAL_FORMS or "start" not in fact:
                continue
            length = (_date(fact["end"]) - _date(fact["start"])).days + 1
            if MIN_DAYS <= length <= MAX_DAYS:
                found.append((tag, fact))
    return found


# --- Fiscal-year names -------------------------------------------------------------------

def calendar_year(end):
    """The calendar year a fiscal year belongs to, judged by its end date.

    Some 52/53-week years run a few days into January (Wendy's fiscal 2022 ended on
    2023-01-01), so we step back one week before reading the year.
    """
    return (_date(end) - timedelta(days=7)).year


def _same_season(end_a, end_b):
    """True if two fiscal years end at about the same time of year."""
    gap = abs(_date(end_a).timetuple().tm_yday - _date(end_b).timetuple().tm_yday)
    return min(gap, 365 - gap) <= SAME_SEASON_DAYS


def own_report_votes(reported):
    """How each year's own annual report labels that year: {period end: calendar year - label}.

    The label is the SEC's "fy" field. A vote of 0 means the year is named after the
    calendar year it ends in (Walmart), 1 means the year before (Target).
    """
    votes = {}
    for _, fact in sorted(reported, key=lambda pair: pair[1]["filed"]):
        days_until_filed = (_date(fact["filed"]) - _date(fact["end"])).days
        if fact["end"] in votes or not fact.get("fy") or not 0 < days_until_filed <= OWN_REPORT_MAX_DAYS:
            continue
        vote = calendar_year(fact["end"]) - fact["fy"]
        # For years ending in the first week of January, many companies put the cover-page
        # year in this field (2023 for a year ending 2023-01-01) while calling it fiscal 2022.
        ends_first_week_of_january = _date(fact["end"]).month == 1 and _date(fact["end"]).day <= 7
        if vote == -1 and ends_first_week_of_january:
            vote = 0
        votes[fact["end"]] = vote
    return votes


def naming_rule(votes, end):
    """How the company names the fiscal year ending on `end`, or None if we can't tell.

    Walmart calls the year that ended 2025-01-31 "fiscal 2025", but Target calls the year
    that ended 2025-02-01 "fiscal 2024". The SEC's year labels are often off by one in
    older reports, so the 5 most recent reports for years ending at the same time of year
    vote, and at least 4 of 5 must agree. (Same time of year, because a company that moves
    its year end, like Build-A-Bear in 2018, may change its naming too.)

    For years ending in January to March both styles are common, so we need at least 3
    votes. For other years, naming after the calendar year it ends in is the norm.
    """
    recent = sorted(((e, v) for e, v in votes.items() if _same_season(e, end)), reverse=True)[:VOTING_REPORTS]
    both_styles_common = (_date(end) - timedelta(days=7)).month <= 3
    if len(recent) < MIN_VOTES:
        if both_styles_common or any(vote != 0 for _, vote in recent):
            return None
        return {"subtract": 0, "agreeing": len(recent), "reports": len(recent)}
    subtract, agreeing = Counter(vote for _, vote in recent).most_common(1)[0]
    if subtract not in (-1, 0, 1) or agreeing < VOTES_NEEDED * len(recent):
        return None
    return {"subtract": subtract, "agreeing": agreeing, "reports": len(recent)}


def year_names(reported):
    """Name every reported period: {period end: (fiscal year, rule)}. Unclear ones get (None, None)."""
    votes = own_report_votes(reported)
    names = {}
    for end in {fact["end"] for _, fact in reported}:
        rule = naming_rule(votes, end)
        names[end] = (calendar_year(end) - rule["subtract"], rule) if rule else (None, None)
    return names


# --- Picking the value -------------------------------------------------------------------

def _one_report(this_year, newest):
    """The (tag, fact) pairs that come from the newest (or the oldest) annual report covering this year."""
    pick = max if newest else min
    _, fact = pick(this_year, key=lambda pair: (pair[1]["filed"], pair[1]["accn"]))
    return [(tag, f) for tag, f in this_year if f["accn"] == fact["accn"]]


def _choose(one_report, tags):
    """Pick the value to use from one annual report. Raises CantVerify if the report isn't clear.

    - Tags are tried in order (see METRICS).
    - A zero doesn't count if another tag in the same report has a real number.
      Flowserve uses the "Revenues" tag for an empty cell in a table, which reads as 0.
    - If a lower tag shows a clearly bigger number than the one picked, the picked tag is
      probably only a part of revenue (Procter & Gamble once tagged its US sales as
      "Revenues"), so we refuse instead of guessing.

    Returns (tag, fact, other figures in the same report as [(tag, value)]).
    """
    by_tag = {}
    for tag, fact in one_report:
        by_tag.setdefault(tag, []).append(fact)
    candidates = [tag for tag in tags["main"] if tag in by_tag]
    if not candidates and not any(tag in by_tag for tag in tags["parts"]):
        candidates = [tag for tag in tags["last_resort"] if tag in by_tag]
    nonzero = [tag for tag in candidates if any(fact["val"] != 0 for fact in by_tag[tag])]
    usable = nonzero or candidates
    if not usable:
        raise CantVerify("The report only gives parts of revenue (such as goods and services separately), not a total.")

    chosen = usable[0]
    if len({fact["val"] for fact in by_tag[chosen]}) > 1:
        raise CantVerify(f"The same report gives two different values under the tag {chosen}.")
    fact = by_tag[chosen][0]
    others = []
    for other_tag in usable[1:]:
        other = max(f["val"] for f in by_tag[other_tag])
        if other > fact["val"] * (1 + DISAGREEMENT_LIMIT) and other_tag not in tags["pass_through"]:
            raise CantVerify(
                f"The report gives two different figures for this year: ${fact['val']:,} (tag {chosen}) "
                f"and ${other:,} (tag {other_tag}). We can't tell which one is the total, so we won't guess."
            )
        if not same_number(other, fact["val"]):
            others.append((other_tag, other))
    return chosen, fact, others


def find_annual_value(facts, metric, fiscal_year, financial_company=False):
    """Return the official number for one fiscal year, or None if the company didn't report it.

    The rules (each one has a test in tests/test_metrics.py):
    1. Only annual reports (form 10-K) count, and the period must be about one year long.
    2. "Fiscal year N" means what the company itself calls fiscal year N (see naming_rule).
       If that is unclear, or two different periods would both get the name, we refuse.
    3. We use the newest report that covers the year, because numbers are sometimes
       corrected or re-presented later ("restated"). If the first report said something
       different, we return that too.
    4. Within a report, tags are tried in order, with the checks in _choose.
    Missing data is never treated as zero: we return None instead.
    """
    tags = tags_for(metric, financial_company)
    reported = annual_facts(facts, tags, METRICS[metric]["unit"])
    names = year_names(reported)
    ends = sorted(end for end, (year, _) in names.items() if year == fiscal_year)
    unclear = sorted(end for end, (year, _) in names.items() if year is None and abs(calendar_year(end) - fiscal_year) <= 1)
    if unclear:
        raise CantVerify(
            f"We can't tell how the company named its fiscal year ending {unclear[0]} (its reports disagree "
            f"or there are too few), so we can't be sure which period is fiscal {fiscal_year}."
        )
    if not ends:
        return None
    if len(ends) > 1:
        raise CantVerify(
            f"Two different years could be called fiscal {fiscal_year} (ending {' and '.join(ends)}), "
            "probably because the company changed its fiscal year. We won't guess which one is meant."
        )

    this_year = [(tag, fact) for tag, fact in reported if fact["end"] == ends[0]]
    tag, fact, others = _choose(_one_report(this_year, newest=True), tags)

    # Did the first report covering this year say something different?
    first_report = _one_report(this_year, newest=False)
    originally_reported = None
    if first_report[0][1]["accn"] != fact["accn"] and not any(same_number(f["val"], fact["val"]) for _, f in first_report):
        try:
            _, first, _ = _choose(first_report, tags)
            originally_reported = {"value": first["val"], "filed": first["filed"], "accession_number": first["accn"]}
        except CantVerify:
            pass  # the first report itself isn't clear, so we don't claim anything about it

    return {
        "value": fact["val"],
        "unit": METRICS[metric]["unit"],
        "period_start": fact["start"],
        "period_end": fact["end"],
        "days": (_date(fact["end"]) - _date(fact["start"])).days + 1,
        "naming": names[ends[0]][1],
        "form": fact["form"],
        "filed": fact["filed"],
        "accession_number": fact["accn"],
        "tag": tag,
        "also_reported": [{"tag": t, "value": v} for t, v in others],
        "originally_reported": originally_reported,
    }


# --- Looking things up -------------------------------------------------------------------

def available_years(facts, metric, financial_company=False):
    """The fiscal years we can name clearly for this metric, oldest first."""
    tags = tags_for(metric, financial_company)
    names = year_names(annual_facts(facts, tags, METRICS[metric]["unit"]))
    return sorted({year for year, _ in names.values() if year is not None})


def describe_years(years):
    """[2008, 2009, 2010, 2012] -> "2008-2010, 2012"."""
    runs = []
    for year in years:
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs) or "none"


def _has_annual_report(facts):
    return any(
        fact.get("form") in ANNUAL_FORMS
        for tag in facts.get("facts", {}).get("us-gaap", {}).values()
        for rows in tag.get("units", {}).values()
        for fact in rows
    )


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
    if not _has_annual_report(facts):
        raise CantVerify(
            f"{company['name']} (SEC filer ID {company['cik']}) hasn't filed an annual report yet. If it is a newly "
            "formed holding company, its earlier reports are under the previous company's filer ID."
        )
    industry = sec.industry(company["cik"])
    financial = is_financial_company(industry["sic"])
    found = find_annual_value(facts, metric, fiscal_year, financial)
    if found is None:
        years = available_years(facts, metric, financial)
        message = (
            f"The SEC data for {company['name']} has no {METRICS[metric]['label'].lower()} figure for fiscal "
            f"{fiscal_year}. Years we can find: {describe_years(years)}."
        )
        if years and fiscal_year < years[0]:
            message += "\nOlder years may be under a previous company's filer ID (after a merger or reorganization)."
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
