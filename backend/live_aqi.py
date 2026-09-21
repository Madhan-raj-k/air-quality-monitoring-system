"""Fetch the current Chikkaballapur Rural AQI and the hours around it.

The current reading comes from the CPCB station feed. Hourly history comes
from the CPCB AQI repository when that month has been published. Hours the
repository does not have yet are filled from Open-Meteo at the station
coordinates and converted to the CPCB AQI scale. Missing hours stay missing.
"""

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import pandas as pd
from zoneinfo import ZoneInfo

SITE_ID = "site_1557"
STATION_NAME = "Chikkaballapur Rural, Chikkaballapur - KSPCB"
LATITUDE = 13.428828
LONGITUDE = 77.731418
IST = ZoneInfo("Asia/Kolkata")

CPCB_ORIGIN = "https://airquality.cpcb.gov.in"
LIVE_FEED_URL = f"{CPCB_ORIGIN}/caaqms/iit_rss_feed_with_coordinates"
FILE_PATH_URL = f"{CPCB_ORIGIN}/caaqms-common/dataRepository/file-path"
DOWNLOAD_URL = f"{CPCB_ORIGIN}/caaqms-common/dataRepository/download-excel-file"
OPEN_METEO_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# CPCB National AQI breakpoints: concentration low, high, index low, high.
# PM, NO2 and SO2 use a 24-hour average. CO and ozone use an 8-hour average.
BREAKPOINTS = {
    "pm25": (
        (0, 30, 0, 50),
        (30, 60, 51, 100),
        (60, 90, 101, 200),
        (90, 120, 201, 300),
        (120, 250, 301, 400),
        (250, 380, 401, 500),
    ),
    "pm10": (
        (0, 50, 0, 50),
        (50, 100, 51, 100),
        (100, 250, 101, 200),
        (250, 350, 201, 300),
        (350, 430, 301, 400),
        (430, 510, 401, 500),
    ),
    "no2": (
        (0, 40, 0, 50),
        (40, 80, 51, 100),
        (80, 180, 101, 200),
        (180, 280, 201, 300),
        (280, 400, 301, 400),
        (400, 520, 401, 500),
    ),
    "so2": (
        (0, 40, 0, 50),
        (40, 80, 51, 100),
        (80, 380, 101, 200),
        (380, 800, 201, 300),
        (800, 1600, 301, 400),
        (1600, 2000, 401, 500),
    ),
    "co": (
        (0, 1.0, 0, 50),
        (1.0, 2.0, 51, 100),
        (2.0, 10, 101, 200),
        (10, 17, 201, 300),
        (17, 34, 301, 400),
        (34, 50, 401, 500),
    ),
    "o3": (
        (0, 50, 0, 50),
        (50, 100, 51, 100),
        (100, 168, 101, 200),
        (168, 208, 201, 300),
        (208, 748, 301, 400),
        (748, 1000, 401, 500),
    ),
}

SOURCE_ARCHIVE = "CPCB archive"
SOURCE_REPOSITORY = "CPCB hourly repository"
SOURCE_LIVE = "CPCB live feed"
SOURCE_FALLBACK = "Open-Meteo"
OFFICIAL_SOURCES = {SOURCE_ARCHIVE, SOURCE_REPOSITORY, SOURCE_LIVE}
PRIORITY = {
    SOURCE_FALLBACK: 1,
    SOURCE_ARCHIVE: 2,
    SOURCE_REPOSITORY: 3,
    SOURCE_LIVE: 4,
}
SHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
USER_AGENT = "AirQualityMonitor/1.0"


def ist_now():
    return pd.Timestamp(datetime.now(IST).replace(tzinfo=None)).floor("h")


def _request(url, payload=None, timeout=30):
    data = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = CPCB_ORIGIN
        headers["Referer"] = f"{CPCB_ORIGIN}/ccr/"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def _request_json(url, payload=None, timeout=30):
    return json.loads(_request(url, payload=payload, timeout=timeout).decode("utf-8"))


