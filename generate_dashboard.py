"""
generate_dashboard.py

Pick a country + a set of indicators, fetch live data from each source, and
render a single self-contained HTML dashboard — styled as a field situation
monitor (KPI strip, narrative feed, charts, funding panel), rather than raw
data tables.

USAGE (interactive):
    python generate_dashboard.py

USAGE (non-interactive, for scheduling):
    python generate_dashboard.py --country "South Sudan" --iso3 SSD \
        --indicators humanitarian_updates,emergencies,food_security_ipc

Output:
    ./output/<country>_<date>/dashboard.html
    ./output/<country>_<date>/<indicator>.csv   (one per indicator, raw data)
"""
import argparse
import json
import os
import re
from datetime import date

import pandas as pd

from registry import available_indicators, fetch_indicator, INDICATORS

OUTPUT_ROOT = os.path.join(os.path.dirname(__file__), "output")


def _safe_json(obj):
    """json.dumps for embedding inside a <script> tag. If any string value
    in the data happens to contain the literal characters '</script', the
    HTML parser closes our script tag early — before the browser's JS
    engine ever sees it — silently breaking every chart and the map with
    no error message at all. Real report titles/org names/descriptions can
    contain almost anything, so this isn't hypothetical. Escaping '</' as
    '<\\/' is the standard fix and is a no-op for normal JSON content."""
    return json.dumps(obj).replace("</", "<\\/")

DATE_COL_HINTS = ["date", "month", "year", "period", "reportingdate", "created"]
NUMERIC_HINTS = ["amount", "value", "usd", "total", "count", "fatal", "displaced",
                  "population", "price", "anomaly", "phase", "rainfall"]

# Only show data from this date forward. The end of the window is always
# "today" (computed at run time below), so the dashboard stays current
# automatically without needing this file edited again.
DATA_START_DATE = date(2026, 1, 1)


def _year_range(start_year=DATA_START_DATE.year):
    """[2026, 2027, ...] up to and including the current year — used by
    connectors (CERF, FTS) that only support fetching one year at a time,
    so we don't waste calls pulling years we're going to discard anyway."""
    return list(range(start_year, date.today().year + 1))


def _find_key(sample_row: dict, hints):
    for col in sample_row.keys():
        norm = col.lower().replace("_", "").replace(" ", "")
        if any(h in norm for h in hints):
            return col
    return None


def _find_date_column(sample_row: dict):
    return _find_key(sample_row, DATE_COL_HINTS)


def _parse_date_value(raw, row=None, date_col=None):
    """
    Parse a single date-like value, handling two real-world gotchas:
    1. Bare years (e.g. 2026, or "2026") — pandas' pd.to_datetime(2026)
       treats a bare int as nanoseconds since 1970-01-01, NOT the year
       2026. Any 4-digit value in a plausible year range is treated as
       January 1 of that year instead.
    2. Separate Month + Year columns (common in "events by month-year"
       exports, e.g. ACLED-derived datasets) — if the date_col found is a
       month name/number with no year in it, and a sibling "Year" column
       exists on the same row, they're combined.
    """
    if row is not None and date_col is not None:
        norm_col = date_col.lower()
        if "month" in norm_col and "year" not in norm_col:
            year_col = next((c for c in row.keys() if "year" in c.lower()), None)
            if year_col and row.get(year_col) not in (None, ""):
                combined = f"{raw} {row[year_col]}"
                parsed = pd.to_datetime(combined, errors="coerce")
                if pd.notna(parsed):
                    return parsed

    # Bare year detection (int or numeric string, plausible year range)
    try:
        as_int = int(float(raw))
        if 1990 <= as_int <= 2100 and str(raw).strip() in (str(as_int), f"{as_int}.0"):
            return pd.Timestamp(year=as_int, month=1, day=1)
    except (TypeError, ValueError):
        pass

    return pd.to_datetime(raw, errors="coerce")


def filter_recent(data, start=DATA_START_DATE, end=None):
    """
    Best-effort recency filter applied uniformly across every source.
    - dict payloads (e.g. FTS's {"flows": [...]}) are filtered on their
      inner list and returned in the same shape.
    - list-of-dict payloads are filtered directly.
    - if no column looks like a date, the data is left untouched — some
      sources (e.g. HDX "latest" snapshots, IFRC GO's currently-active
      lists) are inherently current and have nothing to filter on.
    - rows with an unparseable or missing date are dropped, since we can't
      confirm they're in range.
    """
    if end is None:
        end = date.today()

    if isinstance(data, dict):
        filtered = dict(data)
        if isinstance(filtered.get("flows"), list):
            filtered["flows"] = filter_recent(filtered["flows"], start, end)
        return filtered

    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return data

    date_col = _find_date_column(data[0])
    if not date_col:
        return data  # nothing to filter on — treat as current/snapshot data

    kept = []
    unparseable_samples = []
    for row in data:
        raw = row.get(date_col)
        if raw in (None, ""):
            continue
        parsed = _parse_date_value(raw, row=row, date_col=date_col)
        if pd.isna(parsed):
            if len(unparseable_samples) < 3:
                unparseable_samples.append(raw)
            continue
        if start <= parsed.date() <= end:
            kept.append(row)

    if not kept and data:
        print(f"  -> filter_recent: date_col='{date_col}' found but 0/{len(data)} rows survived "
              f"the {start}\u2013{end} window. Sample raw values that failed to parse or were "
              f"out of range: {unparseable_samples or [r.get(date_col) for r in data[:3]]}")
    return kept


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def prompt_for_selection():
    country = input("Country name (e.g. South Sudan): ").strip()
    iso3 = input("ISO3 code (e.g. SSD): ").strip().upper()

    print("\nAvailable indicators:")
    options = list(available_indicators().items())
    for i, (key, label) in enumerate(options, start=1):
        print(f"  {i}. {label}  [{key}]")

    raw = input("\nPick indicators by number, comma-separated (e.g. 1,2,4), or 'all': ").strip()
    if raw.lower() == "all":
        return country, iso3, [k for k, _ in options]

    chosen_idx = [int(x) - 1 for x in raw.split(",") if x.strip().isdigit()]
    chosen_keys = [options[i][0] for i in chosen_idx if 0 <= i < len(options)]
    return country, iso3, chosen_keys


