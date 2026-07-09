"""
CERF connector
Docs: https://cerfgms-webapi.unocha.org/
Public endpoint, no auth required.

Note: CERF's API doesn't offer a dedicated "projects for country X" endpoint
— only project/year/{year}. So this pulls a year's worth of global projects
and filters client-side for the country. Fine for a single-country dashboard;
would need caching if used across many countries at once.
"""
import requests

BASE_URL = "https://cerfgms-webapi.unocha.org/v1"


def fetch(country_name: str, year: int):
    """
    Fetch CERF-funded projects for a given country and year.

    Args:
        country_name: country name as it appears in CERF records
                      (e.g. "South Sudan") — matched case-insensitively
                      as a substring, since CERF doesn't expose a country
                      filter directly on this endpoint
        year: allocation year (e.g. 2026)

    Returns:
        list of project dicts for that country/year
    """
    url = f"{BASE_URL}/project/year/{year}.json"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    all_projects = resp.json()

    if isinstance(all_projects, dict):
        # some CERF endpoints wrap results in a container key
        all_projects = all_projects.get("value") or all_projects.get("data") or []

    country_lower = country_name.lower()
    matches = [
        p for p in all_projects
        if country_lower in str(p.get("countryName", p.get("country", ""))).lower()
    ]
    return matches
