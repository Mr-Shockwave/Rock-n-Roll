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


def _load_env_file(path: str = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overrides).

    Dependency-free (no python-dotenv). Real environment variables always win
    over the file, so `CAMERA_INDEX=0 python tools.py` still overrides .env.
    Looks for .env next to this file so it works regardless of cwd.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_env_file()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CAPTURE_DIR = "captures"
CAPTURE_PATH = os.path.join(CAPTURE_DIR, "latest.jpg")
WARMUP_FRAMES = 10
MAX_SCAN_INDEX = 4

KNOWN_WIDTH_CM = 8.0
FOCAL_LENGTH_PX = 800.0

# Quality gates for assess_capture() — when these fail, the pipeline reports
# `ok: False` with actionable feedback instead of returning unreliable numbers.
BLUR_VAR_MIN = 50.0       # Laplacian variance of the rock ROI below this = blurry
BRIGHTNESS_MIN = 40.0     # mean ROI brightness (0-255) below this = too dark
BRIGHTNESS_MAX = 215.0    # above this = overexposed
BBOX_FRAME_FRAC_MAX = 0.85  # bbox covering more than this of the frame = not isolated
DIST_MIN_CM = 5.0         # distance below this implies a near-full-frame (bad) bbox
DIST_MAX_CM = 150.0       # distance above this implies a tiny/spurious bbox

DEFAULT_VISION_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_API_BASE = "https://api.butterbase.ai"

# Scan pipeline tunables (change here or via .env)
SCAN_INTERVAL_SECONDS = float(os.environ.get("SCAN_INTERVAL_SECONDS", "2"))
SCAN_TIMEOUT_SECONDS = float(os.environ.get("SCAN_TIMEOUT_SECONDS", "30"))
CONFIDENCE_STOP_THRESHOLD = float(os.environ.get("CONFIDENCE_STOP_THRESHOLD", "0.5"))


def get_api_base() -> str:
    """Return the app-scoped Butterbase API base URL."""
    url = os.environ.get("BUTTERBASE_API_URL", DEFAULT_API_BASE).rstrip("/")
    if "/v1/app_" in url:
        return url
    app_id = os.environ.get("BUTTERBASE_APP_ID")
    if app_id:
        return f"{DEFAULT_API_BASE}/v1/{app_id}"
    return url


def fn_url(name: str) -> str:
    return f"{get_api_base()}/fn/{name}"


def storage_control_base() -> str:
    """Control API host (storage routes live here, not under /v1/app_)."""
    url = os.environ.get("BUTTERBASE_API_URL", DEFAULT_API_BASE).rstrip("/")
    if "/v1/" in url:
        return url.split("/v1/")[0]
    return DEFAULT_API_BASE