def fetch_all(country, iso3, indicator_keys):
    """Fetch every requested indicator, then apply the recency filter.
    Failures don't stop the run — they're recorded so the dashboard can
    show what did and didn't come through."""
    results = {}
    for key in indicator_keys:
        meta = INDICATORS.get(key, {})
        label = meta.get("label", key)
        print(f"Fetching: {label} ...")
        try:
            data = fetch_indicator(key, country, iso3)
            raw_count = len(data) if hasattr(data, "__len__") else None
            if not meta.get("skip_date_filter"):
                data = filter_recent(data)
            results[key] = {**meta, "status": "ok", "data": data}
            n = len(data) if hasattr(data, "__len__") else "?"
            if raw_count is not None and raw_count != n:
                print(f"  -> {n} records (filtered from {raw_count}, {DATA_START_DATE}–today)")
            else:
                print(f"  -> {n} records")
        except Exception as e:
            results[key] = {**meta, "status": "error", "error": str(e), "data": []}
            print(f"  -> FAILED: {e}")
    return results


def save_csvs(results, out_dir):
    for key, res in results.items():
        data = res["data"]
        if isinstance(data, dict):
            data = data.get("flows") or data.get("data") or []
        if data:
            try:
                pd.DataFrame(data).to_csv(os.path.join(out_dir, f"{key}.csv"), index=False)
            except Exception:
                pass  # non-tabular payloads (e.g. nested FTS json) just skip CSV export


# ---------------------------------------------------------------------------
# Light "figure out what this data looks like" helpers — no source-specific
# knowledge required, since column names vary a lot across HDX resources.
# ---------------------------------------------------------------------------

def _to_dataframe(data):
    if isinstance(data, dict):
        data = data.get("flows") or data.get("data") or []
    if not data:
        return pd.DataFrame()
    try:
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()


def _find_column(df, hints):
    for col in df.columns:
        if any(h in col.lower().replace("_", "").replace(" ", "") for h in hints):
            return col
    return None


def detect_chart(df, max_points=24):
    """
    Best-effort: find a date-like column + a numeric column and return data
    for a line chart; otherwise find a categorical + numeric pair for a bar
    chart. Returns None if nothing chartable is found (caller falls back to
    a table).
    """
    if df.empty:
        return None

    numeric_cols = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.6]
    date_col = _find_column(df, DATE_COL_HINTS)
    # A column can't serve as both the date axis and the plotted value —
    # this does happen in practice (e.g. a bare "year" column matches both
    # the date hints and the numeric-column test).
    value_candidates = [c for c in numeric_cols if c != date_col]

    if date_col and value_candidates:
        value_col = value_candidates[0]
        sub = df[[date_col, value_col]].copy()
        sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
        sub[date_col] = sub[date_col].apply(lambda v: _parse_date_value(v))
        sub = sub.dropna().sort_values(date_col).tail(max_points)
        if not sub.empty:
            # Clean date labels — no time-of-day component, which is
            # meaningless noise for monthly/dekadal/annual data.
            labels = sub[date_col].dt.strftime("%Y-%m-%d").tolist()
            return {
                "type": "line",
                "labels": labels,
                "values": sub[value_col].round(2).tolist(),
                "value_label": value_col,
            }

    if numeric_cols:
        value_col = numeric_cols[0]
        cat_col = next((c for c in df.columns if c != value_col and df[c].nunique() < 30), None)
        if cat_col:
            sub = df[[cat_col, value_col]].copy()
            sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
            grouped = sub.groupby(cat_col)[value_col].sum().sort_values(ascending=False).head(10)
            if not grouped.empty:
                return {
                    "type": "bar",
                    "labels": grouped.index.astype(str).tolist(),
                    "values": grouped.round(2).tolist(),
                    "value_label": value_col,
                }
    return None


