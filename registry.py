"""
Indicator registry — maps what a user picks in the dashboard builder to the
connector call(s) needed to fetch it, for the South Sudan dashboard.

Each indicator also carries a "category" used purely for layout grouping in
render_html(): "narrative" (feed of items), "chart" (time series/breakdown),
or "funding" (money panels).
"""
from datetime import date
from connectors import reliefweb, ifrc_go, hdx, fts, cerf, cbpf

# CERF and FTS only support fetching one year at a time. FTS in particular
# is voluntarily/manually reported and often lags — 2026-only can come back
# empty this early in the year, so we pull 2025 onward for a better chance
# of real data while staying reasonably current.
_YEARS = list(range(2025, date.today().year + 1))

INDICATORS = {
    "humanitarian_updates": {
        "label": "Humanitarian situation reports",
        "source": "ReliefWeb",
        "category": "narrative",
        "fetch": lambda country, iso3: reliefweb.fetch(country=country),
    },
    "emergencies": {
        "label": "Active emergencies",
        "source": "IFRC GO",
        "category": "narrative",
        "skip_date_filter": True,  # represents *current* status, not a dated event feed
        "fetch": lambda country, iso3: ifrc_go.fetch("emergencies", country_iso3=iso3),
    },
    "appeals": {
        "label": "Appeals",
        "source": "IFRC GO",
        "category": "narrative",
        "skip_date_filter": True,  # represents *current* status, not a dated event feed
        "fetch": lambda country, iso3: ifrc_go.fetch("appeals", country_iso3=iso3),
    },
    "field_reports": {
        "label": "Field reports",
        "source": "IFRC GO",
        "category": "narrative",
        "skip_date_filter": True,  # same reasoning as emergencies/appeals — best available, not strictly dated
        "fetch": lambda country, iso3: ifrc_go.fetch("field_reports", country_iso3=iso3),
    },
    "food_security_ipc": {
        "label": "Food security (IPC phase, by state)",
        "source": "HDX",
        "category": "chart",
        "skip_date_filter": True,  # this HDX resource is a "latest" snapshot, not a time series
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-ipc"),
    },
    "rainfall_chirps": {
        "label": "Rainfall anomaly",
        "source": "HDX / CHIRPS",
        "category": "chart",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-chirps"),
    },
    "displacement_dtm": {
        "label": "Displacement",
        "source": "HDX / IOM DTM",
        "category": "chart",
        "skip_date_filter": True,  # DTM rounds are infrequent; the latest round should always show
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-dtm"),
    },
    "food_prices_vam": {
        "label": "Food prices",
        "source": "HDX / WFP VAM",
        "category": "chart",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-vam"),
    },
    "conflict_acled": {
        "label": "Conflict trend (aggregate)",
        "source": "HDX / ACLED",
        "category": "chart",
        "fetch": lambda country, iso3: hdx.fetch(f"{iso3.lower()}-acled"),
    },
    "funding_fts": {
        "label": "Funding flows by cluster",
        "source": "FTS",
        "category": "funding",
        "fetch": lambda country, iso3: fts.fetch(country_iso3=iso3, years=_YEARS, groupby="cluster"),
    },
    "funding_cerf": {
        "label": "CERF allocations",
        "source": "CERF",
        "category": "funding",
        "fetch": lambda country, iso3: cerf.fetch(country_name=country, years=_YEARS),
    },
    "funding_cbpf": {
        "label": "SSHF (Country-Based Pooled Fund) projects",
        "source": "CBPF",
        "category": "funding",
        "skip_date_filter": True,  # latest available allocations predate 2026 — show them rather than nothing
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
