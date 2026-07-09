"""
FTS (Financial Tracking Service) connector
Docs: https://api.hpc.tools/docs/v1/
Public endpoint, no auth required for the public flow data used here.
"""
import requests

BASE_URL = "https://api.hpc.tools/v1/public/fts/flow"


def fetch(country_iso3: str, year: int = None, groupby: str = None):
    """
    Fetch humanitarian funding flows for a country from FTS.

    Args:
        country_iso3: 3-letter ISO country code (e.g. "SSD")
        year: optional year filter (e.g. 2026). If omitted, FTS returns
              flows across all years it holds for this country.
        groupby: optional grouping, e.g. "organization", "cluster", "donor"
                 (see FTS docs for the full list of valid values)

    Returns:
        dict with keys like 'flows' (list of individual flow records) and
        totals — FTS returns a richer structure than a flat list, so this
        is passed through close to raw rather than flattened, since the
        shape differs depending on whether groupby is used.
    """
    params = {"countryISO3": country_iso3}
    if year:
        params["year"] = year
    if groupby:
        params["groupby"] = groupby

    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", {})
