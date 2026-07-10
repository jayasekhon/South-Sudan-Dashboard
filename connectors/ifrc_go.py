"""
IFRC GO API connector
Docs: https://goadmin.ifrc.org/api-docs/swagger-ui/
      https://go-wiki.ifrc.org/en/go-api/
Public endpoints, no auth required.

Different GO endpoints filter by country differently — this isn't
consistent across the API:
  - project:      accepts country_iso3 directly (string)
  - event:        wants countries__in=<numeric GO country id>
  - appeal:       wants country=<numeric GO country id> (confirmed in docs)
  - field-report: has NO documented country=<id> filter at all — its own
                   data dictionary only documents is_covid_report, regions,
                   and a free-text "search" that explicitly covers the
                   "countries and summary fields". So this one is filtered
                   by searching the country name as text instead.
  - surge_alert:  assumed to follow appeal's pattern (unconfirmed)

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

# (param_name, param_type) per endpoint.
#   "iso3"   -> send the ISO3 code directly
#   "id"     -> resolve to GO's numeric country id first
#   "search" -> send the plain country name as a free-text search
COUNTRY_FILTER = {
    "event": ("countries__in", "id"),
    "appeal": ("country", "id"),
    "project": ("country_iso3", "iso3"),
    "field-report": ("search", "search"),
    "surge_alert": ("country", "id"),
}


# Confirmed-correct GO numeric country IDs. The dynamic /country/?iso3=...
# lookup below turned out to be unreliable in practice — it resolved South
# Sudan to id=14 (wrong; that ID belongs to some other country), which
# caused every result to correctly get rejected by the client-side safety
# check, i.e. genuinely 0 real South Sudan results, not a filtering fluke.
# 290 was confirmed directly from IFRC's own published country CSV export.
KNOWN_COUNTRY_IDS = {
    "SSD": 290,
}


def _resolve_country_id(iso3: str):
    if iso3 in KNOWN_COUNTRY_IDS:
        return KNOWN_COUNTRY_IDS[iso3]
    # Fallback for countries not in the known table — flagged as less
    # reliable per the SSD experience above; verify with a diagnostic print
    # if you extend this to a new country.
    resp = requests.get(f"{BASE_URL}/country/", params={"iso3": iso3}, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise ValueError(f"Could not resolve a GO country ID for ISO3 '{iso3}'")
    resolved = results[0]["id"]
    print(f"  -> IFRC GO: resolved '{iso3}' to id={resolved} via dynamic lookup "
          f"(unverified — add to KNOWN_COUNTRY_IDS once confirmed)")
    return resolved


def _matches_country(item, iso3, country_name=None):
    """Client-side safety net: confirm a result actually belongs to the
    requested country, in case an endpoint's filter didn't behave as
    expected. Items with no embedded country info at all are kept rather
    than dropped, since absence isn't evidence of a mismatch."""
    country = item.get("country")
    if isinstance(country, dict):
        return country.get("iso3") == iso3
    countries = item.get("countries")
    if isinstance(countries, list) and countries:
        if any(isinstance(c, dict) and c.get("iso3") == iso3 for c in countries):
            return True
        if country_name and any(isinstance(c, dict) and c.get("name") == country_name for c in countries):
            return True
        return False
    return True


def fetch(indicator: str, country_iso3: str, country_name: str = None, keyword: str = None, limit: int = 50):
    """
    Fetch a dataset from IFRC GO for a given country.

    Args:
        indicator: one of ENDPOINTS keys (e.g. "emergencies", "appeals")
        country_iso3: 3-letter ISO country code (e.g. "UGA")
        country_name: plain country name (e.g. "Uganda") — needed for
                      endpoints like field-report that filter by text
                      search rather than a country ID
        keyword: optional additional free-text search
        limit: max records to return

    Returns:
        list of raw result dicts from the GO API, filtered to the country
    """
    if indicator not in ENDPOINTS:
        raise ValueError(f"Unknown IFRC GO indicator '{indicator}'. Choose from {list(ENDPOINTS)}")

    endpoint = ENDPOINTS[indicator]
    param_name, param_type = COUNTRY_FILTER[endpoint]

    if param_type == "iso3":
        resolved = country_iso3
    elif param_type == "search":
        if not country_name:
            raise ValueError(f"'{endpoint}' needs country_name for its search-based country filter.")
        resolved = country_name
    else:
        resolved = _resolve_country_id(country_iso3)

    params = {"limit": limit, param_name: resolved}
    if keyword and param_name != "search":
        params["search"] = keyword

    resp = requests.get(f"{BASE_URL}/{endpoint}/", params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    results = body.get("results", [])

    if param_type == "search":
        # The search query IS the filter here — there's no documented
        # country field to double-check against, and our attempt at one
        # was incorrectly rejecting every genuine result (unknown internal
        # structure of the "countries" field for this endpoint).
        matched = results
    else:
        matched = [r for r in results if _matches_country(r, country_iso3, country_name)]

    print(f"  -> IFRC GO [{indicator}]: requested {param_name}={resolved!r}, "
          f"API reports {body.get('count', '?')} total, page returned {len(results)}, "
          f"{len(matched)} {'kept (search-filtered, no extra check)' if param_type == 'search' else 'passed the country safety-check'}")

    return matched
