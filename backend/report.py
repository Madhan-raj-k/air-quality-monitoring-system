"""Build the station report from the live hour, recent hours, and a fresh model."""

import threading
import time
from datetime import timedelta

import pandas as pd

import live_aqi
import train_aqi_model as aqi

STATION = {
    "name": "Chikkaballapur Rural",
    "place": "Chikkaballapur",
    "agency": "KSPCB",
    "site_id": "site_1557",
    "source": "CPCB live feed",
}

CACHE_SECONDS = 300
_LOCK = threading.Lock()
_CACHE = {"expires": 0.0, "payload": None}


def displayed_aqi(value):
    number = float(value)
    rounded = round(number, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def reading(value, moment, horizon_hours=None):
    shown = displayed_aqi(value)
    band = aqi.category_and_advice(shown)
    if band is None:
        return None
    category, advice, color = band
    payload = {
        "datetime": pd.Timestamp(moment).strftime("%Y-%m-%d %H:%M"),
        "aqi": shown,
        "category": category,
        "advice": advice,
        "color": color,
    }
    if horizon_hours is not None:
        payload["horizon_hours"] = int(horizon_hours)
    return payload


def band_rows():
    rows = []
    lower = 0
    for upper, name, advice, color in aqi.BANDS:
        rows.append(
            {
                "range": f"{lower}–{upper}",
                "category": name,
                "advice": advice,
                "color": color,
            }
        )
        lower = upper + 1
    return rows


def last_24_hours(hourly, latest_time):
    start = pd.Timestamp(latest_time) - timedelta(hours=23)
    window = hourly.loc[start:latest_time, "aqi"]
    points = []
    values = []
    for moment, value in window.items():
        if pd.isna(value):
            points.append(
                {"datetime": moment.strftime("%Y-%m-%d %H:%M"), "aqi": None}
            )
            continue
        shown = displayed_aqi(value)
        points.append(
            {"datetime": moment.strftime("%Y-%m-%d %H:%M"), "aqi": shown}
        )
        values.append(shown)
    summary = {
        "hours_reported": len(values),
        "hours_missing": int(window.isna().sum()),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "average": round(sum(values) / len(values), 1) if values else None,
    }
    return points, summary


def forecast_with(model, featured, latest_time):
    if latest_time not in featured.index:
        return None, "The latest hour is not on the station timeline."
    row = featured.loc[[latest_time], aqi.FEATURES]
    if row.isna().any(axis=None):
        return None, (
            "A 6-hour forecast is not available for this hour because the "
            "recent record has a gap. Missing hours are not treated as zero."
        )
    predicted = float(model.predict(row)[0])
    predicted = min(500.0, max(0.0, predicted))
    due_at = pd.Timestamp(latest_time) + timedelta(hours=aqi.HORIZON_HOURS)
    return reading(predicted, due_at, aqi.HORIZON_HOURS), None


def _build_report():
    archive = aqi.load_history()
    if archive.empty:
        raise RuntimeError("history.csv has no AQI rows.")
    merged, info = live_aqi.build_merged_history(archive)
    series = merged[["datetime", "aqi"]].copy()
    hourly = aqi.hourly_frame(series)
    featured = aqi.add_features(hourly)
    ready = aqi.supervised_rows(featured)
    model = aqi.fit_full(ready)
    latest_time = pd.Timestamp(info["current_datetime"])
    current = reading(float(info["current_aqi"]), latest_time)
    if current is None:
        raise RuntimeError("The current AQI has no category.")
    current["source"] = info["current_source"]
    points, summary = last_24_hours(hourly, latest_time)
    summary["detail"] = info["detail"]
    summary["official_hours"] = info["official_hours"]
    summary["fallback_hours"] = info["fallback_hours"]
    forecast, forecast_note = forecast_with(model, featured, latest_time)
    station = dict(STATION)
    station["source"] = info["current_source"]
    return {
        "station": station,
        "current": current,
        "last_24_hours": points,
        "last_24_summary": summary,
        "forecast": forecast,
        "forecast_note": forecast_note,
        "bands": band_rows(),
    }


def build_report():
    now = time.time()
    with _LOCK:
        cached = _CACHE["payload"]
        if cached is not None and now < _CACHE["expires"]:
            return cached
        payload = _build_report()
        _CACHE["payload"] = payload
        _CACHE["expires"] = time.time() + CACHE_SECONDS
        return payload
