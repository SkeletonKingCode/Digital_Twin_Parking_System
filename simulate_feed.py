"""
simulate_feed.py
----------------
Walks the CNR-EXT_FULL_IMAGE_1000x750 dataset in chronological order and
POSTs each image to the FastAPI backend's /detect endpoint, simulating a
live parking-lot camera feed.

Usage examples
--------------
# Real-time (1x speed)
python simulate_feed.py

# 10x faster
python simulate_feed.py --speed 10

# Only SUNNY images from camera1 and camera3
python simulate_feed.py --weather SUNNY --camera camera1 camera3

# Point at a non-default backend
python simulate_feed.py --url http://localhost:8000
"""

import os
import re
import sys
import time
import argparse
import requests
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DATASET_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "dataset",
    "CNR-EXT_FULL_IMAGE_1000x750",
    "FULL_IMAGE_1000x750",
)
BACKEND_URL = "http://localhost:8000"

# CNR-EXT images span a single day each; timestamps are HHmm so the "real"
# inter-frame gap is typically 30 minutes (1800 s).
REAL_INTERVAL_SECONDS = 1800


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{4})")


def parse_timestamp(filename: str) -> tuple[str, str]:
    """Return (date_str, hhmm) from a filename like 2015-11-12_0709.jpg."""
    m = TS_RE.search(filename)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def collect_images(
    dataset_root: str,
    weather_filter: list[str] | None,
    camera_filter: list[str] | None,
) -> list[dict]:
    """
    Walk dataset_root and collect image records sorted by
    (date, time, weather, camera).

    Returns list of dicts:
        path, weather, date, camera, timestamp, filename
    """
    records = []
    root = Path(dataset_root)

    if not root.exists():
        print(f"[simulate_feed] ERROR: dataset root not found: {root}")
        sys.exit(1)

    # Structure: <root>/<WEATHER>/<DATE>/<CAMERA>/<images>
    for weather_dir in sorted(root.iterdir()):
        if not weather_dir.is_dir():
            continue
        weather = weather_dir.name.upper()
        if weather_filter and weather not in [w.upper() for w in weather_filter]:
            continue

        for date_dir in sorted(weather_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            date_str = date_dir.name

            for cam_dir in sorted(date_dir.iterdir()):
                if not cam_dir.is_dir():
                    continue
                camera = cam_dir.name
                if camera_filter and camera not in camera_filter:
                    continue

                for img_file in sorted(cam_dir.iterdir()):
                    if img_file.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                        continue
                    d, hhmm = parse_timestamp(img_file.name)
                    ts = f"{d}_{hhmm}" if d else img_file.stem
                    records.append({
                        "path":      str(img_file),
                        "weather":   weather,
                        "date":      date_str,
                        "camera":    camera,
                        "timestamp": ts,
                        "filename":  img_file.name,
                        "sort_key":  (date_str, hhmm, weather, camera),
                    })

    records.sort(key=lambda r: r["sort_key"])
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(
    dataset_root: str,
    backend_url: str,
    speed: float,
    weather_filter: list[str] | None,
    camera_filter: list[str] | None,
    dry_run: bool,
):
    images = collect_images(dataset_root, weather_filter, camera_filter)
    total = len(images)

    if total == 0:
        print("[simulate_feed] No images found with the given filters.")
        sys.exit(0)

    print(f"[simulate_feed] Found {total} images.  Speed={speed}x  Dry-run={dry_run}")
    print(f"[simulate_feed] Posting to {backend_url}/detect\n")

    detect_url = f"{backend_url}/detect"

    prev_ts: str | None = None

    for idx, rec in enumerate(images, start=1):
        # --- Timing: simulate the real cadence scaled by --speed ---
        if prev_ts and speed > 0:
            prev_d, prev_hhmm = prev_ts[:10], prev_ts[11:]
            curr_d, curr_hhmm = rec["timestamp"][:10], rec["timestamp"][11:]

            # Compute real delta in seconds between consecutive timestamps
            try:
                prev_dt = datetime.strptime(f"{prev_d}_{prev_hhmm}", "%Y-%m-%d_%H%M")
                curr_dt = datetime.strptime(f"{curr_d}_{curr_hhmm}", "%Y-%m-%d_%H%M")
                delta_real = abs((curr_dt - prev_dt).total_seconds())
                # Clamp: if different dates or same time, use default interval
                if delta_real == 0 or delta_real > 7200:
                    delta_real = REAL_INTERVAL_SECONDS
            except ValueError:
                delta_real = REAL_INTERVAL_SECONDS

            sleep_s = delta_real / speed
            if sleep_s > 0:
                time.sleep(sleep_s)

        prev_ts = rec["timestamp"]

        label = (f"[{idx:>5}/{total}] {rec['weather']}/{rec['date']}/{rec['camera']}/"
                 f"{rec['filename']}")

        if dry_run:
            print(f"  DRY  {label}")
            continue

        try:
            with open(rec["path"], "rb") as fh:
                resp = requests.post(
                    detect_url,
                    files={"file": (rec["filename"], fh, "image/jpeg")},
                    data={
                        "weather":   rec["weather"],
                        "date":      rec["date"],
                        "camera":    rec["camera"],
                        "timestamp": rec["timestamp"],
                    },
                    timeout=60,
                )
            if resp.status_code == 200:
                data = resp.json()
                occ = data.get("occupancy_pct", "?")
                alerts = data.get("alerts", [])
                alert_str = f"  ⚠ {[a['alert_type'] for a in alerts]}" if alerts else ""
                print(f"  OK   {label}  occ={occ}%{alert_str}")
            else:
                print(f"  ERR  {label}  HTTP {resp.status_code}: {resp.text[:120]}")

        except requests.exceptions.ConnectionError:
            print(f"  CONN {label}  — backend unreachable, retrying in 5 s …")
            time.sleep(5)
        except Exception as exc:
            print(f"  EXC  {label}  {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Simulate a live parking camera feed by POSTing dataset images to the backend."
    )
    parser.add_argument(
        "--dataset", default=DATASET_ROOT,
        help=f"Path to FULL_IMAGE_1000x750 directory (default: {DATASET_ROOT})"
    )
    parser.add_argument(
        "--url", default=BACKEND_URL,
        help=f"FastAPI backend base URL (default: {BACKEND_URL})"
    )
    parser.add_argument(
        "--speed", type=float, default=3600.0,
        help="Playback speed multiplier vs real time. "
             "3600 means 30-min gaps become ~0.5 s gaps (default: 3600)"
    )
    parser.add_argument(
        "--weather", nargs="*", choices=["SUNNY", "OVERCAST", "RAINY"],
        help="Filter to specific weather conditions"
    )
    parser.add_argument(
        "--camera", nargs="*",
        help="Filter to specific cameras, e.g. --camera camera1 camera3"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List images without posting them"
    )

    args = parser.parse_args()

    run(
        dataset_root=args.dataset,
        backend_url=args.url,
        speed=args.speed,
        weather_filter=args.weather,
        camera_filter=args.camera,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
