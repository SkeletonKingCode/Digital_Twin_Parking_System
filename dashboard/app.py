"""
dashboard/app.py
Refactored version: no artifacts, clean state handling, same UI and behavior.
Updated: use_container_width → width='stretch' / width='content'
"""

import os
import time
import math
import requests
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

WEATHER_OPTIONS = ["ALL", "SUNNY", "OVERCAST", "RAINY"]
CAMERA_OPTIONS = [f"camera{i}" for i in range(1, 10)]

CLR_FREE = "#22c55e"
CLR_MEDIUM = "#f59e0b"
CLR_BUSY = "#ef4444"
CLR_UNKNOWN = "#6b7280"

ALERT_ICONS = {
    "lot_full": "🔴",
    "nearly_full": "🟡",
    "lot_empty": "🟢",
}
ALERT_LABELS = {
    "lot_full": "Lot Full (≥95%)",
    "nearly_full": "Nearly Full (≥80%)",
    "lot_empty": "Lot Empty (≤10%)",
}

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Smart Parking · Digital Twin",
    page_icon="🅿️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    html, body, [data-testid="stAppViewContainer"] {
        background: #0f172a;
        color: #e2e8f0;
        font-family: 'IBM Plex Mono', 'Courier New', monospace;
    }
    [data-testid="stSidebar"] {
        background: #1e293b !important;
        border-right: 1px solid #334155;
    }
    [data-testid="stMetric"] {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 12px 16px;
    }
    .section-header {
        font-size: 0.65rem;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 0.5rem;
        margin-top: 1.5rem;
    }
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
    .cam-pct { font-size: 1.6rem; font-weight: 700; line-height: 1.1; }
    .spot-grid { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    .spot { width: 14px; height: 14px; border-radius: 3px; }
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
    .meta-pill {
        display: inline-block;
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.72rem;
        color: #94a3b8;
        margin-right: 6px;
        margin-bottom: 4px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def fetch_json(path: str, params: dict | None = None) -> dict:
    """Fetch JSON from backend, no caching."""
    try:
        r = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=5)
        return r.json() if r.ok else {}
    except Exception:
        return {}

def fetch_bytes(path: str) -> bytes | None:
    """Fetch binary data (image) with cache buster."""
    try:
        url = f"{BACKEND_URL}{path}"
        sep = "&" if "?" in url else "?"
        url += f"{sep}_={int(time.time())}"
        r = requests.get(url, timeout=5)
        return r.content if r.ok else None
    except Exception:
        return None

def occ_color(pct: float | None) -> str:
    if pct is None:
        return CLR_UNKNOWN
    if pct >= 80:
        return CLR_BUSY
    if pct >= 50:
        return CLR_MEDIUM
    return CLR_FREE

def placeholder_box(msg: str = "No image yet") -> None:
    st.markdown(
        f"<div style='background:#1e293b;border:1px solid #334155;border-radius:8px;"
        f"padding:40px;text-align:center;color:#475569;font-size:0.8rem'>{msg}</div>",
        unsafe_allow_html=True,
    )

def clear_cache_and_rerun():
    """Clear Streamlit caches and rerun (preserves session state)."""
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "weather" not in st.session_state:
    st.session_state.weather = "ALL"
if "camera" not in st.session_state:
    st.session_state.camera = "ALL"
if "hours" not in st.session_state:
    st.session_state.hours = 24

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

    refresh_rate = st.slider(
        "Refresh rate (seconds)", min_value=1, max_value=60,
        value=10, step=1,
    )

    st.markdown(
        f"<span style='font-size:0.7rem;color:#475569'>Refresh every {refresh_rate}s</span>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<span style='font-size:0.7rem;color:#475569'>Backend: {BACKEND_URL}</span>",
        unsafe_allow_html=True,
    )

    if st.button("⟳ Force refresh"):
        st.rerun()


# ---------------------------------------------------------------------------
# Data fetching (fresh each cycle)
# ---------------------------------------------------------------------------
cameras_meta = fetch_json("/cameras").get("cameras", [])
heatmap_data = fetch_json("/heatmap").get("data", [])

hist_params = {"hours": sel_hours}
if sel_camera != "ALL":
    hist_params["camera"] = sel_camera
if sel_weather != "ALL":
    hist_params["weather"] = sel_weather
history_rows = fetch_json("/history", hist_params).get("rows", [])

latest_meta = fetch_json("/latest")  # may be empty

# Build cam_latest dict for easy access
cam_latest: dict[str, dict] = {}
for cm in cameras_meta:
    snap = cm.get("latest") or {}
    cam_latest[cm["camera"]] = {
        "total_slots": cm.get("total_slots", 36),
        "predicted_cars_parked": snap.get("predicted_cars_parked"),
        "occupancy_pct": snap.get("occupancy_pct"),
        "timestamp": snap.get("timestamp", "—"),
        "weather": snap.get("weather", "—"),
    }

# ---------------------------------------------------------------------------
# Header and summary metrics
# ---------------------------------------------------------------------------
st.markdown("## 🅿 Digital Twin Smart Parking")
st.markdown(
    f"<span style='color:#475569;font-size:0.8rem'>"
    f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    f"</span>",
    unsafe_allow_html=True,
)

total_slots_all = sum(v["total_slots"] for v in cam_latest.values())
occupied_all = sum(
    v["predicted_cars_parked"] for v in cam_latest.values()
    if v["predicted_cars_parked"] is not None
)
free_all = total_slots_all - occupied_all
overall_pct = round(occupied_all / total_slots_all * 100, 1) if total_slots_all else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Slots", f"{total_slots_all}")
c2.metric("Occupied", f"{occupied_all}")
c3.metric("Free", f"{free_all}")
c4.metric("Overall Occ %", f"{overall_pct}%")

# ---------------------------------------------------------------------------
# Camera overview (only when "ALL" is selected)
# ---------------------------------------------------------------------------
if sel_camera == "ALL":
    st.markdown('<p class="section-header">Camera Overview</p>', unsafe_allow_html=True)
    cam_keys = sorted(cam_latest.keys())
    for r in range(math.ceil(len(cam_keys) / 3)):
        cols = st.columns(3)
        for c in range(3):
            idx = r * 3 + c
            if idx >= len(cam_keys):
                break
            cam_id = cam_keys[idx]
            info = cam_latest[cam_id]
            pct = info["occupancy_pct"]
            occupied = info["predicted_cars_parked"]
            total = info["total_slots"]
            color = occ_color(pct)
            pct_lbl = f"{pct:.1f}%" if pct is not None else "—"

            with cols[c]:
                st.markdown(f"""
                <div class="cam-card">
                  <div class="cam-title">{cam_id.replace('camera','Camera ')}</div>
                  <div class="cam-pct" style="color:{color}">{pct_lbl}</div>
                  <div style="font-size:0.72rem;color:#64748b;margin:2px 0 6px">
                    {occupied if occupied is not None else '?'} / {total} spots
                  </div>
                  <div style="background:#1e293b;border:1px solid #334155;border-radius:4px;overflow:hidden;height:6px;">
                    <div style="background:{color};width:{int(pct or 0)}%;height:100%;transition:width .4s;"></div>
                  </div>
                  <div style="font-size:0.65rem;color:#475569;margin-top:6px">
                    {info['timestamp']} · {info['weather']}
                  </div>
                </div>
                """, unsafe_allow_html=True)
    st.divider()

# ---------------------------------------------------------------------------
# Live Camera Feed
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Live Camera Feed</p>', unsafe_allow_html=True)

if sel_camera == "ALL":
    cams_with_data = sorted(k for k, v in cam_latest.items() if v.get("occupancy_pct") is not None)
    if not cams_with_data:
        st.info("No snapshots yet — start the simulator.")
    else:
        for row_start in range(0, len(cams_with_data), 3):
            row_cams = cams_with_data[row_start:row_start + 3]
            img_cols = st.columns(3)
            for col_idx, cam_id in enumerate(row_cams):
                info = cam_latest.get(cam_id, {})
                pct = info.get("occupancy_pct")
                color = occ_color(pct)
                occ = info.get("predicted_cars_parked", "?")
                total = info.get("total_slots", "?")
                ts = info.get("timestamp", "—")
                wx = info.get("weather", "—")

                with img_cols[col_idx]:
                    img_bytes = fetch_bytes(f"/snapshot/{cam_id}")
                    if img_bytes:
                        st.image(img_bytes, width='stretch')
                    else:
                        placeholder_box("No image yet")

                    pct_str = f"{pct:.1f}%" if pct is not None else "—"
                    st.markdown(
                        f"<div style='margin-top:4px'>"
                        f"<span class='meta-pill'>{cam_id}</span>"
                        f"<span class='meta-pill' style='color:{color}'>{pct_str}</span>"
                        f"<span class='meta-pill'>{occ}/{total} spots</span>"
                        f"<span class='meta-pill'>{wx}</span>"
                        f"<span class='meta-pill'>{ts}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
else:
    # Single camera view
    img_bytes = fetch_bytes(f"/snapshot/{sel_camera}")
    info = cam_latest.get(sel_camera, {})
    occupied = info.get("predicted_cars_parked", 0) or 0
    total = info.get("total_slots", 36)
    free = total - occupied
    pct = info.get("occupancy_pct")
    color = occ_color(pct)

    img_col, grid_col = st.columns([3, 2])

    with img_col:
        if img_bytes:
            st.image(
                img_bytes,
                caption=(
                    f"{sel_camera.replace('camera','Camera ')} · "
                    f"{info.get('timestamp','—')} · "
                    f"{info.get('weather','—')}"
                ),
                width='stretch',
            )
        else:
            placeholder_box("No snapshot yet for this camera")

    with grid_col:
        st.markdown(
            f'<p class="section-header">Spot Grid — {sel_camera}</p>',
            unsafe_allow_html=True,
        )
        spots_html = '<div class="spot-grid">'
        for i in range(total):
            spot_color = CLR_BUSY if i < occupied else CLR_FREE
            spots_html += f'<div class="spot" style="background:{spot_color}" title="Spot {i+1}"></div>'
        spots_html += "</div>"
        st.markdown(f"""
        <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;">
          <div style="display:flex;gap:20px;margin-bottom:12px;">
            <span style="font-size:0.8rem;color:{CLR_FREE}">● Free: {free}</span>
            <span style="font-size:0.8rem;color:{CLR_BUSY}">● Occupied: {occupied}</span>
          </div>
          {spots_html}
        </div>
        """, unsafe_allow_html=True)

    # Metadata table for single camera
    st.markdown(
        f'<p class="section-header">Camera Metadata — {sel_camera.replace("camera","Camera ")}</p>',
        unsafe_allow_html=True,
    )
    l_ts = info.get("timestamp", "—")
    l_wx = info.get("weather", "—")
    l_park = info.get("predicted_cars_parked", "?")
    l_total = info.get("total_slots", "?")
    l_free = (l_total - l_park) if isinstance(l_park, int) and isinstance(l_total, int) else "?"
    pct_str = f"{pct:.1f}%" if pct is not None else "—"

    meta_rows = [
        ("Camera", sel_camera.replace("camera", "Camera ")),
        ("Timestamp", l_ts),
        ("Weather", l_wx),
        ("Cars parked", f"{l_park} / {l_total} slots"),
        ("Free spots", str(l_free)),
        ("Occupancy", pct_str),
    ]
    table_html = "<table style='width:100%;border-collapse:collapse;font-size:0.8rem'>"
    for label, value in meta_rows:
        table_html += f"<tr><td style='color:#64748b;padding:5px 12px 5px 0;white-space:nowrap'>{label}</td><td style='color:#e2e8f0;padding:5px 0'>{value}</td></tr>"
    table_html += "</table>"

    meta_col, bar_col = st.columns([1, 1])
    with meta_col:
        st.markdown(table_html, unsafe_allow_html=True)
    with bar_col:
        st.markdown(
            f"<div style='margin-top:8px'>"
            f"<div style='font-size:0.65rem;color:#475569;margin-bottom:6px'>Occupancy</div>"
            f"<div style='background:#0f172a;border:1px solid #334155;border-radius:4px;overflow:hidden;height:8px'>"
            f"<div style='background:{color};width:{int(pct or 0)}%;height:100%'></div>"
            f"</div>"
            f"<div style='font-size:1.4rem;font-weight:700;color:{color};margin-top:8px'>{pct_str}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.divider()

# ---------------------------------------------------------------------------
# Latest Processed Image (only when "ALL" is selected)
# ---------------------------------------------------------------------------
if sel_camera == "ALL":
    st.markdown('<p class="section-header">Latest Processed Image</p>', unsafe_allow_html=True)

    latest_img = fetch_bytes("/latest/image")

    if latest_img and latest_meta:
        lm = latest_meta
        l_cam = lm.get("camera", "—")
        l_ts = lm.get("timestamp", "—")
        l_wx = lm.get("weather", "—")
        l_occ = lm.get("occupancy_pct", 0)
        l_park = lm.get("predicted_cars_parked", "?")
        l_total = lm.get("total_slots", "?")
        l_cars = lm.get("predicted_cars", "?")
        l_proc = lm.get("processing_time")
        l_color = occ_color(l_occ)

        left, right = st.columns([2, 3])

        with left:
            st.image(latest_img, width='stretch')

        with right:
            st.markdown(
                f"<div style='font-size:1.1rem;font-weight:700;color:#e2e8f0;margin-bottom:12px'>"
                f"{l_cam.replace('camera','Camera ')} &nbsp;·&nbsp; "
                f"<span style='color:{l_color}'>{l_occ:.1f}%</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            rows = [
                ("Timestamp", l_ts),
                ("Weather", l_wx),
                ("Cars in parking", f"{l_park} / {l_total} slots"),
                ("Total cars seen", str(l_cars)),
                ("Processing time", f"{l_proc:.2f}s" if isinstance(l_proc, float) else "—"),
            ]
            table_html = "<table style='width:100%;border-collapse:collapse;font-size:0.8rem'>"
            for label, value in rows:
                table_html += f"<tr><td style='color:#64748b;padding:5px 12px 5px 0;white-space:nowrap'>{label}</td><td style='color:#e2e8f0;padding:5px 0'>{value}</td></tr>"
            table_html += "</table>"
            st.markdown(table_html, unsafe_allow_html=True)

            st.markdown(
                f"<div style='margin-top:14px'>"
                f"<div style='font-size:0.65rem;color:#475569;margin-bottom:4px'>Occupancy</div>"
                f"<div style='background:#0f172a;border:1px solid #334155;border-radius:4px;overflow:hidden;height:8px'>"
                f"<div style='background:{l_color};width:{int(l_occ)}%;height:100%'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

            alerts = lm.get("alerts", [])
            if alerts:
                st.markdown("<div style='margin-top:12px'>", unsafe_allow_html=True)
                for a in alerts:
                    icon = ALERT_ICONS.get(a["alert_type"], "⚪")
                    label = ALERT_LABELS.get(a["alert_type"], a["alert_type"])
                    st.markdown(
                        f'<span class="alert-badge alert-{a["alert_type"]}">'
                        f'{icon} {label}</span>',
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)

    else:
        placeholder_box("No detections yet — start the simulator")

    st.divider()

# ---------------------------------------------------------------------------
# Occupancy line chart
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Occupancy Over Time</p>', unsafe_allow_html=True)

if history_rows:
    df_hist = pd.DataFrame(history_rows)
    df_hist["inserted_at"] = pd.to_datetime(df_hist["inserted_at"], errors="coerce")
    df_hist["occupancy_pct"] = pd.to_numeric(df_hist["occupancy_pct"], errors="coerce")
    df_hist = df_hist.dropna(subset=["inserted_at"]).sort_values("inserted_at")

    group_col = "camera" if sel_camera == "ALL" else "weather"
    fig = px.line(
        df_hist, x="inserted_at", y="occupancy_pct", color=group_col,
        labels={
            "inserted_at": "Time",
            "occupancy_pct": "Occupancy %",
            group_col: group_col.title(),
        },
        template="plotly_dark",
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Mono",
        yaxis=dict(range=[0, 105], gridcolor="#1e293b"),
        xaxis=dict(gridcolor="#1e293b"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, width='stretch')
else:
    st.info("No history data yet.")

# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------
st.markdown(
    '<p class="section-header">Heatmap · Avg Occupancy by Hour of Day</p>',
    unsafe_allow_html=True,
)

if heatmap_data:
    df_hm = pd.DataFrame(heatmap_data)
    df_hm.columns = ["weather", "hour", "avg_occ"]
    df_pivot = df_hm.pivot(index="weather", columns="hour", values="avg_occ").fillna(0)
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
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Mono", font_color="#e2e8f0",
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(tickfont=dict(size=10)),
        yaxis=dict(tickfont=dict(size=10)),
    )
    st.plotly_chart(fig_hm, width='stretch')
else:
    st.info("Heatmap data will appear once enough detections are recorded.")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
time.sleep(refresh_rate)
st.rerun()