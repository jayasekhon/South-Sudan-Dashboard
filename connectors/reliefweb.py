"""
ReliefWeb API connector
Docs: https://apidoc.reliefweb.int/
Public API. As of Nov 2025, requires a pre-approved appname (see .env).
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.reliefweb.int/v2/reports"
APPNAME = os.getenv("RELIEFWEB_APPNAME")


def fetch(country: str, keyword: str = None, limit: int = 50):
    """
    Fetch ReliefWeb situation reports / updates for a given country.

    Args:
        country: country name as ReliefWeb expects it (e.g. "South Sudan")
        keyword: optional free-text filter (e.g. "food security")
        limit: max number of reports to return

    Returns:
        list of dicts: title, date, source, url
    """
    if not APPNAME:
        raise EnvironmentError(
            "RELIEFWEB_APPNAME not set. Add it to your .env file "
            "(see README for how to request an approved appname)."
        )

    payload = {
        "appname": APPNAME,
        "limit": limit,
        "filter": {"field": "country", "value": country},
        "sort": ["date:desc"],
        "fields": {"include": ["title", "date.created", "source", "url"]},
    }
    if keyword:
        payload["query"] = {"value": keyword}

    resp = requests.post(BASE_URL, json=payload, params={"appname": APPNAME}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    records = []
    for item in data.get("data", []):
        fields = item.get("fields", {})
        records.append({
            "title": fields.get("title"),
            "date": fields.get("date", {}).get("created"),
            "source": ", ".join(s.get("name", "") for s in fields.get("source", [])),
            "url": fields.get("url"),
        })
    return records
