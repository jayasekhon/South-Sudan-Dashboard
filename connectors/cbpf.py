"""
CBPF connector
Docs: https://cbpfapi.unocha.org/vo3/

Uses vo3's GlobalGenericDataExtract(PF_PROJ_SUMMARY) — the same endpoint
documented in the CBPF_Setup_Guide Power Automate flow — rather than vo2's
ExtendedAllocationDetails, which turned out to be a fixed COVID-19-era
historical snapshot (confirmed via its HDX listing title: "CERF and CBPF
COVID-19 Allocations") that will never have current data.

PF_PROJ_SUMMARY has no direct Country column, only a numeric PooledFundId
with no public lookup table — but project codes (ChfProjectCode) are
conventionally prefixed by country (e.g. "SS-" for South Sudan), so we
filter on that instead. Diagnostics are printed either way, since this
prefix convention isn't confirmed from documentation.
"""
import csv
import io
import requests

BASE_URL = "https://cbpfapi.unocha.org/vo3/odata/GlobalGenericDataExtract"

# Guessed project-code prefixes per country — confirm/adjust from the
# diagnostic output on first run.
COUNTRY_CODE_PREFIX = {
    "south sudan": "SS-",
}


def fetch(country_name: str, allocation_years: str = "2025_2026", sp_code: str = "PF_PROJ_SUMMARY"):
    """
    Fetch current CBPF project summaries for a given country.

    Args:
        country_name: e.g. "South Sudan"
        allocation_years: e.g. "2025_2026" or a single year "2026"
        sp_code: which CBPF dataset to pull — PF_PROJ_SUMMARY is the
                 general, currently-active project summary

    Returns:
        list of dicts — one per project
    """
    prefix = COUNTRY_CODE_PREFIX.get(country_name.lower())
    if not prefix:
        raise KeyError(
            f"No known project-code prefix for '{country_name}'. "
            f"Add it to COUNTRY_CODE_PREFIX in connectors/cbpf.py."
        )

    params = {
        "SPCode": sp_code,
        "PoolfundCodeAbbrv": "",       # blank = all pooled funds
        "ShowAllPooledFunds": "",      # blank/0 = only currently active funds
        "AllocationYears": allocation_years,
        "FundTypeId": 1,               # 1 = CBPF, 2 = CERF
        "$format": "csv",
    }
    resp = requests.get(BASE_URL, params=params, timeout=90)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))

    if not rows:
        raise ValueError(f"CBPF vo3 API returned 0 rows for {allocation_years}. Test manually: {resp.url}")

    code_col = next((c for c in rows[0].keys() if "projectcode" in c.lower() or "chfproject" in c.lower()), None)
    if not code_col:
        raise ValueError(
            f"No project-code column found to filter by country. "
            f"Columns were: {list(rows[0].keys())}. Inspect: {resp.url}"
        )

    sample_codes = [r.get(code_col) for r in rows[:5]]
    matches = [r for r in rows if str(r.get(code_col, "")).upper().startswith(prefix)]
    print(f"  -> CBPF: {len(rows)} total rows for {allocation_years}, code_col='{code_col}', "
          f"sample codes: {sample_codes}")
    print(f"  -> CBPF: {len(matches)} matched prefix '{prefix}'")

    if not matches:
        raise ValueError(
            f"No rows matched prefix '{prefix}' in column '{code_col}' out of {len(rows)} rows. "
            f"Sample values: {sample_codes} — adjust COUNTRY_CODE_PREFIX. Inspect: {resp.url}"
        )
    return matches