def compute_kpis(results):
    """Pull a handful of headline numbers for the KPI strip. Best-effort —
    silently skips any indicator whose shape doesn't match what's expected,
    since exact column names vary per HDX resource."""
    kpis = []

    updates = results.get("humanitarian_updates")
    if updates and updates["status"] == "ok":
        kpis.append({"label": "Situation reports", "value": len(updates["data"]), "tone": "neutral"})

    ipc = results.get("food_security_ipc")
    if ipc and ipc["status"] == "ok" and ipc["data"]:
        df = _to_dataframe(ipc["data"])
        phase_col = _find_column(df, ["phase"])
        if phase_col is not None:
            try:
                max_phase = pd.to_numeric(df[phase_col], errors="coerce").max()
                if pd.notna(max_phase):
                    tone = "alert" if max_phase >= 4 else ("warn" if max_phase >= 3 else "ok")
                    kpis.append({"label": "Highest IPC phase", "value": int(max_phase), "tone": tone})
            except Exception:
                pass

    dtm = results.get("displacement_dtm")
    if dtm and dtm["status"] == "ok" and dtm["data"]:
        trend = extract_grouped_trend(dtm["data"], "adminLevel", 0, "reportingDate", "numPresentIdpInd", agg="sum")
        if trend is not None and not trend.empty:
            latest = int(trend.iloc[-1])
            kpis.append({"label": "Displaced (latest)", "value": f"{latest:,}", "tone": "warn"})

    cbpf = results.get("funding_cbpf")
    if cbpf and cbpf["status"] == "ok" and cbpf["data"]:
        df = _to_dataframe(cbpf["data"])
        num_col = _find_column(df, ["budget", "amount", "allocation"])
        if num_col is not None:
            try:
                total = pd.to_numeric(df[num_col], errors="coerce").sum()
                if total:
                    kpis.append({"label": "SSHF allocated (USD)", "value": f"{int(total):,}", "tone": "ok"})
            except Exception:
                pass

    emergencies = results.get("emergencies")
    if emergencies and emergencies["status"] == "ok":
        kpis.append({"label": "Active emergencies", "value": len(emergencies["data"]), "tone": "neutral"})

    return kpis


# ---------------------------------------------------------------------------
# Map — South Sudan admin1 choropleth (currently colored by IPC phase)
# ---------------------------------------------------------------------------

BOUNDARY_RESOURCE_ID = "487db73a-fe01-41a3-a748-c83e639f34ac"  # ssd_admin_boundaries.geojson.zip
ADMIN_COL_HINTS = ["admin1", "state", "region", "adm1", "level1", "level 1", "area", "province"]
ADMIN_NAME_PROP_HINTS = ["admin1name", "adm1en", "adm1name", "statename"]
IPC_COLORS = {1: "#3A7D44", 2: "#C9A227", 3: "#D9822B", 4: "#B23A2E", 5: "#6B1414"}


def fetch_boundaries():
    """Load South Sudan admin1 boundaries for the map. Returns None (map
    section is skipped gracefully) if this fails — it's supplementary,
    not core data, so a failure here shouldn't break the rest of the run."""
    try:
        from connectors import hdx
        return hdx.get_geojson(BOUNDARY_RESOURCE_ID)
    except Exception as e:
        print(f"  -> Could not load map boundaries: {e}")
        return None


def normalize_admin_name(name):
    """Normalize a state/admin name for matching across sources that may
    format it differently — e.g. IPC data saying "Jonglei" while boundary
    files say "Jonglei State". Lowercases, trims, collapses whitespace, and
    strips common trailing admin-unit words."""
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+(state|county|province|region|governorate)$", "", s)
    return s.strip()


def extract_ipc_phase_by_state(data):
    """
    Correctly extract {normalized_state_name: highest_ipc_phase} from the
    real IPC HDX resource shape. This data has one row per (state, validity
    period, phase category) — including non-numeric aggregate rows like
    "all" and "3+" — so naive numeric coercion or unfiltered max() gets
    confused. This:
      - keeps only pure numeric phase rows (1-5), dropping "all"/"3+"
      - keeps only the nearer-term "first projection" period, to avoid
        mixing two different time windows into one figure
      - drops phase rows with zero population (not meaningfully "present")
      - takes the highest phase per state that still has population in it
    """
    df = _to_dataframe(data)
    if df.empty:
        return {}

    admin_col = _find_key(data[0], ADMIN_COL_HINTS)
    if not admin_col or "Phase" not in df.columns:
        return {}

    df = df[df["Phase"].isin(["1", "2", "3", "4", "5"])]
    if "Validity period" in df.columns and (df["Validity period"] == "first projection").any():
        df = df[df["Validity period"] == "first projection"]
    if "Number" in df.columns:
        df = df[pd.to_numeric(df["Number"], errors="coerce").fillna(0) > 0]
    if df.empty:
        return {}

    df["Phase"] = df["Phase"].astype(int)
    grouped = df.groupby(admin_col)["Phase"].max()
    return {normalize_admin_name(k): v for k, v in grouped.items()}


def extract_grouped_trend(data, level_col, level_value, date_col, value_col, agg="mean"):
    """
    Shared logic for CHIRPS/DTM: these HDX resources have many rows per
    date (one per admin unit, or per breakdown category), so a genuine
    time trend needs filtering to one admin level and aggregating by date
    — not just sorting raw rows and taking the last N. Returns a sorted
    pandas Series indexed by date, or None if the shape doesn't match.
    """
    df = _to_dataframe(data)
    if df.empty or level_col not in df.columns or date_col not in df.columns or value_col not in df.columns:
        return None

    sub = df[df[level_col] == level_value].copy()
    if sub.empty:
        return None
    sub[date_col] = pd.to_datetime(sub[date_col], errors="coerce")
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
    sub = sub.dropna(subset=[date_col, value_col])
    if sub.empty:
        return None

    grouped = sub.groupby(date_col)[value_col]
    series = (grouped.mean() if agg == "mean" else grouped.sum()).sort_index()
    return series