def sub_index(concentration, breakpoints):
    if concentration is None or pd.isna(concentration) or concentration < 0:
        return None
    for low, high, index_low, index_high in breakpoints:
        if concentration <= high:
            if high == low:
                return float(index_high)
            span = (index_high - index_low) / (high - low)
            return span * (concentration - low) + index_low
    return 500.0


def indian_aqi(pm25, pm10, no2, so2, co_mg, ozone):
    """CPCB AQI from averaged concentrations. Needs PM and two other pollutants."""
    parts = {
        "pm25": sub_index(pm25, BREAKPOINTS["pm25"]),
        "pm10": sub_index(pm10, BREAKPOINTS["pm10"]),
        "no2": sub_index(no2, BREAKPOINTS["no2"]),
        "so2": sub_index(so2, BREAKPOINTS["so2"]),
        "co": sub_index(co_mg, BREAKPOINTS["co"]),
        "o3": sub_index(ozone, BREAKPOINTS["o3"]),
    }
    available = [value for value in parts.values() if value is not None]
    has_pm = parts["pm25"] is not None or parts["pm10"] is not None
    if not has_pm or len(available) < 3:
        return None
    return int(round(min(500, max(available))))


def fetch_live_station():
    """Latest published AQI for site_1557 from the CPCB station feed."""
    payload = _request_json(LIVE_FEED_URL, timeout=40)
    for state in payload.get("country") or []:
        for city in state.get("citiesInState") or []:
            for station in city.get("stationsInCity") or []:
                if station.get("siteId") != SITE_ID:
                    continue
                raw_aqi = station.get("airQualityIndexValue")
                raw_time = station.get("lastUpdate")
                if raw_aqi in (None, "", "-") or not raw_time:
                    return None
                try:
                    moment = datetime.strptime(raw_time, "%d-%m-%Y %H:%M:%S")
                    value = float(raw_aqi)
                except (TypeError, ValueError):
                    return None
                if value < 0 or value > 500:
                    return None
                return {
                    "datetime": pd.Timestamp(moment).floor("h"),
                    "aqi": value,
                    "name": station.get("stationName") or STATION_NAME,
                }
    return None


def _column_index(cell_ref):
    letters = "".join(character for character in cell_ref if character.isalpha())
    index = 0
    for character in letters:
        index = index * 26 + ord(character.upper()) - 64
    return index - 1


def _cell_text(cell):
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find("m:is/m:t", SHEET_NS)
        if node is None or node.text is None:
            return None
        text = node.text.strip()
        return text or None
    node = cell.find("m:v", SHEET_NS)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def parse_hourly_aqi_xlsx(content, year, month):
    """Read a CPCB station hourly workbook: one row per day, one column per hour."""
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in workbook.namelist():
            sheets = [name for name in workbook.namelist() if name.startswith("xl/worksheets/")]
            if not sheets:
                return []
            sheet_name = sheets[0]
        sheet = workbook.read(sheet_name)
    root = ET.fromstring(sheet)
    rows = []
    for row in root.findall("m:sheetData/m:row", SHEET_NS):
        cells = {}
        for cell in row.findall("m:c", SHEET_NS):
            cells[_column_index(cell.get("r") or "")] = _cell_text(cell)
        day_text = cells.get(0)
        if day_text is None:
            continue
        try:
            day = int(float(day_text))
        except ValueError:
            continue
        if not 1 <= day <= 31:
            continue
        for hour in range(24):
            raw = cells.get(hour + 1)
            if raw is None:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if not 0 <= value <= 500:
                continue
            try:
                moment = datetime(year, month, day, hour)
            except ValueError:
                continue
            rows.append((moment, value))
    return rows


