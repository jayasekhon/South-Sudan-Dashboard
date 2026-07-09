"""
HDX connector
Docs: https://data.humdata.org/api

Reads HDX_API_TOKEN from .env — needed for higher rate limits and to pull
non-public resources; also good practice to always send it.

HDX resource_ids are specific to each dataset, so — same as the
HDX_Tabular_Data_Setup_Guide pattern — the workflow is:
  1. search_datasets() once, to find the right resource_id for a country/topic
  2. save it into KNOWN_RESOURCES below
  3. fetch()/get_resource() is then instant, no searching needed

Not every HDX resource is "DataStore active" (queryable via datastore_search)
— plenty are just plain files (.xlsx especially). get_resource() tries the
DataStore first and, on a 404, falls back to downloading the file directly
and parsing it with pandas.
"""
import io
import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.humdata.org/api/3/action"
API_TOKEN = os.getenv("HDX_API_TOKEN")

# key format: "<iso3-lowercase>-<indicator>"  ->  HDX resource_id
KNOWN_RESOURCES = {
    "ssd-ipc":    "0b79be00-7c46-4784-934d-6c80fc9d46ae",  # ipc_ssd_level1_long_latest.csv (admin1)
    "ssd-chirps": "3c1cd950-67ad-4e65-b0ef-2a80563e99aa",  # ssd-rainfall-subnat-full.csv
    "ssd-dtm":    "bf8fa617-99c8-498d-a5f6-e4e00176ad20",  # South Sudan IOM DTM data, admin 0-2
    "ssd-vam":    "5a3e103d-f352-41f6-94e9-acaad13c66a5",  # South Sudan - Food Prices (WFP VAM)
    "ssd-acled":  "de70c202-2ad4-4b11-b6de-58b0653017c0",  # political violence events & fatalities by month-year
}


def _headers():
    return {"Authorization": API_TOKEN} if API_TOKEN else {}


