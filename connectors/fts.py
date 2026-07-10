"""
FTS (Financial Tracking Service) connector
Docs: https://api.hpc.tools/docs/v1/
Public endpoint, no auth required for the public flow data used here.
"""
from datetime import date
import requests

BASE_URL = "https://api.hpc.tools/v1/public/fts/flow"


def _fetch_year(country_iso3: str, year: int, groupby: str = None):
    params = {"countryISO3": country_iso3}
    if year:
        params["year"] = year
    if groupby:
        params["groupby"] = groupby

    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", {})
    flows = data.get("flows", []) if isinstance(data, dict) else []

    incoming = data.get("incoming", {}) if isinstance(data, dict) else {}
    print(f"  -> FTS {year}: status={body.get('status')}, "
          f"incoming.flowCount={incoming.get('flowCount')}, "
          f"incoming.fundingTotal={incoming.get('fundingTotal')}, "
          f"flows array length={len(flows)}")

    return data


def fetch(country_iso3: str, years=None, groupby: str = None):
    """
    Fetch humanitarian funding flows for a country from FTS, across one or
    more years.

    Args:
        country_iso3: 3-letter ISO country code (e.g. "SSD")
        years: iterable of years, e.g. [2026] or range(2026, 2028).
               Defaults to just the current year if omitted.
        groupby: optional grouping, e.g. "organization", "cluster", "donor"

    Returns:
        dict {"flows": [...]} — combined flow records across all requested
        years (per-year totals aren't preserved when combining years; use
        the individual flow records for anything that needs a breakdown)
    """
    if years is None:
        years = [date.today().year]

    combined_flows = []
    for y in years:
        data = _fetch_year(country_iso3, y, groupby)
        flows = data.get("flows", []) if isinstance(data, dict) else []
        combined_flows.extend(flows)

    return {"flows": combined_flows}