def extract_acled_trend(data, value_col="Fatalities"):
    """
    ACLED-derived conflict data has one row per (Month, Year, county) —
    separate Month/Year text+int columns, not a combined date, and many
    rows per period (one per admin2 county). Combine Month+Year into a
    real date and sum across counties for a genuine national trend.
    """
    df = _to_dataframe(data)
    if df.empty or "Month" not in df.columns or "Year" not in df.columns or value_col not in df.columns:
        return None

    df = df.copy()
    df["_date"] = pd.to_datetime(
        df["Month"].astype(str) + " " + df["Year"].astype(str), errors="coerce", format="mixed"
    )
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=["_date", value_col])
    if df.empty:
        return None

    return df.groupby("_date")[value_col].sum().sort_index()


def extract_vam_price_trend(data, commodity_filter="Maize"):
    """
    VAM food price data has many rows per date (one per market x
    commodity), so a genuine national trend needs filtering to one staple
    commodity and averaging across markets per date.
    """
    df = _to_dataframe(data)
    if df.empty or "commodity" not in df.columns or "date" not in df.columns:
        return None

    price_col = "usdprice" if "usdprice" in df.columns else ("price" if "price" in df.columns else None)
    if not price_col:
        return None

    sub = df[df["commodity"].astype(str).str.contains(commodity_filter, case=False, na=False)].copy()
    if sub.empty:
        return None
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub[price_col] = pd.to_numeric(sub[price_col], errors="coerce")
    sub = sub.dropna(subset=["date", price_col])
    if sub.empty:
        return None

    return sub.groupby("date")[price_col].mean().sort_index()


def series_to_chart(series, value_label, max_points=24):
    """Convert a pandas Series (date-indexed) into the chart dict shape."""
    if series is None or series.empty:
        return None
    tail = series.tail(max_points)
    return {
        "type": "line",
        "labels": [d.strftime("%Y-%m-%d") for d in tail.index],
        "values": tail.round(2).tolist(),
        "value_label": value_label,
    }


def build_state_values(data, value_hints, agg="max"):
    """Build {normalized_state_name: value} from a list-of-dict dataset,
    auto-detecting the admin/state column and a numeric value column
    matching value_hints. Returns {} if nothing usable is found, in which
    case the map falls back to showing boundaries with no shading."""
    if not data or not isinstance(data, list) or not isinstance(data[0], dict):
        return {}

    admin_col = _find_key(data[0], ADMIN_COL_HINTS)
    value_col = _find_key(data[0], value_hints)
    if not admin_col or not value_col:
        print(f"  -> build_state_values: couldn't find admin_col (got {admin_col!r}) "
              f"or value_col matching {value_hints} (got {value_col!r}). "
              f"Available columns: {list(data[0].keys())}")
        return {}

    result = {}
    for row in data:
        name, raw_val = row.get(admin_col), row.get(value_col)
        if not name or raw_val in (None, ""):
            continue
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            continue
        key = normalize_admin_name(name)
        result[key] = max(result.get(key, val), val) if agg == "max" else result.get(key, 0) + val
    return result


def render_map_section(boundaries, ipc_data):
    """Returns (card_html, map_config). map_config is None if the map
    can't be built, in which case the card explains why instead."""
    if not boundaries or not boundaries.get("features"):
        return ("""
        <article class="card card--map">
          <header><h2>Food security by state</h2></header>
          <p class="muted">Map unavailable — could not load admin boundaries this run.</p>
        </article>""", None)

    state_values = extract_ipc_phase_by_state(ipc_data)

    sample_props = boundaries["features"][0].get("properties", {})
    name_prop = _find_key(sample_props, ADMIN_NAME_PROP_HINTS) or _find_key(sample_props, ["name"])
    if not name_prop and sample_props:
        name_prop = next((k for k, v in sample_props.items() if isinstance(v, str)), None)

    # Diagnostics — printed to the workflow log so a name-matching problem
    # is visible without another screenshot round-trip.
    boundary_names = set()
    if name_prop:
        for feat in boundaries.get("features", []):
            v = feat.get("properties", {}).get(name_prop)
            if v:
                boundary_names.add(normalize_admin_name(v))
    matched = boundary_names & set(state_values.keys())
    print(f"  -> Map: name_prop='{name_prop}', sample boundary properties: {sample_props}")
    print(f"  -> Map: {len(state_values)} IPC states found: {sorted(state_values.keys())}")
    print(f"  -> Map: {len(boundary_names)} boundary names found: {sorted(boundary_names)}")
    print(f"  -> Map: {len(matched)} names matched between them: {sorted(matched)}")

    card_html = """
    <article class="card card--map">
      <header><h2>Food security by state</h2><span class="tag">IPC phase</span></header>
      <div id="ssd-map"></div>
      <div class="map-legend">
        <span><i style="background:#3A7D44"></i>1 · Minimal</span>
        <span><i style="background:#C9A227"></i>2 · Stressed</span>
        <span><i style="background:#D9822B"></i>3 · Crisis</span>
        <span><i style="background:#B23A2E"></i>4 · Emergency</span>
        <span><i style="background:#6B1414"></i>5 · Catastrophe</span>
        <span><i style="background:#CBD1C6"></i>No data</span>
      </div>
    </article>"""

    return card_html, {"geojson": boundaries, "name_prop": name_prop, "values": state_values}


