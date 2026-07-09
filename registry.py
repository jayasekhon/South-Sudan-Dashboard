"""
Indicator registry — maps what a user picks in the dashboard builder to the
connector call(s) needed to fetch it, for the South Sudan dashboard.
"""
from connectors import reliefweb, ifrc_go, hdx, fts, cerf, cbpf

INDICATORS = {
    "humanitarian_updates": {
        "label": "Humanitarian situation reports (ReliefWeb)",
        "source": "reliefweb",
        "fetch": lambda country, iso3: reliefweb.fetch(country=country),
    },
    "emergencies": {
        "label": "Active emergencies (IFRC GO)",
        "source": "ifrc_go",
        "fetch": lambda country, iso3: ifrc_go.fetch("emergencies", country_iso3=iso3),
    },
    "appeals": {
        "label": "Appeals (IFRC GO)",
        "source": "ifrc_go",
        "fetch": lambda country, iso3: ifrc_go.fetch("appeals", country_iso3=iso3),
    },
    "field_reports": {
        "label": "Field reports (IFRC GO)",
        "source": "ifrc_go",
        "fetch": lambda country, iso3: ifrc_go.fetch("field_reports", country_iso3=iso3),
    },
    "food_security_ipc": {
        "label": "Food security / IPC (HDX)",
        "source": "hdx",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-ipc"),
    },
    "rainfall_chirps": {
        "label": "Rainfall anomaly / CHIRPS (HDX)",
        "source": "hdx",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-chirps"),
    },
    "displacement_dtm": {
        "label": "Displacement / IOM DTM (HDX)",
        "source": "hdx",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-dtm"),
    },
    "food_prices_vam": {
        "label": "Food prices / WFP VAM (HDX)",
        "source": "hdx",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-vam"),
    },
    "conflict_acled": {
        "label": "Conflict trend, aggregate / ACLED via HDX (charts only)",
        "source": "hdx",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-acled"),
    },
    "funding_fts": {
        "label": "Humanitarian funding flows (FTS)",
        "source": "fts",
        "fetch": lambda country, iso3: fts.fetch(country_iso3=iso3, groupby="cluster"),
    },
    "funding_cerf": {
        "label": "CERF allocations",
        "source": "cerf",
        "fetch": lambda country, iso3: cerf.fetch(country_name=country, year=2026),
    },
    "funding_cbpf": {
        "label": "Country-Based Pooled Fund / SSHF projects (CBPF)",
        "source": "cbpf",
        "fetch": lambda country, iso3: cbpf.fetch(country_name=country),
    },
}


def available_indicators():
    """Returns {key: human-readable label} for every indicator that can be requested."""
    return {key: v["label"] for key, v in INDICATORS.items()}


def fetch_indicator(key, country, iso3):
    """Fetch one indicator's raw data for a given country."""
    if key not in INDICATORS:
        raise KeyError(f"Unknown indicator '{key}'. Choose from {list(INDICATORS)}")
    return INDICATORS[key]["fetch"](country, iso3)
