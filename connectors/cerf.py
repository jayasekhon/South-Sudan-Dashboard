"""
CERF connector
Docs: https://cerfgms-webapi.unocha.org/
Public endpoint, no auth required.

Note: CERF's API doesn't offer a dedicated "projects for country X" endpoint
— only project/year/{year}. So this pulls a year's worth of global projects
and filters client-side for the country. Fine for a single-country dashboard;
would need caching if used across many countries at once.
"""
from datetime import date
import requests

BASE_URL = "https://cerfgms-webapi.unocha.org/v1"


def _fetch_year(year: int, country_name: str):
    url = f"{BASE_URL}/project/year/{year}.json"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    all_projects = resp.json()

    if isinstance(all_projects, dict):
        all_projects = all_projects.get("value") or all_projects.get("data") or []

    country_lower = country_name.lower()
    return [
        p for p in all_projects
        if country_lower in str(p.get("countryName", p.get("country", ""))).lower()
    ]


def fetch(country_name: str, years=None):
    """
    Fetch CERF-funded projects for a country across one or more years.

    Args:
        country_name: e.g. "South Sudan" — matched case-insensitively as a
                      substring, since CERF doesn't expose a country filter
                      directly on this endpoint
        years: iterable of years to pull, e.g. [2026] or range(2026, 2028).
               Defaults to just the current year if omitted.

    Returns:
        list of project dicts for that country across all given years
    """
    if years is None:
        years = [date.today().year]

    matches = []
    for y in years:
        matches.extend(_fetch_year(y, country_name))
    return matches