def _repository_year(today, year):
    payload = {
        "station_id": SITE_ID,
        "station_name": STATION_NAME,
        "state": "Karnataka",
        "city": "Chikkaballapur",
        "year": str(year),
        "frequency": "1H",
        "dataType": "stationLevel",
    }
    try:
        listed = _request_json(FILE_PATH_URL, payload=payload, timeout=40)
    except RuntimeError:
        return []
    entries = []
    for entry in listed.get("data") or []:
        month_name = entry.get("month")
        filepath = entry.get("filepath")
        if not month_name or not filepath:
            continue
        try:
            month = datetime.strptime(month_name, "%B").month
        except ValueError:
            continue
        year_match = re.search(r"(20\d{2})", filepath)
        file_year = int(year_match.group(1)) if year_match else year
        entries.append((file_year, month, filepath))
    if not entries:
        return []
    previous_month = 12 if today.month == 1 else today.month - 1
    recent_months = {today.month, previous_month}
    chosen = [entry for entry in entries if entry[1] in recent_months]
    if not chosen and year == today.year:
        chosen = [entries[-1]]
    rows = []
    for file_year, month, filepath in chosen:
        url = DOWNLOAD_URL + "?file_name=" + filepath
        try:
            content = _request(url, timeout=40)
        except RuntimeError:
            continue
        if not content.startswith(b"PK"):
            continue
        rows.extend(parse_hourly_aqi_xlsx(content, file_year, month))
    return rows


def fetch_repository_hours(today):
    """Hourly AQI from the CPCB repository for the recent published months."""
    years = [int(today.year)]
    if today.month == 1:
        years.append(int(today.year) - 1)
    rows = []
    for year in years:
        rows.extend(_repository_year(today, year))
    return rows


def _co_as_milligrams(series):
    numeric = pd.to_numeric(series, errors="coerce")
    median = numeric.median(skipna=True)
    # Open-Meteo publishes CO in µg/m³. CPCB breakpoints are mg/m³.
    if pd.notna(median) and median > 20:
        return numeric / 1000.0
    return numeric


