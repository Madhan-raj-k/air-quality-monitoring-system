"""Train the 6-hour AQI forecast for Chikkaballapur Rural.

The model predicts the AQI number only. Category and health guidance are
assigned from fixed AQI bands, not by the model.
"""

from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "history.csv"
MODEL_PATH = Path(__file__).resolve().parent / "aqi_model.joblib"

HORIZON_HOURS = 6
TEST_DAYS = 14
FEATURES = [
    "aqi",
    "aqi_lag_1h",
    "aqi_lag_3h",
    "aqi_lag_24h",
    "aqi_mean_24h",
    "aqi_max_24h",
    "hour",
    "dow",
    "month",
]

# Inclusive upper bounds. 50 is Good, 51 is Satisfactory, and so on.
BANDS = [
    (50, "Good", "normal outdoor activity", "#0b7a43"),
    (100, "Satisfactory", "sensitive people take care", "#8fbf1f"),
    (200, "Moderate", "children / asthma shorten outdoor PT", "#e6b800"),
    (300, "Poor", "avoid long outdoor exercise", "#e06a12"),
    (400, "Very Poor", "prefer indoor", "#c0102e"),
    (500, "Severe", "restrict outdoor exposure", "#6b1020"),
]


def category_and_advice(aqi):
    """Return (category, advice, color) from the AQI bands, or None."""
    if pd.isna(aqi):
        return None
    value = float(aqi)
    if value < 0:
        return None
    for upper, name, advice, color in BANDS:
        if value <= upper:
            return name, advice, color
    name, advice, color = BANDS[-1][1], BANDS[-1][2], BANDS[-1][3]
    return name, advice, color


def load_history(path=DATA_PATH):
    history = pd.read_csv(path, parse_dates=["datetime"])
    history = history[["datetime", "aqi"]].copy()
    history = history.dropna(subset=["datetime", "aqi"])
    history = history.sort_values("datetime")
    history = history.drop_duplicates("datetime", keep="last")
    return history.reset_index(drop=True)


def hourly_frame(history):
    """One row per clock hour. Missing official hours stay NaN, never 0."""
    timeline = pd.date_range(
        history["datetime"].min(),
        history["datetime"].max(),
        freq="h",
    )
    frame = history.set_index("datetime")[["aqi"]].reindex(timeline)
    frame.index.name = "datetime"
    return frame


def add_features(hourly):
    frame = hourly.copy()
    # Shifts use the hourly clock, so a skipped CPCB hour stays a real gap.
    frame["aqi_lag_1h"] = frame["aqi"].shift(1)
    frame["aqi_lag_3h"] = frame["aqi"].shift(3)
    frame["aqi_lag_24h"] = frame["aqi"].shift(24)
    window_24h = frame["aqi"].rolling(24, min_periods=24)
    frame["aqi_mean_24h"] = window_24h.mean()
    frame["aqi_max_24h"] = window_24h.max()
    frame["hour"] = frame.index.hour
    frame["dow"] = frame.index.dayofweek
    frame["month"] = frame.index.month
    frame["aqi_in_6h"] = frame["aqi"].shift(-HORIZON_HOURS)
    return frame


def supervised_rows(featured):
    needed = FEATURES + ["aqi_in_6h"]
    return featured.dropna(subset=needed).copy()


def regression_scores(y_true, y_pred):
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    return mae, rmse


def category_accuracy(y_true, y_pred):
    matches = []
    for actual, predicted in zip(y_true, y_pred):
        actual_band = category_and_advice(actual)
        predicted_band = category_and_advice(predicted)
        if actual_band is None or predicted_band is None:
            matches.append(False)
        else:
            matches.append(actual_band[0] == predicted_band[0])
    return float(np.mean(matches))


def train_and_evaluate(ready):
    cutoff = pd.Timestamp(ready.index.max()) - timedelta(days=TEST_DAYS)
    train = ready[ready.index < cutoff]
    test = ready[ready.index >= cutoff]
    if train.empty or test.empty:
        raise RuntimeError("Train or test split is empty. Check the AQI series.")

    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=1)
    model.fit(train[FEATURES], train["aqi_in_6h"])
    pred = model.predict(test[FEATURES])

    y_true = test["aqi_in_6h"]
    scores = {
        "future = now": regression_scores(y_true, test["aqi"]),
        "future = same hour yesterday": regression_scores(y_true, test["aqi_lag_24h"]),
        "RandomForest": regression_scores(y_true, pred),
    }
    accuracy = {
        "RandomForest": category_accuracy(y_true, pred),
        "future = now": category_accuracy(y_true, test["aqi"]),
    }
    return model, cutoff, train, test, scores, accuracy


def print_report(history, hourly, ready, cutoff, train, test, scores, accuracy):
    missing = int(hourly["aqi"].isna().sum())
    print(f"Hourly AQI rows in the training series: {len(history)}")
    print(
        "Hourly timeline: "
        f"{hourly.index.min():%Y-%m-%d %H:%M} through "
        f"{hourly.index.max():%Y-%m-%d %H:%M}"
    )
    print(
        f"Missing hours on that timeline: {missing} "
        "(left missing, not filled with 0)"
    )
    print(f"Rows with complete features and a 6-hour target: {len(ready)}")
    print(f"Train: before {cutoff:%Y-%m-%d %H:%M} ({len(train)} rows)")
    print(
        "Test: last 14 days, "
        f"{test.index.min():%Y-%m-%d %H:%M} through "
        f"{test.index.max():%Y-%m-%d %H:%M} ({len(test)} rows)"
    )
    print()
    print("MAE and RMSE on the test window, in AQI points")
    for name, (mae, rmse) in scores.items():
        print(f"  {name:<32} MAE {mae:6.2f}   RMSE {rmse:6.2f}")
    print()
    print("AQI-category accuracy (fixed bands, not the model)")
    for name, accuracy_value in accuracy.items():
        print(f"  {name:<32} {accuracy_value * 100:5.1f}%")
    print()
    mae_now = scores["future = now"][0]
    mae_rf = scores["RandomForest"][0]
    if mae_now <= mae_rf:
        print(
            "Persistence beat the forest "
            f"(MAE now {mae_now:.2f} vs RF {mae_rf:.2f})."
        )
    else:
        print(
            "The forest beat persistence "
            f"(MAE RF {mae_rf:.2f} vs now {mae_now:.2f})."
        )


def fit_full(ready):
    """Fit on every labeled hour, including the most recent complete ones."""
    if ready.empty:
        raise RuntimeError("No complete rows to train the 6-hour forecast.")
    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=1)
    model.fit(ready[FEATURES], ready["aqi_in_6h"])
    return model


def save_model(model, path=MODEL_PATH):
    payload = {
        "model": model,
        "features": list(FEATURES),
        "horizon_hours": HORIZON_HOURS,
    }
    joblib.dump(payload, path)
    return payload


def main():
    import live_aqi

    archive = load_history()
    history, info = live_aqi.build_merged_history(archive)
    print(
        f"Current reading: {info['current_aqi']} AQI at "
        f"{info['current_datetime']:%Y-%m-%d %H:%M} ({info['current_source']})"
    )
    print(info["detail"])
    print()
    hourly = hourly_frame(history[["datetime", "aqi"]])
    featured = add_features(hourly)
    ready = supervised_rows(featured)
    _model, cutoff, train, test, scores, accuracy = train_and_evaluate(ready)
    print_report(history, hourly, ready, cutoff, train, test, scores, accuracy)
    saved = fit_full(ready)
    save_model(saved)
    print(f"Saved {MODEL_PATH}")


if __name__ == "__main__":
    main()
