"""tools.py — the vision toolkit Person B imports.

Public API (the agreed contract):
    camera_tool(prompt: str)   -> {"image_path", "description"}
    distance_tool(image_path)  -> {"distance_cm", "bbox"}        (or {"distance_cm": -1, "error"})
    angle_tool(image_path)     -> {"angle_deg"}                  (or {"angle_deg": 0.0, "error"})
    run_vision_pipeline(prompt)-> {"image_path", "description", "distance_cm", "angle_deg"}

This file is self-contained — it does NOT import the standalone *_tool.py files.

Usage:
    from tools import run_vision_pipeline
    result = run_vision_pipeline("identify this rock")
    print(result)

Env vars:
    CAMERA_INDEX            force a specific OpenCV camera index (e.g. "1")
    BUTTERBASE_API_KEY      bb_sk_... key with the ai:gateway scope (for descriptions)
    BUTTERBASE_API_URL      defaults to https://api.butterbase.ai
    BUTTERBASE_VISION_MODEL defaults to anthropic/claude-haiku-4.5
"""

import base64
import json
import os
import time

import cv2
import numpy as np
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CAPTURE_DIR = "captures"
CAPTURE_PATH = os.path.join(CAPTURE_DIR, "latest.jpg")
WARMUP_FRAMES = 10
MAX_SCAN_INDEX = 4

KNOWN_WIDTH_CM = 8.0
FOCAL_LENGTH_PX = 800.0

DEFAULT_VISION_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_API_BASE = "https://api.butterbase.ai"


# ---------------------------------------------------------------------------
# Shared contour detection (robust to light AND dark rocks)
# ---------------------------------------------------------------------------
def find_largest_contour(img: np.ndarray):
    """Return the largest object-like contour, or None.

    Combines Otsu thresholding (both polarities) with a Canny-edge fallback and
    discards contours that fill almost the whole frame (the background), so it
    works on quartz (light) and obsidian (dark) alike.
    """
    h, w = img.shape[:2]
    frame_area = float(h * w)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    masks = []
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(otsu)
    masks.append(cv2.bitwise_not(otsu))

    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    masks.append(edges)

    best = None
    best_area = 0.0
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < 0.01 * frame_area or area > 0.95 * frame_area:
                continue
            if area > best_area:
                best_area = area
                best = c
    return best


# ---------------------------------------------------------------------------
# Camera capture
# ---------------------------------------------------------------------------
def _open_capture(index: int):
    backend = getattr(cv2, "CAP_AVFOUNDATION", None)
    cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def _detect_camera_index() -> int:
    """CAMERA_INDEX wins; else scan 0..MAX_SCAN_INDEX-1 and prefer the highest
    working index (external/iPhone cameras tend to sit above the built-in cam)."""
    forced = os.environ.get("CAMERA_INDEX")
    if forced is not None and forced.strip() != "":
        return int(forced)

    working = []
    for i in range(MAX_SCAN_INDEX):
        cap = _open_capture(i)
        if cap is None:
            continue
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            working.append(i)

    if not working:
        raise RuntimeError(
            "No camera frame available. On macOS this is usually a Camera permission "
            "issue: grant your terminal / VS Code app Camera access in System Settings > "
            "Privacy & Security > Camera, then fully quit and reopen it. Otherwise plug "
            "in the iPhone or set CAMERA_INDEX."
        )
    chosen = max(working)
    print(f"[tools] cameras found at {working}, using index {chosen}")
    return chosen


def _capture_frame(index: int) -> np.ndarray:
    cap = _open_capture(index)
    if cap is None:
        raise RuntimeError(f"Could not open camera index {index}.")
    try:
        for _ in range(WARMUP_FRAMES):  # let autofocus/exposure settle
            cap.read()
            time.sleep(0.05)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Camera index {index} opened but returned no frame.")
        return frame
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Description (Butterbase vision, with offline fallback)
# ---------------------------------------------------------------------------
def _color_name(bgr) -> str:
    b, g, r = bgr
    brightness = (int(b) + int(g) + int(r)) / 3.0
    if max(r, g, b) - min(r, g, b) < 25:
        if brightness < 70:
            return "dark gray/black"
        if brightness > 185:
            return "white/pale"
        return "gray"
    if r >= g and r >= b:
        return "reddish/tan"
    if g >= r and g >= b:
        return "greenish"
    return "bluish"


