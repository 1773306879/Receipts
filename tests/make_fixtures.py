"""Make the small test files in tests/fixtures/ from real SEC downloads.

The tests must run without internet, so they use saved copies of real SEC data,
cut down to only the parts the tests need. To refresh them:

    uv run receipts lookup AAPL revenue 2024   # (and the other companies) to fill cache/
    uv run python tests/make_fixtures.py
"""

import json
from pathlib import Path

from receipts.metrics import METRICS
from receipts.sec import CACHE_DIR, find_company

COMPANIES = ["AAPL", "WMT", "HD", "WEN", "DLTR", "GOOGL", "MSFT", "NVDA", "TJX", "CAKE", "FLS", "GPN", "VTSI", "MTB"]
KEEP_FIELDS = ["start", "end", "val", "accn", "fy", "fp", "form", "filed"]
FIXTURES = Path(__file__).parent / "fixtures"

tags = {tag for metric in METRICS.values() for tag in metric["tags"] + metric["financial_company_tags"]}
FIXTURES.mkdir(exist_ok=True)
for ticker in COMPANIES:
    cik = find_company(ticker)["cik"]
    facts = json.loads((CACHE_DIR / f"companyfacts_{cik}.json").read_text())
    us_gaap = {
        tag: {"units": {unit: [{k: fact[k] for k in KEEP_FIELDS if k in fact} for fact in rows]
                        for unit, rows in data["units"].items()}}
        for tag, data in facts["facts"]["us-gaap"].items()
        if tag in tags
    }
    trimmed = {"cik": facts["cik"], "entityName": facts["entityName"], "facts": {"us-gaap": us_gaap}}
    (FIXTURES / f"{ticker}.json").write_text(json.dumps(trimmed, indent=0))
    print(f"{ticker}: {sum(len(r) for d in us_gaap.values() for r in d['units'].values())} facts")
