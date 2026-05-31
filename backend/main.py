"""
backend/main.py
---------------
FastAPI backend for the Digital Twin Smart Parking System.

Endpoints
---------
POST   /detect                 — run inference on an uploaded image
GET    /status                 — current occupancy per camera
GET    /history                — time-series data (query: hours, camera, weather)
GET    /cameras                — list cameras with latest snapshot
GET    /alerts                 — triggered threshold alerts
WS     /live                   — WebSocket push of real-time updates

Run with:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""


import os
import io
import csv
import shutil
import tempfile
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import (
    init_db, get_conn,
    insert_event, maybe_insert_alert,
    get_latest_per_camera, get_history,
    get_alerts, get_occupancy_heatmap,
    DB_PATH,
)
from backend.inference_wrapper import run_inference

# ---------------------------------------------------------------------------
# Slot counts per camera (derived from the dataset CSV files)
# ---------------------------------------------------------------------------
# If the camera<N>.csv files are present we load them; otherwise fall back to
# a hard-coded estimate so the server works even without the full dataset.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DATASET_DIR = os.path.join(_ROOT, "dataset", "CNR-EXT_FULL_IMAGE_1000x750")

SLOT_COUNTS: dict[str, int] = {}

def _load_slot_counts():
    for i in range(1, 10):
        cam = f"camera{i}"
        csv_path = os.path.join(DATASET_DIR, f"{cam}.csv")
        if os.path.exists(csv_path):
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                SLOT_COUNTS[cam] = sum(1 for _ in reader)
        else:
            SLOT_COUNTS[cam] = 36

_load_slot_counts()

# ---------------------------------------------------------------------------
# In-memory snapshot store: camera -> raw JPEG bytes
# ---------------------------------------------------------------------------
_snapshots: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)
    yield

app = FastAPI(title="Digital Twin Smart Parking", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# POST /detect
# ---------------------------------------------------------------------------
@app.post("/detect")
async def detect(
    file:      UploadFile = File(...),
    weather:   str = Form("UNKNOWN"),
    date:      str = Form(""),
    camera:    str = Form("camera1"),
    timestamp: str = Form(""),
):
    """
    Accept an image + metadata, run YOLOv8 inference, store result, return spot states.
    """
    # Save upload to a temp file
    suffix = os.path.splitext(file.filename)[-1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        # Store raw bytes as the latest snapshot for this camera
        _snapshots[camera] = contents

        metrics = run_inference(tmp.name, camera, original_filename=file.filename)
    finally:
        os.unlink(tmp.name)

    # Derive values
    predicted_parking = metrics["predicted_cars_parked"]
    total_slots = SLOT_COUNTS.get(camera, 36)
    occupancy_pct = round((predicted_parking / total_slots) * 100, 1) if total_slots else 0.0

    ts = timestamp or metrics.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    dt = date or (ts[:10] if len(ts) >= 10 else "")

    conn = get_conn()
    event_id = insert_event(
        conn,
        timestamp=ts,
        weather=weather.upper(),
        date=dt,
        camera=camera,
        image_name=file.filename,
        predicted_cars=metrics["predicted_cars"],
        predicted_cars_parked=predicted_parking,
        total_slots=total_slots,
        occupancy_pct=occupancy_pct,
        processing_time=metrics["processing_time"],
    )
    new_alerts = maybe_insert_alert(conn, camera, occupancy_pct)
    conn.close()

    payload = {
        "event_id":               event_id,
        "camera":                 camera,
        "timestamp":              ts,
        "weather":                weather.upper(),
        "predicted_cars":         metrics["predicted_cars"],
        "predicted_cars_parked": predicted_parking,
        "total_slots":            total_slots,
        "occupancy_pct":          occupancy_pct,
        "processing_time":        metrics["processing_time"],
        "alerts":                 new_alerts,
    }

    # Push to WebSocket clients
    await manager.broadcast(payload)
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# GET /snapshot/{camera}  — latest raw image for a camera
# ---------------------------------------------------------------------------
@app.get("/snapshot/{camera}")
def snapshot(camera: str):
    data = _snapshots.get(camera)
    if data is None:
        raise HTTPException(status_code=404, detail="No snapshot yet for this camera")
    return Response(content=data, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# GET /snapshots  — which cameras have a snapshot available
# ---------------------------------------------------------------------------
@app.get("/snapshots")
def snapshots_index():
    return {"cameras": list(_snapshots.keys())}


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------
@app.get("/status")
def status():
    """Return current occupancy across all active cameras."""
    conn = get_conn()
    rows = get_latest_per_camera(conn)
    conn.close()
    return {"cameras": rows, "retrieved_at": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------
@app.get("/history")
def history(
    hours:   int           = 24,
    camera:  Optional[str] = None,
    weather: Optional[str] = None,
):
    conn = get_conn()
    rows = get_history(conn, hours=hours, camera=camera, weather=weather)
    conn.close()
    return {"rows": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# GET /cameras
# ---------------------------------------------------------------------------
@app.get("/cameras")
def cameras():
    """List all camera IDs with their latest occupancy snapshot."""
    conn = get_conn()
    latest = get_latest_per_camera(conn)
    conn.close()
    result = []
    for cam_id, slots in SLOT_COUNTS.items():
        snap = next((r for r in latest if r["camera"] == cam_id), None)
        result.append({
            "camera":      cam_id,
            "total_slots": slots,
            "latest":      snap,
        })
    return {"cameras": result}


# ---------------------------------------------------------------------------
# GET /alerts
# ---------------------------------------------------------------------------
@app.get("/alerts")
def alerts(limit: int = 50):
    conn = get_conn()
    rows = get_alerts(conn, limit=limit)
    conn.close()
    return {"alerts": rows}


# ---------------------------------------------------------------------------
# GET /heatmap
# ---------------------------------------------------------------------------
@app.get("/heatmap")
def heatmap():
    conn = get_conn()
    rows = get_occupancy_heatmap(conn)
    conn.close()
    return {"data": rows}


# ---------------------------------------------------------------------------
# WebSocket /live
# ---------------------------------------------------------------------------
@app.websocket("/live")
async def websocket_live(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # Keep connection alive; actual data is pushed by /detect
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)