def upload_preview_image(image_path: str, session_id: str, phase: str) -> str | None:
    """Upload a camera snapshot to Butterbase storage; return object_id or None."""
    api_key = os.environ.get("BUTTERBASE_API_KEY")
    app_id = os.environ.get("BUTTERBASE_APP_ID")
    if not api_key or not app_id:
        return None
    try:
        size = os.path.getsize(image_path)
        filename = f"scan_{session_id}_{phase}_{int(time.time() * 1000)}.jpg"
        resp = requests.post(
            f"{storage_control_base()}/storage/{app_id}/upload",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "filename": filename,
                "contentType": "image/jpeg",
                "sizeBytes": size,
                "public": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        upload_url = body.get("uploadUrl") or body.get("upload_url")
        object_id = body.get("objectId") or body.get("object_id")
        if not upload_url or not object_id:
            return None
        with open(image_path, "rb") as f:
            put = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": "image/jpeg"},
                timeout=60,
            )
        put.raise_for_status()
        return str(object_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[tools] storage upload failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Shared contour detection (robust to light AND dark rocks)
# ---------------------------------------------------------------------------
def _detect_object(img: np.ndarray):
    """Find the rock contour and report confidence.

    Returns (contour_or_None, isolated). `isolated` is True only when a contour
    that does NOT touch the frame border was found — that's the high-confidence
    case. If detection had to fall back to a border-touching contour (e.g. a
    busy background where the strongest edge is the table/floor boundary),
    `isolated` is False, which the quality check treats as unreliable.
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

    margin = 5
    best_inside = None
    best_inside_area = 0.0
    best_any = None
    best_any_area = 0.0
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < 0.01 * frame_area or area > 0.95 * frame_area:
                continue
            if area > best_any_area:
                best_any_area = area
                best_any = c
            x, y, bw, bh = cv2.boundingRect(c)
            touches_border = (
                x <= margin or y <= margin
                or (x + bw) >= (w - margin) or (y + bh) >= (h - margin)
            )
            if not touches_border and area > best_inside_area:
                best_inside_area = area
                best_inside = c
    if best_inside is not None:
        return best_inside, True
    return best_any, False


def find_largest_contour(img: np.ndarray):
    """Return the largest object-like contour, or None (confidence ignored)."""
    contour, _ = _detect_object(img)
    return contour


def assess_capture(image_path: str) -> dict:
    """Judge whether an image is good enough for reliable distance/angle.

    Returns {"ok": bool, "feedback": str, "issues": [str, ...]}. This is the
    feedback signal a robot/agent reads to decide whether to retake, reposition,
    or ask the user. `description` (vision) works regardless and is not gated.
    """
    img = cv2.imread(image_path)
    if img is None:
        return {
            "ok": False,
            "feedback": "Could not read the captured image; retake.",
            "issues": ["unreadable_image"],
        }

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    issues = []
    msgs = []

    contour, isolated = _detect_object(img)

    # Blur — measured on the rock ROI when we have one, else the whole frame, so
    # a plain (uniform) background doesn't get mistaken for "blurry".
    if contour is not None:
        x, y, bw, bh = cv2.boundingRect(contour)
        roi = gray[y:y + bh, x:x + bw]
    else:
        x = y = 0
        bw, bh = w, h
        roi = gray
    blur_var = float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size else 0.0
    if blur_var < BLUR_VAR_MIN:
        issues.append("blurry")
        msgs.append("Image looks blurry — hold the camera steady, let it focus, and retake.")

    # Exposure
    brightness = float(gray.mean())
    if brightness < BRIGHTNESS_MIN:
        issues.append("too_dark")
        msgs.append("Image is too dark — add light and retake.")
    elif brightness > BRIGHTNESS_MAX:
        issues.append("too_bright")
        msgs.append("Image is overexposed — reduce glare/light and retake.")

    # Object detection + isolation
    if contour is None:
        issues.append("no_object")
        msgs.append("No rock detected — place a rock in view on a plain surface.")
    else:
        frac = (bw * bh) / float(w * h)
        if not isolated or frac > BBOX_FRAME_FRAC_MAX:
            issues.append("object_not_isolated")
            msgs.append("Couldn't separate the rock from the background — put it on a "
                        "plain surface filling the frame and retake.")
        else:
            dist = (KNOWN_WIDTH_CM * FOCAL_LENGTH_PX) / float(bw) if bw else -1.0
            if dist < DIST_MIN_CM or dist > DIST_MAX_CM:
                issues.append("implausible_distance")
                msgs.append(f"Distance estimate looks off ({dist:.0f} cm) — reframe so "
                            "the rock is fully in view and retake.")

    ok = not issues
    feedback = "Input looks good." if ok else " ".join(msgs)
    return {"ok": ok, "feedback": feedback, "issues": issues}


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


def _capture_frame(index: int, wake_timeout: float = 10.0) -> np.ndarray:
    cap = _open_capture(index)
    if cap is None:
        raise RuntimeError(f"Could not open camera index {index}.")
    try:
        # Wait for the first valid frame. An iPhone Continuity Camera can take a
        # few seconds to wake, returning empty reads until it starts streaming.
        deadline = time.time() + wake_timeout
        first = None
        while time.time() < deadline:
            ok, f = cap.read()
            if ok and f is not None:
                first = f
                break
            time.sleep(0.2)
        if first is None:
            raise RuntimeError(
                f"Camera index {index} opened but returned no frame within "
                f"{wake_timeout:.0f}s. If this is an iPhone, make sure it is locked, "
                "propped up still, and rear camera facing the scene."
            )
        # Discard warmup frames so autofocus/exposure settle, then grab the shot.
        for _ in range(WARMUP_FRAMES):
            cap.read()
            time.sleep(0.05)
        ok, frame = cap.read()
        return frame if ok and frame is not None else first
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


def _encode_image_for_vision(image_path: str, max_side: int = 1024,
                             quality: int = 85) -> str:
    """Downscale + re-encode to a base64 JPEG data URI.

    Full-res phone photos (esp. on textured backgrounds) can exceed the gateway's
    payload limit, so shrink the longest side to `max_side` px. That's plenty for
    a vision model to identify a rock.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"could not read image: {image_path}")
    h, w = img.shape[:2]
    scale = min(1.0, float(max_side) / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("failed to encode image for vision request")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def _describe_with_butterbase(image_path: str, prompt: str) -> str:
    api_key = os.environ.get("BUTTERBASE_API_KEY")
    if not api_key:
        raise RuntimeError("BUTTERBASE_API_KEY not set")

    base = get_api_base()
    model = os.environ.get("BUTTERBASE_VISION_MODEL", DEFAULT_VISION_MODEL)
    url = f"{base}/chat/completions"

    data_uri = _encode_image_for_vision(image_path)

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
def capture_frame_only() -> str:
    """Capture a photo without running vision. Returns the saved image path."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    index = _detect_camera_index()
    frame = _capture_frame(index)
    cv2.imwrite(CAPTURE_PATH, frame)
    print(f"[tools] saved capture to {CAPTURE_PATH}")
    return CAPTURE_PATH


def camera_tool(prompt: str) -> dict:
    """Capture a photo and return {"image_path", "description"}."""
    image_path = capture_frame_only()
    description = describe_image(image_path, prompt)
    return {"image_path": image_path, "description": description}


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
    """Capture -> describe -> distance -> angle, merged into one dict.

    Adds `ok`/`feedback`/`issues`: when the capture isn't good enough for
    reliable distance/angle, `ok` is False and `feedback` says what to fix.
    The numbers are still returned (so the agent can inspect them) but should
    be treated as unreliable when `ok` is False. This is the report side of the
    feedback loop — the agent/robot decides whether to retake or reposition.
    """
    cam = camera_tool(prompt)
    image_path = cam["image_path"]
    dist = distance_tool(image_path)
    ang = angle_tool(image_path)
    quality = assess_capture(image_path)
    result = {
        "ok": quality["ok"],
        "feedback": quality["feedback"],
        "issues": quality["issues"],
        "image_path": image_path,
        "description": cam.get("description"),
        "distance_cm": dist.get("distance_cm"),
        "angle_deg": ang.get("angle_deg"),
    }
    if not quality["ok"]:
        print(f"[tools] LOW CONFIDENCE: {quality['feedback']}")
    return result


if __name__ == "__main__":
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "identify this rock"
    result = run_vision_pipeline(prompt)
    print(json.dumps(result, indent=2))
