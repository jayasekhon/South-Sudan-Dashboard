"""
IFRC GO API connector
Docs: https://goadmin.ifrc.org/api-docs/swagger-ui/
      https://go-wiki.ifrc.org/en/go-api/
Public endpoints, no auth required.

Different GO endpoints filter by country differently — this isn't
consistent across the API:
  - project:  accepts country_iso3 directly (string)
  - event:    wants countries__in=<numeric GO country id>
  - appeal, field-report, surge_alert: want country=<numeric GO country id>

fetch() resolves the ISO3 code to GO's internal country ID once (via
/country/?iso3=...) when an endpoint needs it, and applies the right
parameter. As a safety net — since not every endpoint's exact filter
behaviour is confirmed from documentation — results are also checked
client-side against the requested country before being returned.
"""
import requests

BASE_URL = "https://goadmin.ifrc.org/api/v2"

ENDPOINTS = {
    "emergencies": "event",
    "appeals": "appeal",
    "projects": "project",
    "surge_alerts": "surge_alert",
    "field_reports": "field-report",
}

# (param_name, param_type) per endpoint. param_type "iso3" sends the code
# directly; "id" means we need to resolve it to GO's numeric country id first.
COUNTRY_FILTER = {
    "event": ("countries__in", "id"),
    "appeal": ("country", "id"),
    "project": ("country_iso3", "iso3"),
    "field-report": ("country", "id"),
    "surge_alert": ("country", "id"),
}


def _resolve_country_id(iso3: str):
    resp = requests.get(f"{BASE_URL}/country/", params={"iso3": iso3}, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise ValueError(f"Could not resolve a GO country ID for ISO3 '{iso3}'")
    return results[0]["id"]


def _matches_country(item, iso3):
    """Client-side safety net: confirm a result actually belongs to the
    requested country, in case an endpoint's filter didn't behave as
    expected. Items with no embedded country info at all are kept rather
    than dropped, since absence isn't evidence of a mismatch."""
    country = item.get("country")
    if isinstance(country, dict):
        return country.get("iso3") == iso3
    countries = item.get("countries")
    if isinstance(countries, list) and countries:
        return any(isinstance(c, dict) and c.get("iso3") == iso3 for c in countries)
    return True


def fetch(indicator: str, country_iso3: str, keyword: str = None, limit: int = 50):
    """
    Fetch a dataset from IFRC GO for a given country.

    Args:
        indicator: one of ENDPOINTS keys (e.g. "emergencies", "appeals")
        country_iso3: 3-letter ISO country code (e.g. "UGA")
        keyword: optional free-text search
        limit: max records to return

    Returns:
        list of raw result dicts from the GO API, filtered to the country
    """
    if indicator not in ENDPOINTS:
        raise ValueError(f"Unknown IFRC GO indicator '{indicator}'. Choose from {list(ENDPOINTS)}")

    endpoint = ENDPOINTS[indicator]
    param_name, param_type = COUNTRY_FILTER[endpoint]

    resolved_id = country_iso3 if param_type == "iso3" else _resolve_country_id(country_iso3)
    params = {"limit": limit, param_name: resolved_id}
    if keyword:
        params["search"] = keyword

    resp = requests.get(f"{BASE_URL}/{endpoint}/", params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    results = body.get("results", [])
    matched = [r for r in results if _matches_country(r, country_iso3)]

    print(f"  -> IFRC GO [{indicator}]: requested {param_name}={resolved_id!r}, "
          f"API reports {body.get('count', '?')} total, page returned {len(results)}, "
          f"{len(matched)} passed the country safety-check")

    return matched