def build_map_init_js(map_config):
    """Plain string concatenation (not an f-string) so the JS's own { }
    braces never need escaping — only the JSON payloads are substituted in."""
    if not map_config:
        return ""
    geojson_json = _safe_json(map_config["geojson"])
    values_json = _safe_json(map_config["values"])
    name_prop_json = _safe_json(map_config["name_prop"])

    return (
        "var leafletMap = L.map('ssd-map', { scrollWheelZoom: false }).setView([7.5, 30], 6);\n"
        "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {\n"
        "  attribution: '&copy; OpenStreetMap contributors',\n"
        "  maxZoom: 10\n"
        "}).addTo(leafletMap);\n"
        "var geojsonData = " + geojson_json + ";\n"
        "var stateValues = " + values_json + ";\n"
        "var nameProp = " + name_prop_json + ";\n"
        "var ipcColors = {1:'#3A7D44',2:'#C9A227',3:'#D9822B',4:'#B23A2E',5:'#6B1414'};\n"
        "function normName(s) {\n"
        "  return (s || '').toString().trim().toLowerCase()\n"
        "    .replace(/\\s+/g, ' ')\n"
        "    .replace(/\\s+(state|county|province|region|governorate)$/, '');\n"
        "}\n"
        "function styleFeature(feature) {\n"
        "  var name = normName(feature.properties[nameProp]);\n"
        "  var val = stateValues[name];\n"
        "  var color = val ? (ipcColors[Math.round(val)] || '#CBD1C6') : '#CBD1C6';\n"
        "  return { fillColor: color, weight: 1.5, color: '#3A4440', opacity: 0.8, fillOpacity: 0.8 };\n"
        "}\n"
        "if (nameProp) {\n"
        "  var layer = L.geoJSON(geojsonData, {\n"
        "    style: styleFeature,\n"
        "    onEachFeature: function(feature, layer) {\n"
        "      var rawName = feature.properties[nameProp] || 'Unknown';\n"
        "      var key = normName(rawName);\n"
        "      var val = stateValues[key];\n"
        "      layer.bindTooltip(rawName + (val ? (' \\u2014 IPC Phase ' + val) : ' \\u2014 no data'));\n"
        "    }\n"
        "  }).addTo(leafletMap);\n"
        "  leafletMap.fitBounds(layer.getBounds(), { padding: [10, 10] });\n"
        "} else {\n"
        "  var layer = L.geoJSON(geojsonData, { style: { fillColor: '#CBD1C6', weight: 1, color: '#fff', fillOpacity: 0.85 } }).addTo(leafletMap);\n"
        "  leafletMap.fitBounds(layer.getBounds(), { padding: [10, 10] });\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_expandable_feed_card(key, res, max_items=15):
    """
    For sources that are really a list of individual records (projects,
    allocations) rather than a clean time series — a chart doesn't make
    sense, but a clickable list with full detail on expand does. Uses a
    native <details>/<summary> disclosure, so it needs no JS.
    Field names are guessed generically since exact columns vary by source.
    """
    label = res.get("label", key)
    source = res.get("source", "")
    if res["status"] == "error":
        return f"""
        <article class="card card--error">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">Could not fetch this — {res['error']}</p>
        </article>"""

    data = res["data"]
    if isinstance(data, dict):
        data = data.get("flows") or data.get("data") or []
    if not data:
        return f"""
        <article class="card">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">No records returned.</p>
        </article>"""

    title_keys = ["title", "Title", "ProjectTitle", "projectTitle", "name", "Name", "OrgName", "agency", "Agency"]
    date_keys = ["date", "Date", "DateOfAlloc", "year", "Year", "AllocationDate"]
    amount_keys = ["amount", "Amount", "amountUSD", "budget", "Budget", "TargetAmt"]

    items_html = ""
    for item in list(data)[:max_items]:
        title = next((item[k] for k in title_keys if item.get(k) not in (None, "")), "Untitled")
        date_val = next((item[k] for k in date_keys if item.get(k) not in (None, "")), "")
        amount = next((item[k] for k in amount_keys if item.get(k) not in (None, "")), None)

        summary = f"{str(date_val)[:10]} — {title}" if date_val else str(title)
        if amount is not None:
            try:
                summary += f" (${float(amount):,.0f})"
            except (TypeError, ValueError):
                summary += f" ({amount})"

        detail_rows = "".join(
            f'<div class="detail-row"><span class="detail-key">{k}</span><span>{v}</span></div>'
            for k, v in item.items() if v not in (None, "")
        )

        items_html += f"""
        <details class="feed-detail">
          <summary>{summary}</summary>
          <div class="detail-body">{detail_rows}</div>
        </details>"""

    footer = f"{len(data)} total record(s)"
    if len(data) > max_items:
        footer += f" — showing first {max_items}"

    return f"""
    <article class="card">
      <header><h2>{label}</h2><span class="tag">{source}</span></header>
      <div class="feed-expandable">{items_html}</div>
      <p class="muted small">{footer}</p>
    </article>"""


def render_narrative_card(key, res):
    label = res.get("label", key)
    source = res.get("source", "")
    if res["status"] == "error":
        return f"""
        <article class="card card--error">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">Could not fetch this — {res['error']}</p>
        </article>"""

    data = res["data"]
    if not data:
        return f"""
        <article class="card">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">No records returned.</p>
        </article>"""

    items_html = ""
    for item in list(data)[:8]:
        title = item.get("title") or item.get("name") or item.get("summary") or "Untitled"
        date_str = item.get("date") or item.get("disaster_start_date") or item.get("created_at") or ""
        url = item.get("url") or item.get("link") or ""
        title_html = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
        items_html += f'<li><span class="feed-date">{str(date_str)[:10]}</span>{title_html}</li>'

    return f"""
    <article class="card">
      <header><h2>{label}</h2><span class="tag">{source}</span></header>
      <ul class="feed">{items_html}</ul>
      <p class="muted small">{len(data)} total record(s)</p>
    </article>"""


def render_chart_card(key, res, chart_id):
    label = res.get("label", key)
    source = res.get("source", "")
    if res["status"] == "error":
        return f"""
        <article class="card card--error">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">Could not fetch this — {res['error']}</p>
        </article>""", None

    # These four sources all have the same underlying shape problem: many
    # rows share the same date (one per state, market, or category), so
    # generic "sort by date, take last N rows" logic produces nonsense.
    # Each needs its correct grouping/aggregation applied explicitly.

    if key == "food_security_ipc":
        state_values = extract_ipc_phase_by_state(res["data"])
        if state_values:
            chart = {
                "type": "bar",
                "labels": [name.title() for name in state_values.keys()],
                "values": list(state_values.values()),
                "value_label": "IPC phase",
            }
            card_html = f"""
            <article class="card">
              <header><h2>{label}</h2><span class="tag">{source}</span></header>
              <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
            </article>"""
            return card_html, {"id": chart_id, **chart}

    if key == "rainfall_chirps":
        series = extract_grouped_trend(res["data"], "adm_level", 1, "date", "rfq", agg="mean")
        chart = series_to_chart(series, "Rainfall (% of average)")
        if chart:
            card_html = f"""
            <article class="card">
              <header><h2>{label}</h2><span class="tag">{source}</span></header>
              <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
            </article>"""
            return card_html, {"id": chart_id, **chart}

    if key == "displacement_dtm":
        series = extract_grouped_trend(res["data"], "adminLevel", 0, "reportingDate", "numPresentIdpInd", agg="sum")
        if series is not None and not series.empty:
            # Raw values run into the millions, which get visually truncated
            # on the Y-axis (shows as "000,000" with no leading digits) —
            # scaling to thousands keeps labels short and readable.
            series = (series / 1000).round(1)
        chart = series_to_chart(series, "People displaced (thousands)")
        if chart:
            card_html = f"""
            <article class="card">
              <header><h2>{label}</h2><span class="tag">{source}</span></header>
              <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
            </article>"""
            return card_html, {"id": chart_id, **chart}

    if key == "food_prices_vam":
        series = extract_vam_price_trend(res["data"])
        chart = series_to_chart(series, "Maize price (USD)")
        if chart:
            card_html = f"""
            <article class="card">
              <header><h2>{label}</h2><span class="tag">{source}</span></header>
              <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
            </article>"""
            return card_html, {"id": chart_id, **chart}

    if key == "conflict_acled":
        series = extract_acled_trend(res["data"])
        chart = series_to_chart(series, "Fatalities (national)")
        if chart:
            card_html = f"""
            <article class="card">
              <header><h2>{label}</h2><span class="tag">{source}</span></header>
              <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
            </article>"""
            return card_html, {"id": chart_id, **chart}

    df = _to_dataframe(res["data"])
    chart = detect_chart(df)

    if not chart:
        if df.empty:
            body = '<p class="muted">No records returned.</p>'
        else:
            body = df.head(20).to_html(index=False, escape=True, na_rep="—", classes="mini-table")
        return f"""
        <article class="card">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          {body}
        </article>""", None

    card_html = f"""
    <article class="card">
      <header><h2>{label}</h2><span class="tag">{source}</span></header>
      <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
    </article>"""
    return card_html, {"id": chart_id, **chart}


def render_funding_card(key, res, chart_id):
    label = res.get("label", key)
    source = res.get("source", "")
    if res["status"] == "error":
        return f"""
        <article class="card card--error">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          <p class="muted">Could not fetch this — {res['error']}</p>
        </article>""", None

    data = res["data"]
    if isinstance(data, dict):
        data = data.get("flows") or data.get("data") or []
    df = _to_dataframe(data)
    chart = detect_chart(df)

    if not chart:
        n = len(data) if hasattr(data, "__len__") else 0
        body = f'<p class="muted">{n} record(s) — not enough structure to chart, see CSV export.</p>' if n else '<p class="muted">No records returned.</p>'
        return f"""
        <article class="card">
          <header><h2>{label}</h2><span class="tag">{source}</span></header>
          {body}
        </article>""", None

    card_html = f"""
    <article class="card">
      <header><h2>{label}</h2><span class="tag">{source}</span></header>
      <div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>
    </article>"""
    return card_html, {"id": chart_id, **chart}


def render_kpi_strip(kpis):
    if not kpis:
        return ""
    blocks = ""
    for k in kpis:
        blocks += f"""
        <div class="kpi">
          <span class="kpi-dot kpi-dot--{k['tone']}"></span>
          <span class="kpi-value">{k['value']}</span>
          <span class="kpi-label">{k['label']}</span>
        </div>"""
    return f'<section class="kpi-strip">{blocks}</section>'


def render_html(country, results, out_dir):
    kpis = render_kpi_strip(compute_kpis(results))

    # Map: only bother fetching boundaries if we have something to show on it
    map_html, map_config = "", None
    ipc_res = results.get("food_security_ipc")
    if ipc_res is not None:
        boundaries = fetch_boundaries()
        map_html, map_config = render_map_section(boundaries, ipc_res.get("data", []))

    narrative_html, chart_html, funding_html = "", "", ""
    charts_js = []
    chart_counter = 0

    for key, res in results.items():
        # IPC is fully covered by the map now — showing it again as a
        # separate bar chart in Indicators is redundant. Data is still
        # fetched/kept in `results` for the map and KPI strip to use.
        if key == "food_security_ipc":
            continue

        category = res.get("category", "chart")
        if category == "narrative":
            narrative_html += render_narrative_card(key, res)
        elif key == "funding_cerf":
            # CERF data is a list of project summaries, not a time series —
            # a chart doesn't fit; an expandable list does.
            funding_html += render_expandable_feed_card(key, res)
        elif category == "funding":
            chart_counter += 1
            html, chart_data = render_funding_card(key, res, f"chart_{chart_counter}")
            funding_html += html
            if chart_data:
                charts_js.append(chart_data)
        else:
            chart_counter += 1
            html, chart_data = render_chart_card(key, res, f"chart_{chart_counter}")
            chart_html += html
            if chart_data:
                charts_js.append(chart_data)

    chart_init_js = ""
    for c in charts_js:
        cfg_type = "line" if c["type"] == "line" else "bar"
        chart_init_js += f"""
        new Chart(document.getElementById('{c["id"]}'), {{
          type: '{cfg_type}',
          data: {{
            labels: {_safe_json(c["labels"])},
            datasets: [{{
              label: {_safe_json(c["value_label"])},
              data: {_safe_json(c["values"])},
              borderColor: '#1F6F5C',
              backgroundColor: {_safe_json('rgba(31,111,92,0.12)' if cfg_type == 'line' else '#C98A3D')},
              fill: {str(cfg_type == 'line').lower()},
              tension: 0.3,
              borderWidth: 2,
              borderRadius: 4,
            }}]
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
              x: {{ grid: {{ display: false }}, ticks: {{ font: {{ family: 'IBM Plex Mono', size: 10 }} }} }},
              y: {{ grid: {{ color: '#E1E5DC' }}, ticks: {{ font: {{ family: 'IBM Plex Mono', size: 10 }} }} }}
            }}
          }}
        }});"""

    map_init_js = build_map_init_js(map_config)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{country} — Humanitarian Situation Monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=Roboto+Slab:wght@600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {{
    --bg: #EEF1EC;
    --paper: #FFFFFF;
    --ink: #1B2320;
    --muted: #667169;
    --border: #DDE2D8;
    --primary: #1F6F5C;
    --primary-soft: rgba(31,111,92,0.10);
    --warn: #C98A3D;
    --alert: #B23A2E;
  }}
  html {{ scroll-behavior: smooth; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: 'IBM Plex Sans', sans-serif; -webkit-font-smoothing: antialiased;
  }}
  h1, h2 {{ font-family: 'Roboto Slab', serif; margin: 0; }}
  .mono {{ font-family: 'IBM Plex Mono', monospace; }}

  .masthead {{
    background: var(--ink); color: var(--bg); padding: 28px 32px;
  }}
  .masthead h1 {{ font-size: 24px; letter-spacing: 0.01em; }}
  .masthead p {{ margin: 6px 0 0; font-family: 'IBM Plex Mono', monospace; font-size: 12px; opacity: 0.7; }}

  .subnav {{
    position: sticky; top: 0; z-index: 20; background: var(--paper);
    border-bottom: 1px solid var(--border); padding: 0 32px;
    display: flex; gap: 4px; overflow-x: auto;
  }}
  .subnav a {{
    font-family: 'IBM Plex Mono', monospace; font-size: 12px; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--muted); text-decoration: none; padding: 14px 12px;
    border-bottom: 2px solid transparent; white-space: nowrap;
  }}
  .subnav a:hover {{ color: var(--primary); border-bottom-color: var(--primary); }}
  section {{ scroll-margin-top: 52px; }}

  .kpi-strip {{
    display: flex; flex-wrap: wrap; gap: 0; background: var(--paper);
    border-bottom: 1px solid var(--border);
  }}
  .kpi {{
    flex: 1; min-width: 160px; padding: 18px 24px; border-right: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 4px;
  }}
  .kpi-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--primary); }}
  .kpi-dot--ok {{ background: var(--primary); }}
  .kpi-dot--warn {{ background: var(--warn); }}
  .kpi-dot--alert {{ background: var(--alert); }}
  .kpi-dot--neutral {{ background: var(--muted); }}
  .kpi-value {{ font-family: 'IBM Plex Mono', monospace; font-size: 26px; font-weight: 600; }}
  .kpi-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }}

  main {{ max-width: 1200px; margin: 0 auto; padding: 32px; }}
  .section-label {{
    font-family: 'IBM Plex Mono', monospace; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); margin: 32px 0 12px; display: flex; align-items: center; gap: 10px;
  }}
  .section-label::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}

  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }}
  .grid--wide {{ grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); }}
  .grid--narrow {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}

  .card {{
    background: var(--paper); border: 1px solid var(--border); border-radius: 8px; padding: 20px;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
  }}
  .card:hover {{ box-shadow: 0 4px 16px rgba(27,35,32,0.06); }}
  .card--error {{ border-color: #E3B7AE; background: #FBF3F1; }}
  .card--map {{ grid-column: 1 / -1; }}
  .card header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; gap: 10px; }}
  .card h2 {{ font-size: 15px; }}
  .tag {{
    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--primary);
    background: var(--primary-soft); padding: 3px 8px; border-radius: 4px; white-space: nowrap;
  }}
  .muted {{ color: var(--muted); font-size: 13px; }}
  .small {{ font-size: 11px; margin-top: 10px; }}

  #ssd-map {{ height: 420px; border-radius: 6px; z-index: 1; }}
  .chart-wrap {{ position: relative; height: 220px; }}
  .map-legend {{
    display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px; font-size: 11px; color: var(--muted);
  }}
  .map-legend span {{ display: flex; align-items: center; gap: 5px; }}
  .map-legend i {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}

  .feed-expandable {{ display: flex; flex-direction: column; gap: 6px; }}
  .feed-detail {{ border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; }}
  .feed-detail summary {{
    cursor: pointer; font-size: 13px; list-style: none; display: flex; align-items: center;
  }}
  .feed-detail summary::-webkit-details-marker {{ display: none; }}
  .feed-detail summary::before {{ content: '▸'; margin-right: 8px; color: var(--primary); font-size: 11px; }}
  .feed-detail[open] summary::before {{ content: '▾'; }}
  .detail-body {{
    margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);
    display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 6px 16px;
  }}
  .detail-row {{ font-size: 12px; display: flex; flex-direction: column; gap: 1px; }}
  .detail-key {{
    font-family: 'IBM Plex Mono', monospace; font-size: 10px; text-transform: uppercase;
    color: var(--muted); letter-spacing: 0.03em;
  }}

  .feed {{ list-style: none; margin: 0; padding: 0; }}
  .feed li {{
    padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px; line-height: 1.4;
  }}
  .feed li:last-child {{ border-bottom: none; }}
  .feed a {{ color: var(--ink); text-decoration: none; }}
  .feed a:hover {{ color: var(--primary); text-decoration: underline; }}
  .feed-date {{
    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--muted);
    display: block; margin-bottom: 2px;
  }}

  .mini-table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  .mini-table th, .mini-table td {{
    border-bottom: 1px solid var(--border); text-align: left; padding: 5px 8px;
    max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  .mini-table th {{ color: var(--muted); font-weight: 500; }}

  footer {{
    max-width: 1200px; margin: 0 auto; padding: 24px 32px 48px; color: var(--muted); font-size: 12px;
  }}
</style>
</head>
<body>
  <div class="masthead">
    <h1>{country} — Humanitarian Situation Monitor</h1>
    <p>GENERATED {date.today().isoformat()} · SOURCES: RELIEFWEB · IFRC GO · HDX · FTS · CERF · CBPF</p>
  </div>

  <nav class="subnav">
    <a href="#map-section">Map</a>
    <a href="#situation">Situation feed</a>
    <a href="#indicators">Indicators</a>
    <a href="#funding">Funding</a>
  </nav>

  {kpis}

  <main>
    <section id="map-section">
      <div class="section-label">Geographic overview</div>
      <div class="grid">{map_html}</div>
    </section>

    <section id="situation">
      <div class="section-label">Situation feed</div>
      <div class="grid grid--narrow">{narrative_html}</div>
    </section>

    <section id="indicators">
      <div class="section-label">Indicators</div>
      <div class="grid grid--wide">{chart_html}</div>
    </section>

    <section id="funding">
      <div class="section-label">Funding</div>
      <div class="grid">{funding_html}</div>
    </section>
  </main>

  <footer>
    Data pulled live from public humanitarian APIs. No AI classification or forecasting is applied —
    this dashboard shows source data only. Cross-check before use in decision-making.
  </footer>

  <script>
    Chart.defaults.font.family = "'IBM Plex Sans', sans-serif";
    {chart_init_js}
    {map_init_js}
  </script>
</body>
</html>"""

    out_path = os.path.join(out_dir, "dashboard.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Generate a humanitarian situation dashboard for a country.")
    parser.add_argument("--country", help="Country name, e.g. South Sudan")
    parser.add_argument("--iso3", help="ISO3 code, e.g. SSD")
    parser.add_argument("--indicators", help="Comma-separated indicator keys, or 'all'")
    parser.add_argument("--output-html", help="Also copy the rendered dashboard to this exact path "
                                                "(e.g. docs/index.html) — used for CI/GitHub Pages publishing.")
    parser.add_argument("--output-csv-dir", help="Also copy every indicator's raw CSV into this exact "
                                                   "folder (e.g. data/) — overwrites each run, so you (or I) "
                                                   "can inspect real data directly from a stable GitHub link.")
    args = parser.parse_args()

    if args.country and args.iso3 and args.indicators:
        country, iso3 = args.country, args.iso3.upper()
        indicator_keys = list(INDICATORS.keys()) if args.indicators == "all" else [
            k.strip() for k in args.indicators.split(",")
        ]
    else:
        country, iso3, indicator_keys = prompt_for_selection()

    if not indicator_keys:
        print("No indicators selected — nothing to fetch.")
        return

    out_dir = os.path.join(OUTPUT_ROOT, f"{country.replace(' ', '_')}_{date.today().isoformat()}")
    os.makedirs(out_dir, exist_ok=True)

    results = fetch_all(country, iso3, indicator_keys)
    save_csvs(results, out_dir)
    html_path = render_html(country, results, out_dir)

    if args.output_html:
        import shutil
        os.makedirs(os.path.dirname(args.output_html) or ".", exist_ok=True)
        shutil.copy(html_path, args.output_html)
        print(f"Also copied to: {args.output_html}")

    if args.output_csv_dir:
        import shutil
        import glob
        os.makedirs(args.output_csv_dir, exist_ok=True)
        for csv_path in glob.glob(os.path.join(out_dir, "*.csv")):
            dest = os.path.join(args.output_csv_dir, os.path.basename(csv_path))
            shutil.copy(csv_path, dest)
        print(f"Also copied CSVs to: {args.output_csv_dir}")

    print(f"\nDone. Dashboard: {html_path}")


if __name__ == "__main__":
    main()
