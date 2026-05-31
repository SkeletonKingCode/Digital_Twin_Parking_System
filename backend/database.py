"""
database.py — SQLite setup and query helpers for the Digital Twin Smart Parking System.
Tables: detection_events, alerts
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.environ.get("PARKING_DB", "parking.db")


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS detection_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT    NOT NULL,          -- e.g. '2015-11-12_0709'
    weather               TEXT    NOT NULL,          -- SUNNY | OVERCAST | RAINY
    date                  TEXT    NOT NULL,          -- e.g. '2015-11-12'
    camera                TEXT    NOT NULL,          -- e.g. 'camera1'
    image_name            TEXT    NOT NULL,
    predicted_cars        INTEGER,
    predicted_cars_parked INTEGER,
    total_slots           INTEGER,
    occupancy_pct         REAL,
    processing_time       REAL,
    inserted_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at  TEXT    NOT NULL,
    camera        TEXT    NOT NULL,
    alert_type    TEXT    NOT NULL,   -- 'lot_full' | 'nearly_full' | 'lot_empty'
    occupancy_pct REAL    NOT NULL
);
"""

ALERT_THRESHOLDS = {
    "lot_full":    lambda pct: pct >= 95,
    "nearly_full": lambda pct: 80 <= pct < 95,
    "lot_empty":   lambda pct: pct <= 0,
}


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH):
    conn = get_conn(db_path)
    conn.executescript(DDL)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_event(
    conn: sqlite3.Connection,
    *,
    timestamp: str,
    weather: str,
    date: str,
    camera: str,
    image_name: str,
    predicted_cars: int,
    predicted_cars_parked: int,
    total_slots: int,
    occupancy_pct: float,
    processing_time: float,
) -> int:
    inserted_at = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT INTO detection_events
            (timestamp, weather, date, camera, image_name,
             predicted_cars, predicted_cars_parked, total_slots,
             occupancy_pct, processing_time, inserted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (timestamp, weather, date, camera, image_name,
         predicted_cars, predicted_cars_parked, total_slots,
         occupancy_pct, processing_time, inserted_at),
    )
    conn.commit()
    return cur.lastrowid


def maybe_insert_alert(
    conn: sqlite3.Connection,
    camera: str,
    occupancy_pct: float,
) -> list[dict]:
    """Insert alerts for any triggered threshold; returns list of new alert dicts."""
    triggered = []
    triggered_at = datetime.utcnow().isoformat()
    for alert_type, fn in ALERT_THRESHOLDS.items():
        if fn(occupancy_pct):
            conn.execute(
                "INSERT INTO alerts (triggered_at, camera, alert_type, occupancy_pct) VALUES (?,?,?,?)",
                (triggered_at, camera, alert_type, occupancy_pct),
            )
            triggered.append({
                "triggered_at": triggered_at,
                "camera": camera,
                "alert_type": alert_type,
                "occupancy_pct": occupancy_pct,
            })
    conn.commit()
    return triggered


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_latest_per_camera(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM detection_events
        WHERE id IN (
            SELECT MAX(id) FROM detection_events GROUP BY camera
        )
        ORDER BY camera
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_history(
    conn: sqlite3.Connection,
    hours: int = 24,
    camera: Optional[str] = None,
    weather: Optional[str] = None,
) -> list[dict]:
    """
    Return time-series rows from detection_events.
    'hours' is interpreted against the dataset timestamps (not wall-clock time),
    so we simply return the last N hours worth of data per the timestamp column.
    When hours=0 return all rows.
    """
    filters = []
    params: list = []

    if camera:
        filters.append("camera = ?")
        params.append(camera)
    if weather:
        filters.append("weather = ?")
        params.append(weather.upper())

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = conn.execute(
        f"""
        SELECT timestamp, weather, date, camera,
               predicted_cars_parked, total_slots, occupancy_pct, inserted_at
        FROM detection_events
        {where}
        ORDER BY inserted_at DESC
        LIMIT 2000
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_alerts(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_occupancy_heatmap(conn: sqlite3.Connection) -> list[dict]:
    """Return avg occupancy grouped by (weather, hour_of_day)."""
    rows = conn.execute(
        """
        SELECT weather,
               CAST(SUBSTR(timestamp, 12, 2) AS INTEGER) AS hour,
               AVG(occupancy_pct)                         AS avg_occ
        FROM detection_events
        GROUP BY weather, hour
        ORDER BY weather, hour
        """
    ).fetchall()
    return [dict(r) for r in rows]
