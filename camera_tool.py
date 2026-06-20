"""camera_tool — capture a photo from a connected camera and describe it.

Person A owns this. Person B just calls camera_tool(prompt).

Capture:
  - Auto-detects a working camera index (prefers an external/iPhone camera,
    falls back to the Mac's built-in FaceTime cam). Override with CAMERA_INDEX.
  - Discards warmup frames so iPhone autofocus/exposure settle before saving.
  - Saves to ./captures/latest.jpg.

Description:
  - Sends the captured JPEG to the Butterbase AI gateway (a vision model) to
    identify the rock, using the caller's `prompt`.
  - If no API key is set or the call fails, falls back to a local OpenCV
    summary so the pipeline never crashes during a demo.

Env vars:
  CAMERA_INDEX           force a specific OpenCV camera index (e.g. "1")
  BUTTERBASE_API_KEY     bb_sk_... key with the ai:gateway scope (for descriptions)
  BUTTERBASE_API_URL     defaults to https://api.butterbase.ai
  BUTTERBASE_VISION_MODEL defaults to anthropic/claude-haiku-4.5
"""

import base64
import os
import time

import cv2
import numpy as np
import requests

CAPTURE_DIR = "captures"
CAPTURE_PATH = os.path.join(CAPTURE_DIR, "latest.jpg")
WARMUP_FRAMES = 10
MAX_SCAN_INDEX = 4  # scan camera indices 0..MAX_SCAN_INDEX-1 during auto-detect

DEFAULT_VISION_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_API_BASE = "https://api.butterbase.ai"


def _open_capture(index: int):
    """Open a camera at `index`, using AVFoundation on macOS when available."""
    backend = getattr(cv2, "CAP_AVFOUNDATION", None)
    cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def _detect_camera_index() -> int:
    """Pick a camera index.

    CAMERA_INDEX wins if set. Otherwise scan 0..MAX_SCAN_INDEX-1, keep the ones
    that actually yield a frame, and prefer the highest index — on a Mac the
    built-in FaceTime cam is usually index 0, so an external/iPhone camera tends
    to land on a higher index.
    """
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
    print(f"[camera_tool] cameras found at {working}, using index {chosen}")
    return chosen


def _capture_frame(index: int) -> np.ndarray:
    """Grab one frame from `index`, discarding warmup frames first."""
    cap = _open_capture(index)
    if cap is None:
        raise RuntimeError(f"Could not open camera index {index}.")
    try:
        for _ in range(WARMUP_FRAMES):
            cap.read()
            time.sleep(0.05)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Camera index {index} opened but returned no frame.")
        return frame
    finally:
        cap.release()


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
    """Offline fallback description from basic OpenCV features."""
    img = cv2.imread(image_path)
    if img is None:
        return "A rock (image could not be analyzed)."
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        x, y, bw, bh = cv2.boundingRect(c)
        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(mask, [c], -1, 255, -1)
        mean_bgr = cv2.mean(img, mask=mask)[:3]
        frac = (bw * bh) / float(w * h)
        size = "small" if frac < 0.1 else "large" if frac > 0.4 else "medium"
        return f"A {size} {_color_name(mean_bgr)} rock (~{bw}x{bh}px in frame)."
    return "A rock on a plain background (no distinct object outline detected)."


def _describe_with_butterbase(image_path: str, prompt: str) -> str:
    """Identify the rock via the Butterbase AI gateway. Raises on any failure."""
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
    body = resp.json()
    return body["choices"][0]["message"]["content"].strip()


def describe_image(image_path: str, prompt: str) -> str:
    """Best-effort description: Butterbase vision first, local CV as fallback."""
    try:
        desc = _describe_with_butterbase(image_path, prompt)
        print("[camera_tool] description via Butterbase AI gateway")
        return desc
    except Exception as e:  # noqa: BLE001 - demo must keep running
        print(f"[camera_tool] Butterbase description unavailable ({e}); using local summary")
        return _describe_locally(image_path)


def camera_tool(prompt: str) -> dict:
    """Capture a photo and return {"image_path", "description"}."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    index = _detect_camera_index()
    frame = _capture_frame(index)
    cv2.imwrite(CAPTURE_PATH, frame)
    print(f"[camera_tool] saved capture to {CAPTURE_PATH}")
    description = describe_image(CAPTURE_PATH, prompt)
    return {"image_path": CAPTURE_PATH, "description": description}


if __name__ == "__main__":
    result = camera_tool("identify this rock")
    print(result)
