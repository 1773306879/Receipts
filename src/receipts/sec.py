"""Downloading data from SEC EDGAR, the US government's free database of company filings.

Every download is saved on disk (the "cache"), so we don't ask the SEC for the
same file again until it is a day old.
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}"

# The project folder (two levels up from this file), so the cache and .env are found
# no matter which folder you run the command from.
PROJECT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_DIR / "cache"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
# The SEC allows 10 requests per second. We stay well below that.
SECONDS_BETWEEN_REQUESTS = 0.2

_last_request_at = 0.0


class SecError(Exception):
    """A problem talking to the SEC, explained in plain English."""


def _user_agent():
    load_dotenv(PROJECT_DIR / ".env")
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in user_agent:
        raise SecError(
            "The SEC requires every request to say who is asking.\n"
            "Copy .env.example to .env and set SEC_USER_AGENT to your name and email."
        )
    return user_agent


def _download_json(url):
    global _last_request_at
    wait = SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    try:
        response = requests.get(url, headers={"User-Agent": _user_agent()}, timeout=30)
    except requests.RequestException as error:
        raise SecError(
            f"Could not reach {url}.\n"
            "Check your internet connection, or that sec.gov is allowed by your network settings.\n"
            f"Details: {error}"
        ) from error
    finally:
        _last_request_at = time.monotonic()
    if response.status_code == 404:
        raise SecError(f"The SEC has no data at {url}")
    if response.status_code != 200:
        raise SecError(f"The SEC answered with error {response.status_code} for {url}")
    try:
        return response.json()
    except ValueError as error:
        raise SecError(f"{url} sent back something that isn't data (maybe a network login page).") from error


def _cached_json(url, cache_name):
    path = CACHE_DIR / cache_name
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass  # a damaged cache file: download it again
    data = _download_json(url)
    CACHE_DIR.mkdir(exist_ok=True)
    # Write to a temporary file first, so an interrupted download never leaves a broken cache file.
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)
    return data


def find_company(ticker):
    """Turn a stock ticker like "AAPL" into the SEC's 10-digit company ID (the "CIK")."""
    wanted = ticker.strip().upper().replace(".", "-")  # the SEC writes BRK.B as BRK-B
    for row in _cached_json(TICKERS_URL, "company_tickers.json").values():
        if row["ticker"].upper() == wanted:
            return {"ticker": row["ticker"], "name": row["title"], "cik": f"{row['cik_str']:010d}"}
    raise SecError(f"The SEC's list of companies has no ticker {ticker!r}.")


def company_facts(cik):
    """Every number the company has reported in its filings, as one big JSON document."""
    return _cached_json(COMPANY_FACTS_URL.format(cik=cik), f"companyfacts_{cik}.json")


def submissions(cik):
    """The company's profile (industry, fiscal year end) and its list of recent filings."""
    return _cached_json(SUBMISSIONS_URL.format(cik=cik), f"submissions_{cik}.json")


def industry(cik):
    """The company's industry code (SIC) and its description, e.g. 3571 "Electronic Computers"."""
    profile = submissions(cik)
    return {"sic": profile.get("sic"), "description": profile.get("sicDescription") or "unknown industry"}


def filing_link(cik, accession_number):
    """A link to the filing itself: the main document if we can find it, otherwise the filing's index page."""
    folder = ARCHIVES_URL.format(cik=int(cik), folder=accession_number.replace("-", ""))
    recent = submissions(cik)["filings"]["recent"]
    for number, document in zip(recent["accessionNumber"], recent["primaryDocument"]):
        if number == accession_number and document:
            return f"{folder}/{document}"
    return f"{folder}/{accession_number}-index.htm"
