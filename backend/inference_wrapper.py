"""
inference_wrapper.py
--------------------
Wraps inference_yolo_ultralytics.py (called as a subprocess) so that the
FastAPI backend can trigger per-image inference and retrieve metrics.

Strategy
--------
1. Write the single image to a temp directory.
2. Call `python backend/inference_yolo_ultralytics.py --input_path <tmp> ...`
3. Parse the output CSV that the script always writes to
   `<output_path>/batch_*/df_individual_metrics_*.csv`.
4. Return a dict with the columns we need.

The script already handles model loading, masking, etc.  We just need to
plumb the right arguments and read the result.
"""

import os
import sys
import glob
import shutil
import tempfile
import subprocess
import time
import pandas as pd

# Resolve paths relative to the project root (one level above backend/)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

MODEL_PATH = os.path.join(_ROOT, "assets", "models", "yolo11m")   # no extension — script appends .pt/.tflite
MASK_DIR   = os.path.join(_ROOT, "assets", "masks")
SCRIPT     = os.path.join(_HERE, "inference_yolo_ultralytics.py")


def get_mask_path(camera: str) -> str:
    """
    Return the mask file for a given camera id, e.g. 'camera1' → 
    assets/masks/cnrpark_mask_camera1_1000_750_bw.png
    Falls back to the all-black mask that ships with the backend.
    """
    num = camera.replace("camera", "")
    candidate = os.path.join(MASK_DIR, f"cnrpark_mask_camera{num}_1000_750_bw.png")
    if os.path.exists(candidate):
        return candidate
    fallback = os.path.join(_HERE, "all_black_mask.png")
    return fallback


def run_inference(
    image_path: str,
    camera: str,
    *,
    model: str = MODEL_PATH,
    savefigs: str = "no",
) -> dict:
    """
    Run inference on a single image and return a metrics dict.

    Returns
    -------
    dict with keys:
        image_name, timestamp, predicted_cars, predicted_cars_parked,
        processing_time   (and zeros/defaults if inference fails)
    """
    mask_file = get_mask_path(camera)

    # Create isolated temp directories
    tmp_in  = tempfile.mkdtemp(prefix="parking_in_")
    tmp_out = tempfile.mkdtemp(prefix="parking_out_")

    try:
        # Copy image into the temp input dir so the script finds it
        dest = os.path.join(tmp_in, os.path.basename(image_path))
        shutil.copy2(image_path, dest)

        cmd = [
            sys.executable, SCRIPT,
            "--model",      model,
            "--input_path", tmp_in,
            "--output_path",tmp_out,
            "--mask_type",  "post",
            "--mask_file",  mask_file,
            "--savefigs",   savefigs,
            "--num_splits", "1",
        ]

        t0 = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.perf_counter() - t0

        if result.returncode != 0:
            print("[inference_wrapper] stderr:", result.stderr[-2000:])
            return _empty_result(image_path, elapsed)

        # Find the CSV written by the script
        csv_files = glob.glob(
            os.path.join(tmp_out, "**", "df_individual_metrics_*.csv"),
            recursive=True,
        )
        if not csv_files:
            print("[inference_wrapper] No CSV produced.")
            return _empty_result(image_path, elapsed)

        df = pd.read_csv(csv_files[0])
        if df.empty:
            return _empty_result(image_path, elapsed)

        row = df.iloc[-1]
        return {
            "image_name":              str(row.get("image_name", os.path.basename(image_path))),
            "timestamp":               str(row.get("timestamp", "")),
            "predicted_cars":          int(row.get("predicted_cars", 0) or 0),
            "predicted_cars_parked":  int(row.get("predicted_cars_parked", 0)) if not pd.isna(row.get("predicted_cars_parked", 0)) else  int(row.get("predicted_cars", 0) or 0), #Error in detection
            "processing_time":         float(row.get("processing_time", elapsed) or elapsed),
        }

    finally:
        shutil.rmtree(tmp_in,  ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)


def _empty_result(image_path: str, elapsed: float) -> dict:
    return {
        "image_name":             os.path.basename(image_path),
        "timestamp":              "",
        "predicted_cars":         0,
        "predicted_cars_parked": 0,
        "processing_time":        elapsed,
    }