def search_datasets(country_query: str, topic_query: str = "", rows: int = 10):
    """
    Search HDX for candidate datasets matching a country + topic.
    Use this to find a resource_id worth saving into KNOWN_RESOURCES.
    """
    q = f"{country_query} {topic_query}".strip()
    resp = requests.get(
        f"{BASE_URL}/package_search",
        params={"q": q, "rows": rows},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json().get("result", {}).get("results", [])

    datasets = []
    for ds in results:
        for res in ds.get("resources", []):
            datasets.append({
                "dataset_title": ds.get("title"),
                "dataset_url": f"https://data.humdata.org/dataset/{ds.get('name')}",
                "resource_name": res.get("name"),
                "resource_id": res.get("id"),
                "format": res.get("format"),
            })
    return datasets


def _get_from_datastore(resource_id: str, page_size: int = 32000):
    """Pull all rows from a DataStore-active resource, paginating past the
    per-call cap rather than silently truncating at page_size."""
    all_records = []
    offset = 0
    while True:
        resp = requests.get(
            f"{BASE_URL}/datastore_search",
            params={"resource_id": resource_id, "limit": page_size, "offset": offset},
            headers=_headers(),
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        records = result.get("records", [])
        all_records.extend(records)

        total = result.get("total", len(all_records))
        offset += len(records)
        if not records or offset >= total:
            break
    return all_records


def _get_from_file(resource_id: str):
    """Fallback for resources that aren't DataStore-active: look up the
    direct file URL and download + parse it (csv or xlsx)."""
    resp = requests.get(
        f"{BASE_URL}/resource_show",
        params={"id": resource_id},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    meta = resp.json().get("result", {})
    url = meta.get("url")
    fmt = (meta.get("format") or "").lower()

    if not url:
        raise ValueError(f"No download URL found for resource {resource_id}")

    file_resp = requests.get(url, headers=_headers(), timeout=120)
    file_resp.raise_for_status()

    if fmt == "csv" or url.lower().endswith(".csv"):
        df = pd.read_csv(io.StringIO(file_resp.text))
    elif fmt in ("xlsx", "xls") or url.lower().endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(io.BytesIO(file_resp.content), sheet_name=None)
        # Many HDX .xlsx resources have a license/readme cover sheet before
        # the real data (e.g. ACLED exports). Pick whichever sheet actually
        # looks like a data table: more than one column, most cells.
        candidates = {name: sdf for name, sdf in sheets.items() if sdf.shape[1] > 1}
        if not candidates:
            candidates = sheets
        best_sheet = max(candidates, key=lambda name: candidates[name].shape[0] * candidates[name].shape[1])
        df = candidates[best_sheet]
    elif fmt == "geojson" or url.lower().endswith(".geojson"):
        import json
        gj = json.loads(file_resp.text)
        df = pd.json_normalize([f.get("properties", {}) for f in gj.get("features", [])])
    else:
        raise ValueError(
            f"Unsupported format '{fmt}' for resource {resource_id} — "
            f"download and inspect manually: {url}"
        )

    return df.to_dict(orient="records")


def get_geojson(resource_id: str):
    """
    Download and extract a GeoJSON file from an HDX resource. Handles both
    plain .geojson resources and .geojson files packaged inside a .zip
    (common for admin boundary datasets, e.g. ssd_admin_boundaries.geojson.zip).
    Returns the parsed GeoJSON dict.
    """
    import json
    import zipfile

    resp = requests.get(
        f"{BASE_URL}/resource_show",
        params={"id": resource_id},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    meta = resp.json().get("result", {})
    url = meta.get("url")
    if not url:
        raise ValueError(f"No download URL found for resource {resource_id}")

    file_resp = requests.get(url, headers=_headers(), timeout=120)
    file_resp.raise_for_status()

    if url.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(file_resp.content)) as zf:
            geojson_names = [n for n in zf.namelist() if n.lower().endswith((".geojson", ".json"))]
            if not geojson_names:
                raise ValueError(f"No .geojson/.json file found inside zip for resource {resource_id}")

            print(f"  -> get_geojson: zip contains {len(geojson_names)} candidate file(s): {geojson_names}")

            # COD-AB style zips typically bundle multiple layers together —
            # admin0/1/2/3 AREA polygons plus a separate boundary LINES file
            # (which has line-topology properties like left_pcod/right_pcod,
            # not area attributes like a state name). We want an AREA file
            # for admin level 1 specifically, not a lines file.
            def _score(name):
                n = name.lower()
                if "bndl" in n or "lin" in n:
                    return -10  # boundary lines — actively avoid
                if "adm1" in n or "admin1" in n or "adm_1" in n:
                    return 10   # exactly what we want
                if "adm0" in n or "admin0" in n:
                    return -1   # country outline, not useful for a state map
                return 0

            geojson_names.sort(key=_score, reverse=True)
            chosen = geojson_names[0]
            print(f"  -> get_geojson: selected '{chosen}' as the best admin1-area match")

            with zf.open(chosen) as f:
                return json.loads(f.read())
    else:
        return json.loads(file_resp.text)


def get_resource(resource_id: str):
    """
    Pull tabular data for a known HDX resource_id. Tries the DataStore API
    first (fast, queryable); falls back to downloading the raw file if the
    resource isn't DataStore-active (common for .xlsx resources).
    """
    try:
        return _get_from_datastore(resource_id)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return _get_from_file(resource_id)
        raise


def fetch(indicator_key: str):
    """Convenience wrapper: look up a KNOWN_RESOURCES key and return its data."""
    if indicator_key not in KNOWN_RESOURCES:
        raise KeyError(
            f"No known resource_id for '{indicator_key}'. "
            f"Run search_datasets() first to find one, then add it to "
            f"KNOWN_RESOURCES in connectors/hdx.py."
        )
    return get_resource(KNOWN_RESOURCES[indicator_key])
