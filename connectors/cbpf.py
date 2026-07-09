"""
CBPF connector
Docs: https://cbpfapi.unocha.org/vo3/
Public endpoint, no auth required.

Uses the vo2 ExtendedAllocationDetails endpoint rather than vo3's
GlobalGenericDataExtract(PF_PROJ_SUMMARY): the vo3 project-summary dataset
identifies country only via a numeric PooledFundId with no public lookup
table, whereas ExtendedAllocationDetails returns a plain-text Country column
directly, confirmed to include South Sudan allocation records — much simpler
and more robust than trying to guess or resolve fund ID codes.
"""
import csv
import io
import requests

BASE_URL = "https://cbpfapi.unocha.org/vo2/odata/ExtendedAllocationDetails"


def fetch(country_name: str):
    """
    Fetch CBPF allocation records for a given country.

    Args:
        country_name: e.g. "South Sudan" — matched case-insensitively as a
                      substring against the API's Country column

    Returns:
        list of dicts — one per allocation record (date, type, theme,
        target amount, location, etc.)
    """
    resp = requests.get(BASE_URL, params={"PoolfundCodeAbbrv": "", "$format": "csv"}, timeout=90)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))

    if not rows:
        raise ValueError(f"CBPF ExtendedAllocationDetails returned 0 rows. Test manually: {resp.url}")

    country_col = next((c for c in rows[0].keys() if c.lower() == "country"), None)
    if not country_col:
        country_col = next((c for c in rows[0].keys() if "country" in c.lower()), None)
    if not country_col:
        raise ValueError(
            f"Got {len(rows)} rows but no Country column found. "
            f"Columns were: {list(rows[0].keys())}. Inspect: {resp.url}"
        )

    matches = [r for r in rows if country_name.lower() in str(r.get(country_col, "")).lower()]
    if not matches:
        raise ValueError(
            f"No rows matched country '{country_name}' in column '{country_col}' "
            f"out of {len(rows)} total rows. Inspect: {resp.url}"
        )
    return matches
