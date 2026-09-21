"""Vercel entrypoint for the dashboard and /api/report.

Local use stays `python backend/server.py`. Vercel loads the FastAPI `app`
in this file.
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import report  # noqa: E402

app = FastAPI()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/report")
def api_report():
    try:
        payload = report.build_report()
        status = 200
    except Exception as exc:
        payload = {"error": str(exc)}
        status = 500
    return JSONResponse(
        payload,
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


app.mount(
    "/",
    StaticFiles(directory=str(ROOT / "frontend"), html=True),
    name="dashboard",
)