def _describe_locally(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return "A rock (image could not be analyzed)."
    h, w = img.shape[:2]
    contour = find_largest_contour(img)
    if contour is None:
        return "A rock on a plain background (no distinct object outline detected)."
    x, y, bw, bh = cv2.boundingRect(contour)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mean_bgr = cv2.mean(img, mask=mask)[:3]
    frac = (bw * bh) / float(w * h)
    size = "small" if frac < 0.1 else "large" if frac > 0.4 else "medium"
    return f"A {size} {_color_name(mean_bgr)} rock (~{bw}x{bh}px in frame)."


def _describe_with_butterbase(image_path: str, prompt: str) -> str:
    api_key = os.environ.get("BUTTERBASE_API_KEY")
    if not api_key:
        raise RuntimeError("BUTTERBASE_API_KEY not set")

    base = os.environ.get("BUTTERBASE_API_URL", DEFAULT_API_BASE).rstrip("/")
    model = os.environ.get("BUTTERBASE_VISION_MODEL", DEFAULT_VISION_MODEL)
    url = f"{base}/v1/chat/completions"

    with open(image_path, "rb") as f:
        data_uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")

    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a geologist. Identify the rock in the image. Reply with "
                    "the likely rock type and 1-2 sentences on its color, texture, and "
                    "distinguishing features. Be concise."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Identify this rock."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def describe_image(image_path: str, prompt: str) -> str:
    """Butterbase vision first; fall back to a local CV summary if it fails."""
    try:
        desc = _describe_with_butterbase(image_path, prompt)
        print("[tools] description via Butterbase AI gateway")
        return desc
    except Exception as e:  # noqa: BLE001 - demo must keep running
        print(f"[tools] Butterbase description unavailable ({e}); using local summary")
        return _describe_locally(image_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def camera_tool(prompt: str) -> dict:
    """Capture a photo and return {"image_path", "description"}."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    index = _detect_camera_index()
    frame = _capture_frame(index)
    cv2.imwrite(CAPTURE_PATH, frame)
    print(f"[tools] saved capture to {CAPTURE_PATH}")
    description = describe_image(CAPTURE_PATH, prompt)
    return {"image_path": CAPTURE_PATH, "description": description}


def distance_tool(image_path: str) -> dict:
    """Estimate distance (cm) to the largest object in the image."""
    img = cv2.imread(image_path)
    if img is None:
        return {"distance_cm": -1, "error": f"could not read image: {image_path}"}
    contour = find_largest_contour(img)
    if contour is None:
        return {"distance_cm": -1, "error": "no object detected"}
    x, y, bw, bh = cv2.boundingRect(contour)
    if bw <= 0:
        return {"distance_cm": -1, "error": "no object detected"}
    distance_cm = (KNOWN_WIDTH_CM * FOCAL_LENGTH_PX) / float(bw)
    return {"distance_cm": round(distance_cm, 2), "bbox": [int(x), int(y), int(bw), int(bh)]}


def angle_tool(image_path: str) -> dict:
    """Return the rotation angle of the largest object as {"angle_deg": float}."""
    img = cv2.imread(image_path)
    if img is None:
        return {"angle_deg": 0.0, "error": f"could not read image: {image_path}"}
    contour = find_largest_contour(img)
    if contour is None:
        return {"angle_deg": 0.0, "error": "no object detected"}
    rect = cv2.minAreaRect(contour)  # ((cx, cy), (w, h), angle)
    return {"angle_deg": round(float(rect[2]), 2)}


def run_vision_pipeline(prompt: str) -> dict:
    """Capture -> describe -> distance -> angle, merged into one dict."""
    cam = camera_tool(prompt)
    image_path = cam["image_path"]
    dist = distance_tool(image_path)
    ang = angle_tool(image_path)
    return {
        "image_path": image_path,
        "description": cam.get("description"),
        "distance_cm": dist.get("distance_cm"),
        "angle_deg": ang.get("angle_deg"),
    }


if __name__ == "__main__":
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "identify this rock"
    result = run_vision_pipeline(prompt)
    print(json.dumps(result, indent=2))
