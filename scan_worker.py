"""Local scan worker — polls Butterbase for queued sessions and runs the camera loop.

Run alongside the frontend during the demo:
    python scan_worker.py

The frontend calls scan-start on Butterbase; this worker claims the session,
captures from the iPhone (Continuity Camera), uploads frames, then runs local
distance/angle on the final confirmation frame.
"""

from __future__ import annotations

import base64
import json
import sys
import time

import requests

import debug_overlay
import storage
from tools import (
    CONFIDENCE_STOP_THRESHOLD,
    SCAN_INTERVAL_SECONDS,
    SCAN_TIMEOUT_SECONDS,
    angle_tool,
    capture_frame_only,
    distance_tool,
    fn_url,
    get_api_base,
)


def _safe_upload(image_path: str) -> str | None:
    """Upload an image to Butterbase Storage; never crash the scan loop."""
    try:
        return storage.upload_image(image_path)
    except Exception as exc:  # noqa: BLE001 - demo must keep running
        print(f"[worker] image upload failed: {exc}")
        return None


def _make_overlay_upload(image_path: str) -> str | None:
    """Render the debug.jpg-style overlay and upload it; return its object_id."""
    try:
        overlay_path = debug_overlay.overlay(image_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] overlay render failed: {exc}")
        return None
    return _safe_upload(overlay_path)


def _auth_headers() -> dict:
    import os

    key = os.environ.get("BUTTERBASE_API_KEY")
    if not key:
        raise RuntimeError("BUTTERBASE_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _image_base64(image_path: str) -> str:
    from tools import _encode_image_for_vision

    data_uri = _encode_image_for_vision(image_path)
    if data_uri.startswith("data:"):
        return data_uri.split(",", 1)[1]
    return data_uri


def _post_frame(
    session_id: str,
    phase: str,
    image_path: str,
    preview_object_id: str | None = None,
) -> dict:
    resp = requests.post(
        fn_url("scan-frame"),
        headers=_auth_headers(),
        json={
            "session_id": session_id,
            "phase": phase,
            "image_base64": _image_base64(image_path),
            "preview_object_id": preview_object_id,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def _claim_session() -> dict | None:
    resp = requests.post(fn_url("scan-claim"), headers=_auth_headers(), json={}, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    return body.get("session")


def _finalize(
    session_id: str,
    distance_cm: float,
    angle_deg: float,
    c1: float,
    c2: float,
    overlay_object_id: str | None = None,
    final_frame_object_id: str | None = None,
) -> dict:
    resp = requests.post(
        fn_url("scan-finalize"),
        headers=_auth_headers(),
        json={
            "session_id": session_id,
            "distance_cm": distance_cm,
            "angle_deg": angle_deg,
            "confirm_confidence_1": c1,
            "confirm_confidence_2": c2,
            "overlay_object_id": overlay_object_id,
            "final_frame_object_id": final_frame_object_id,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _timeout(session_id: str) -> None:
    requests.post(
        fn_url("scan-timeout"),
        headers=_auth_headers(),
        json={"session_id": session_id},
        timeout=30,
    )


def run_session(session: dict) -> None:
    session_id = session["session_id"]
    targets = session.get("target_minerals")
    if isinstance(targets, str):
        try:
            targets = json.loads(targets)
        except Exception:  # noqa: BLE001
            targets = None
    if not targets:
        targets = [session.get("target_mineral", "?")]
    print(f"[worker] scanning for {', '.join(map(str, targets))} (session {session_id})")
    print(
        f"[worker] interval={SCAN_INTERVAL_SECONDS}s timeout={SCAN_TIMEOUT_SECONDS}s "
        f"stop>{CONFIDENCE_STOP_THRESHOLD}"
    )

    deadline = time.time() + SCAN_TIMEOUT_SECONDS
    stop_triggered = False

    while time.time() < deadline and not stop_triggered:
        image_path = capture_frame_only()
        preview_id = _safe_upload(image_path)  # live preview for the UI
        result = _post_frame(session_id, "scan", image_path, preview_object_id=preview_id)
        conf = result.get("best_confidence", 0)
        action = result.get("action", "continue")
        print(f"[worker] scan frame conf={conf:.2f} action={action} preview={preview_id}")
        if action == "stop":
            stop_triggered = True
            break
        time.sleep(SCAN_INTERVAL_SECONDS)

    if not stop_triggered:
        print("[worker] scan timed out — no promising rocks found")
        _timeout(session_id)
        return

    confidences: list[float] = []
    final_path = None
    for phase in ("confirm1", "confirm2"):
        time.sleep(0.5)
        final_path = capture_frame_only()
        preview_id = _safe_upload(final_path)
        result = _post_frame(session_id, phase, final_path, preview_object_id=preview_id)
        conf = float(result.get("best_confidence", 0))
        confidences.append(conf)
        print(f"[worker] {phase} conf={conf:.2f}")

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    final_path = final_path or capture_frame_only()
    dist = distance_tool(final_path)
    ang = angle_tool(final_path)
    distance_cm = dist.get("distance_cm", -1)
    angle_deg = ang.get("angle_deg", 0.0)

    print(f"[worker] geometry distance={distance_cm}cm angle={angle_deg}° avg_conf={avg_conf:.2f}")

    # Upload the final raw frame and the annotated overlay (bbox/angle/distance).
    final_frame_id = _safe_upload(final_path)
    overlay_id = _make_overlay_upload(final_path)
    print(f"[worker] uploaded final_frame={final_frame_id} overlay={overlay_id}")

    outcome = _finalize(
        session_id,
        float(distance_cm) if distance_cm != -1 else -1,
        float(angle_deg),
        confidences[0] if confidences else 0.0,
        confidences[1] if len(confidences) > 1 else confidences[0] if confidences else 0.0,
        overlay_object_id=overlay_id,
        final_frame_object_id=final_frame_id,
    )
    print(f"[worker] done: {json.dumps(outcome, indent=2)}")


def main() -> None:
    print(f"[worker] polling {get_api_base()}/fn/scan-claim …")
    while True:
        try:
            session = _claim_session()
            if session:
                run_session(session)
            else:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[worker] stopped")
            sys.exit(0)
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] error: {exc}")
            time.sleep(2.0)


if __name__ == "__main__":
    main()
