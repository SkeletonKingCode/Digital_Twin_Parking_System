"""
dashboard/app.py
----------------
Streamlit dashboard for the Digital Twin Smart Parking System.

Run with:
    streamlit run dashboard/app.py
"""

import os
import sys
import time
import math
import requests
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "5"))   # seconds

WEATHER_OPTIONS  = ["ALL", "SUNNY", "OVERCAST", "RAINY"]
CAMERA_OPTIONS   = [f"camera{i}" for i in range(1, 10)]

# Colour palette
CLR_FREE     = "#22c55e"   # green
CLR_MEDIUM   = "#f59e0b"   # amber
CLR_BUSY     = "#ef4444"   # red
CLR_UNKNOWN  = "#6b7280"   # grey
CLR_BG       = "#0f172a"   # slate-900
CLR_CARD     = "#1e293b"   # slate-800
CLR_BORDER   = "#334155"   # slate-700

ALERT_ICONS = {
    "lot_full":    "🔴",
    "nearly_full": "🟡",
    "lot_empty":   "🟢",
}
ALERT_LABELS = {
    "lot_full":    "Lot Full (≥95%)",
    "nearly_full": "Nearly Full (≥80%)",
    "lot_empty":   "Lot Empty (≤10%)",
}


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Smart Parking · Digital Twin",
    page_icon="🅿️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* ── Global ── */
    html, body, [data-testid="stAppViewContainer"] {
        background: #0f172a;
        color: #e2e8f0;
        font-family: 'IBM Plex Mono', 'Courier New', monospace;
    }
    [data-testid="stSidebar"] {
        background: #1e293b !important;
        border-right: 1px solid #334155;
    }
    /* ── Metric cards ── */
    [data-testid="stMetric"] {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 12px 16px;
    }
    /* ── Section headers ── */
    .section-header {
        font-size: 0.65rem;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 0.5rem;
        margin-top: 1.5rem;
    }
    /* ── Camera card ── */
    .cam-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 14px 16px;
        margin-bottom: 10px;
    }
    .cam-title {
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #94a3b8;
        margin-bottom: 6px;
    }
    .cam-pct {
        font-size: 1.6rem;
        font-weight: 700;
        line-height: 1.1;
    }
    /* ── Spot grid ── */
    .spot-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        margin-top: 6px;
    }
    .spot {
        width: 14px;
        height: 14px;
        border-radius: 3px;
    }
    /* ── Alert badge ── */
    .alert-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        margin: 3px 0;
    }
    .alert-lot_full    { background: #7f1d1d; color: #fca5a5; }
    .alert-nearly_full { background: #78350f; color: #fcd34d; }
    .alert-lot_empty   { background: #14532d; color: #86efac; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch_status():
    try:
        r = requests.get(f"{BACKEND_URL}/status", timeout=5)
        return r.json() if r.ok else {}
    except Exception:
        return {}


@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch_cameras():
    try:
        r = requests.get(f"{BACKEND_URL}/cameras", timeout=5)
        return r.json().get("cameras", []) if r.ok else []
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch_history(hours: int, camera: str | None, weather: str | None):
    params = {"hours": hours}
    if camera and camera != "ALL":
        params["camera"] = camera
    if weather and weather != "ALL":
        params["weather"] = weather
    try:
        r = requests.get(f"{BACKEND_URL}/history", params=params, timeout=5)
        return r.json().get("rows", []) if r.ok else []
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch_alerts():
    try:
        r = requests.get(f"{BACKEND_URL}/alerts", timeout=5)
        return r.json().get("alerts", []) if r.ok else []
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch_heatmap():
    try:
        r = requests.get(f"{BACKEND_URL}/heatmap", timeout=5)
        return r.json().get("data", []) if r.ok else []
    except Exception:
        return []


def occ_color(pct: float | None) -> str:
    if pct is None:
        return CLR_UNKNOWN
    if pct >= 80:
        return CLR_BUSY
    if pct >= 50:
        return CLR_MEDIUM
    return CLR_FREE


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🅿 Smart Parking")
    st.markdown("*Digital Twin Dashboard*")
    st.divider()

    sel_weather = st.selectbox("Weather condition", WEATHER_OPTIONS, index=0)
    sel_camera  = st.selectbox("Camera", ["ALL"] + CAMERA_OPTIONS, index=0)
    sel_hours   = st.slider("History window (hours)", 1, 168, 24)

    st.divider()
    st.markdown(f"<span style='font-size:0.7rem;color:#475569'>Refresh every {REFRESH_INTERVAL}s</span>", unsafe_allow_html=True)
    st.markdown(f"<span style='font-size:0.7rem;color:#475569'>Backend: {BACKEND_URL}</span>", unsafe_allow_html=True)

    if st.button("⟳ Force refresh"):
        st.cache_data.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
cameras_meta = fetch_cameras()
status_data  = fetch_status()
history_rows = fetch_history(
    sel_hours,
    None if sel_camera == "ALL" else sel_camera,
    None if sel_weather == "ALL" else sel_weather,
)
alerts_data  = fetch_alerts()
heatmap_data = fetch_heatmap()

# Build camera → latest snapshot dict
cam_latest: dict[str, dict] = {}
for cm in cameras_meta:
    snap = cm.get("latest") or {}
    cam_latest[cm["camera"]] = {
        "total_slots":            cm.get("total_slots", 36),
        "predicted_cars_parked": snap.get("predicted_cars_parked", None),
        "occupancy_pct":          snap.get("occupancy_pct", None),
        "timestamp":              snap.get("timestamp", "—"),
        "weather":                snap.get("weather", "—"),
    }


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("## 🅿 Digital Twin Smart Parking")
st.markdown(
    f"<span style='color:#475569;font-size:0.8rem'>"
    f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    f"</span>",
    unsafe_allow_html=True,
)

# Summary metrics
total_slots_all = sum(v["total_slots"] for v in cam_latest.values())
occupied_all    = sum(
    v["predicted_cars_parked"] for v in cam_latest.values()
    if v["predicted_cars_parked"] is not None
)
free_all        = total_slots_all - occupied_all
overall_pct     = round(occupied_all / total_slots_all * 100, 1) if total_slots_all else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Slots",    f"{total_slots_all}")
c2.metric("Occupied",       f"{occupied_all}")
c3.metric("Free",           f"{free_all}")
c4.metric("Overall Occ %",  f"{overall_pct}%")


# ---------------------------------------------------------------------------
# Alerts panel
# ---------------------------------------------------------------------------
if alerts_data:
    st.markdown('<p class="section-header">⚠ Active Alerts</p>', unsafe_allow_html=True)
    for a in alerts_data[:10]:
        icon  = ALERT_ICONS.get(a["alert_type"], "⚪")
        label = ALERT_LABELS.get(a["alert_type"], a["alert_type"])
        st.markdown(
            f'<span class="alert-badge alert-{a["alert_type"]}">'
            f'{icon} {a["camera"]}  ·  {label}  ·  {a["occupancy_pct"]:.1f}%  '
            f'<span style="opacity:.6">{a["triggered_at"][:16]}</span>'
            f'</span>',
            unsafe_allow_html=True,
        )

st.divider()


# ---------------------------------------------------------------------------
# 3×3 Camera Grid
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Camera Overview</p>', unsafe_allow_html=True)

cam_keys = sorted(cam_latest.keys())
cols_per_row = 3
rows = math.ceil(len(cam_keys) / cols_per_row)

for r in range(rows):
    cols = st.columns(cols_per_row)
    for c in range(cols_per_row):
        idx = r * cols_per_row + c
        if idx >= len(cam_keys):
            break
        cam_id   = cam_keys[idx]
        info     = cam_latest[cam_id]
        pct      = info["occupancy_pct"]
        occupied = info["predicted_cars_parked"]
        total    = info["total_slots"]
        color    = occ_color(pct)

        with cols[c]:
            bar_width = int(pct or 0)
            pct_label = f"{pct:.1f}%" if pct is not None else "—"

            st.markdown(f"""
            <div class="cam-card">
              <div class="cam-title">{cam_id.replace('camera','Camera ')}</div>
              <div class="cam-pct" style="color:{color}">{pct_label}</div>
              <div style="font-size:0.72rem;color:#64748b;margin:2px 0 6px">
                {occupied if occupied is not None else '?'} / {total} spots
              </div>
              <div style="background:#1e293b;border:1px solid #334155;border-radius:4px;overflow:hidden;height:6px;">
                <div style="background:{color};width:{bar_width}%;height:100%;transition:width .4s;"></div>
              </div>
              <div style="font-size:0.65rem;color:#475569;margin-top:6px">
                {info['timestamp']} · {info['weather']}
              </div>
            </div>
            """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Spot Grid for selected camera
# ---------------------------------------------------------------------------
if sel_camera != "ALL":
    st.markdown(f'<p class="section-header">Spot Grid — {sel_camera}</p>', unsafe_allow_html=True)
    info     = cam_latest.get(sel_camera, {})
    occupied = info.get("predicted_cars_parked", 0) or 0
    total    = info.get("total_slots", 36)
    free     = total - occupied

    spots_html = '<div class="spot-grid">'
    for i in range(total):
        color = CLR_BUSY if i < occupied else CLR_FREE
        spots_html += f'<div class="spot" style="background:{color}" title="Spot {i+1}"></div>'
    spots_html += "</div>"

    st.markdown(f"""
    <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;">
      <div style="display:flex;gap:20px;margin-bottom:12px;">
        <span style="font-size:0.8rem;color:#22c55e">● Free: {free}</span>
        <span style="font-size:0.8rem;color:#ef4444">● Occupied: {occupied}</span>
      </div>
      {spots_html}
    </div>
    """, unsafe_allow_html=True)

st.divider()


# ---------------------------------------------------------------------------
# Occupancy line chart
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Occupancy Over Time</p>', unsafe_allow_html=True)

if history_rows:
    df_hist = pd.DataFrame(history_rows)
    df_hist["inserted_at"] = pd.to_datetime(df_hist["inserted_at"], errors="coerce")
    df_hist = df_hist.dropna(subset=["inserted_at"]).sort_values("inserted_at")
    df_hist["occupancy_pct"] = pd.to_numeric(df_hist["occupancy_pct"], errors="coerce")

    group_col = "camera" if sel_camera == "ALL" else "weather"
    fig = px.line(
        df_hist,
        x="inserted_at",
        y="occupancy_pct",
        color=group_col,
        labels={"inserted_at": "Time", "occupancy_pct": "Occupancy %", group_col: group_col.title()},
        template="plotly_dark",
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Mono",
        yaxis=dict(range=[0, 105], gridcolor="#1e293b"),
        xaxis=dict(gridcolor="#1e293b"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No history data yet. Start the simulator to populate the database.")


# ---------------------------------------------------------------------------
# Heatmap — avg occupancy by hour × weather
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Heatmap · Avg Occupancy by Hour of Day</p>', unsafe_allow_html=True)

if heatmap_data:
    df_hm = pd.DataFrame(heatmap_data)
    df_hm.columns = ["weather", "hour", "avg_occ"]
    df_pivot = df_hm.pivot(index="weather", columns="hour", values="avg_occ").fillna(0)

    # Ensure all hours 0-23 exist
    for h in range(24):
        if h not in df_pivot.columns:
            df_pivot[h] = 0
    df_pivot = df_pivot[sorted(df_pivot.columns)]

    fig_hm = go.Figure(data=go.Heatmap(
        z=df_pivot.values,
        x=[f"{h:02d}:00" for h in df_pivot.columns],
        y=df_pivot.index.tolist(),
        colorscale=[[0, "#14532d"], [0.5, "#f59e0b"], [1, "#7f1d1d"]],
        zmin=0, zmax=100,
        hovertemplate="Hour: %{x}<br>Weather: %{y}<br>Avg Occ: %{z:.1f}%<extra></extra>",
    ))
    fig_hm.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Mono",
        font_color="#e2e8f0",
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(tickfont=dict(size=10)),
        yaxis=dict(tickfont=dict(size=10)),
    )
    st.plotly_chart(fig_hm, use_container_width=True)
else:
    st.info("Heatmap data will appear once enough detections have been recorded.")


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
time.sleep(REFRESH_INTERVAL)
st.rerun()