def fetch_fallback_hours(end):
    """Hourly CPCB-scale AQI at the station coordinates from Open-Meteo."""
    query = urllib.parse.urlencode(
        {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "hourly": ",".join(
                (
                    "pm10",
                    "pm2_5",
                    "carbon_monoxide",
                    "nitrogen_dioxide",
                    "sulphur_dioxide",
                    "ozone",
                )
            ),
            "timezone": "Asia/Kolkata",
            "past_days": 28,
            "forecast_days": 1,
        }
    )
    payload = _request_json(f"{OPEN_METEO_URL}?{query}", timeout=40)
    hourly = payload.get("hourly") or {}
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(hourly.get("time") or []),
            "pm25": hourly.get("pm2_5") or [],
            "pm10": hourly.get("pm10") or [],
            "no2": hourly.get("nitrogen_dioxide") or [],
            "so2": hourly.get("sulphur_dioxide") or [],
            "co": hourly.get("carbon_monoxide") or [],
            "o3": hourly.get("ozone") or [],
        }
    )
    if frame.empty:
        return []
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    frame = frame.drop_duplicates("datetime", keep="last")
    frame = frame[frame["datetime"] <= pd.Timestamp(end)]
    for column in ("pm25", "pm10", "no2", "so2", "co", "o3"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["co"] = _co_as_milligrams(frame["co"])
    indexed = frame.set_index("datetime")
    pm25 = indexed["pm25"].rolling(24, min_periods=24).mean()
    pm10 = indexed["pm10"].rolling(24, min_periods=24).mean()
    no2 = indexed["no2"].rolling(24, min_periods=24).mean()
    so2 = indexed["so2"].rolling(24, min_periods=24).mean()
    co = indexed["co"].rolling(8, min_periods=8).mean()
    ozone = indexed["o3"].rolling(8, min_periods=8).mean()
    rows = []
    for moment in indexed.index:
        value = indian_aqi(
            pm25.loc[moment],
            pm10.loc[moment],
            no2.loc[moment],
            so2.loc[moment],
            co.loc[moment],
            ozone.loc[moment],
        )
        if value is None:
            continue
        rows.append((moment, value))
    return rows


def _frame_from_rows(rows, source):
    frame = pd.DataFrame(rows, columns=["datetime", "aqi"])
    if frame.empty:
        return frame
    frame["datetime"] = pd.to_datetime(frame["datetime"]).dt.floor("h")
    frame["aqi"] = pd.to_numeric(frame["aqi"], errors="coerce")
    frame = frame.dropna(subset=["datetime", "aqi"])
    frame["source"] = source
    return frame


def _series_detail(window):
    if window.empty:
        return "No hourly AQI was available for this window."
    official = int(window["source"].isin(OFFICIAL_SOURCES).sum())
    fallback = int((window["source"] == SOURCE_FALLBACK).sum())
    if fallback == 0 and official:
        return "Official hourly AQI from CPCB for Chikkaballapur Rural."
    if official and fallback:
        hour_word = "hour" if fallback == 1 else "hours"
        return (
            "Current hour is the CPCB station reading. "
            f"{fallback} earlier {hour_word} in this window come from Open-Meteo "
            "at this location, on the CPCB AQI scale, because CPCB has not "
            "published those hourly files yet."
        )
    return (
        "CPCB did not return hourly AQI for this window. These hours are "
        "Open-Meteo at Chikkaballapur Rural, converted to the CPCB AQI scale."
    )


def build_merged_history(history):
    """Combine the saved archive with the live hour and any unpublished hours.

    Returns the merged datetime/aqi/source frame, trimmed at the current hour,
    and a small description of where the current reading came from.
    """
    now = ist_now()
    live = None
    live_error = None
    try:
        live = fetch_live_station()
    except RuntimeError as exc:
        live_error = str(exc)

    fresh_live = False
    if live is not None:
        age = now - pd.Timestamp(live["datetime"])
        # The feed often keeps one published hour for much of the day.
        # Treat that stamp as current until it is more than a day behind.
        fresh_live = pd.Timedelta(hours=-2) <= age <= pd.Timedelta(hours=26)
    end = pd.Timestamp(live["datetime"]) if fresh_live else now

    archive = history[["datetime", "aqi"]].copy()
    archive["datetime"] = pd.to_datetime(archive["datetime"]).dt.floor("h")
    archive["source"] = SOURCE_ARCHIVE

    repository_rows = []
    repository_error = None
    try:
        repository_rows = fetch_repository_hours(end)
    except RuntimeError as exc:
        repository_error = str(exc)

    fallback_rows = []
    fallback_error = None
    try:
        fallback_rows = fetch_fallback_hours(end)
    except RuntimeError as exc:
        fallback_error = str(exc)

    parts = [
        archive,
        _frame_from_rows(repository_rows, SOURCE_REPOSITORY),
        _frame_from_rows(fallback_rows, SOURCE_FALLBACK),
    ]
    if fresh_live:
        parts.append(
            _frame_from_rows(
                [(live["datetime"], live["aqi"])],
                SOURCE_LIVE,
            )
        )
    merged = pd.concat([part for part in parts if not part.empty], ignore_index=True)
    if merged.empty:
        raise RuntimeError(
            "No AQI could be loaded from CPCB or from Open-Meteo. "
            + " ".join(item for item in (live_error, repository_error, fallback_error) if item)
        )
    merged["_priority"] = merged["source"].map(PRIORITY).fillna(0)
    merged = merged[merged["datetime"] <= end]
    merged = merged.sort_values(["datetime", "_priority"])
    merged = merged.drop_duplicates("datetime", keep="last")
    merged = merged.drop(columns="_priority").reset_index(drop=True)
    if merged.empty:
        raise RuntimeError("The AQI series has no hours at or before the current time.")

    if fresh_live:
        current_time = pd.Timestamp(live["datetime"])
        current_aqi = float(live["aqi"])
        current_source = SOURCE_LIVE
    else:
        current = merged.iloc[-1]
        current_time = pd.Timestamp(current["datetime"])
        current_aqi = float(current["aqi"])
        current_source = str(current["source"])

    start = current_time - pd.Timedelta(hours=23)
    window = merged[(merged["datetime"] >= start) & (merged["datetime"] <= current_time)]
    if not fresh_live and live_error:
        detail = (
            "The CPCB station feed could not be read "
            f"({live_error}). "
            + _series_detail(window)
        )
    else:
        detail = _series_detail(window)

    info = {
        "current_datetime": current_time,
        "current_aqi": current_aqi,
        "current_source": current_source,
        "detail": detail,
        "official_hours": int(window["source"].isin(OFFICIAL_SOURCES).sum()),
        "fallback_hours": int((window["source"] == SOURCE_FALLBACK).sum()),
        "window_hours": int(len(window)),
    }
    return merged, info
