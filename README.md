# Digital Twin Smart Parking System

A real-time parking lot simulation and monitoring system built on top of a YOLOv8 inference pipeline, the CNR-EXT dataset, FastAPI, SQLite, and Streamlit.

---

## Project Structure

```
.
├── simulate_feed.py              # Dataset walker → POSTs images to backend
├── requirements.txt
├── README.md
├── backend/
│   ├── main.py                   # FastAPI app (all endpoints + WebSocket)
│   ├── database.py               # SQLite schema + query helpers
│   ├── inference_wrapper.py      # Subprocess wrapper around inference script
│   ├── inference_yolo_ultralytics.py   # (copy / symlink from your existing script)
│   └── all_black_mask.png        # Fallback mask (all-black = no masking)
├── dashboard/
│   └── app.py                    # Streamlit dashboard
└── dataset/
    └── CNR-EXT_FULL_IMAGE_1000x750/
        ├── FULL_IMAGE_1000x750/
        │   ├── SUNNY/
        │   ├── OVERCAST/
        │   └── RAINY/
        └── camera1.csv .. camera9.csv   # Slot position files
```

---

## Prerequisites

- Python 3.11+
- The CNR-EXT_FULL_IMAGE_1000x750 dataset in `dataset/`
- YOLOv8n model weights at `assets/models/yolov8n.pt`
- (Optional) Per-camera masks at `assets/masks/cnrpark_mask_camera{N}_1000_750_bw.png`
- Your existing `utils.py` in the **parent** directory of `backend/`
  (the inference script does `sys.path.append('..')` to find it)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Place required files

```
# Model weights
assets/models/yolov8n.pt

# Per-camera masks (optional — fallback all-black mask is used if missing)
assets/masks/cnrpark_mask_camera1_1000_750_bw.png
...
assets/masks/cnrpark_mask_camera9_1000_750_bw.png

# Copy (or symlink) the inference script into backend/
cp inference_yolo_ultralytics.py backend/

# utils.py must be one level above backend/
ls utils.py          # should exist at project root
```

### 3. Dataset symlink / copy

Place (or symlink) the dataset so the path resolves to:

```
dataset/CNR-EXT_FULL_IMAGE_1000x750/FULL_IMAGE_1000x750/SUNNY|OVERCAST|RAINY/...
```

Also place the `camera1.csv … camera9.csv` slot-position files at:

```
dataset/CNR-EXT_FULL_IMAGE_1000x750/camera1.csv
dataset/CNR-EXT_FULL_IMAGE_1000x750/camera2.csv
...
```

---

## Running the System

Open **three terminals** in the project root.

### Terminal 1 — Backend

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Verify it's running: http://localhost:8000/docs

---

### Terminal 2 — Simulator

```bash
# Default: all cameras, all weather, 3600× speed (30-min gaps → ~0.5 s)
python simulate_feed.py

# Only SUNNY, camera1 + camera2, slower pace
python simulate_feed.py --weather SUNNY --camera camera1 camera2 --speed 60

# Dry-run (lists images, no HTTP calls)
python simulate_feed.py --dry-run

# Custom dataset path or backend URL
python simulate_feed.py --dataset /path/to/FULL_IMAGE_1000x750 --url http://localhost:8000
```

---

### Terminal 3 — Dashboard

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501 in your browser.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/detect` | Upload image + metadata; runs inference, stores result |
| `GET` | `/status` | Current occupancy snapshot for all cameras |
| `GET` | `/cameras` | List cameras with slot counts + latest snapshot |
| `GET` | `/history?hours=24&camera=camera1&weather=SUNNY` | Time-series occupancy |
| `GET` | `/alerts` | Recent threshold alerts |
| `GET` | `/heatmap` | Avg occupancy grouped by weather × hour of day |
| `WS` | `/live` | WebSocket — pushes each detection result in real time |

### POST /detect body (multipart/form-data)

| Field | Type | Description |
|-------|------|-------------|
| `file` | File | JPEG/PNG image |
| `weather` | str | `SUNNY` \| `OVERCAST` \| `RAINY` |
| `date` | str | `2015-11-12` |
| `camera` | str | `camera1` … `camera9` |
| `timestamp` | str | `2015-11-12_0709` |

---

## Alert Thresholds

| Alert Type | Condition |
|------------|-----------|
| `lot_full` | occupancy ≥ 95% |
| `nearly_full` | 80% ≤ occupancy < 95% |
| `lot_empty` | occupancy ≤ 10% |

---

## Dashboard Features

- **Sidebar** — filter by weather, camera, history window
- **Summary metrics** — total/occupied/free slots, overall occupancy %
- **Alerts panel** — colour-coded threshold alerts
- **3×3 camera grid** — per-camera occupancy %, colour-coded progress bar
- **Spot grid** — individual parking-spot squares (green = free, red = occupied) for a selected camera
- **Line chart** — occupancy % over the selected time window, grouped by camera or weather
- **Heatmap** — average occupancy by hour of day, broken down by weather condition
- **Auto-refresh** — reruns every 5 seconds

---

## Notes & Troubleshooting

- The inference script is called as a **subprocess**; if `utils.py` is not importable from the project root the subprocess will fail. Keep `utils.py` at the project root.
- If no mask file exists for a camera the fallback `backend/all_black_mask.png` is used (fully black = whole image is unmasked in post-masking mode).
- SQLite database is written to `parking.db` in the working directory. Delete it to reset all data.
- Set `BACKEND_URL` environment variable to point the dashboard at a remote backend:
  ```bash
  BACKEND_URL=http://192.168.1.10:8000 streamlit run dashboard/app.py
  ```
