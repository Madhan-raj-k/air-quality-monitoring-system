# Air Quality Monitoring System

Station report for KSPCB Chikkaballapur Rural, Chikkaballapur (site_1557).

The current reading is the live CPCB station feed for KSPCB Chikkaballapur Rural. The last 24 hours come from the CPCB AQI repository when that month is published. Hours the repository does not have yet are taken from Open-Meteo at the station and converted to the CPCB AQI scale. The 6-hour figure is this system's forecast, trained on `history.csv` plus those latest hours. Category and health guidance come from fixed AQI bands, not from the forecast model. Hours nobody published stay missing and are never filled with 0.

## Layout

- `frontend/` — dashboard
- `backend/` — forecast training, report builder, and server
- `history.csv` — official hourly AQI archive, January 2026–August 2026. The dashboard adds the live hour on top of this file.

## Run

From this folder, in the VS Code terminal:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python backend\train_aqi_model.py
python backend\server.py
```

Open http://127.0.0.1:8000

Vercel runs `app.py` (FastAPI) from this folder. It serves the same dashboard and `/api/report`.

If PowerShell blocks activate, run `.venv\Scripts\Activate.ps1` instead.

Training prints MAE and RMSE for the forecast and for two baselines on the last 14 days: future = now, and future = same hour yesterday. On the 16–30 Aug 2026 window, future = now is the stronger baseline. That result is part of the training output.
