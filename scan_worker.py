"""Local scan worker — polls Butterbase for queued sessions and runs the camera loop.

Run alongside the frontend during the demo:
    python scan_worker.py

The frontend calls scan-start on Butterbase; this worker claims the session,
captures from the iPhone (Continuity Camera), uploads frames to Butterbase
storage, then runs local distance/angle only after mineral ID confirmation passes.
"""

from __future__ import annotations

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
    """Render the debug overlay and upload it; return its object_id."""
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
    *,
    preview_object_id: str | None = None,
    focus_rock_description: str | None = None,
) -> dict:
    payload: dict = {
        "session_id": session_id,
        "phase": phase,
        "image_base64": _image_base64(image_path),
    }
    if preview_object_id:
        payload["preview_object_id"] = preview_object_id
        payload["camera_object_id"] = preview_object_id
    if focus_rock_description:
        payload["focus_rock_description"] = focus_rock_description
    resp = requests.post(
        fn_url("scan-frame"),
        headers=_auth_headers(),
        json=payload,
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
    *,
    confirm_confidence_1: float | None = None,
    confirm_confidence_2: float | None = None,
    distance_cm: float | None = None,
    angle_deg: float | None = None,
    geometry: bool = False,
    overlay_object_id: str | None = None,
    final_frame_object_id: str | None = None,
) -> dict:
    payload: dict = {"session_id": session_id}
    if confirm_confidence_1 is not None:
        payload["confirm_confidence_1"] = confirm_confidence_1
    if confirm_confidence_2 is not None:
        payload["confirm_confidence_2"] = confirm_confidence_2
    if geometry:
        payload["geometry"] = True
        payload["distance_cm"] = distance_cm
        payload["angle_deg"] = angle_deg
        if overlay_object_id:
            payload["overlay_object_id"] = overlay_object_id
        if final_frame_object_id:
            payload["final_frame_object_id"] = final_frame_object_id
    resp = requests.post(
        fn_url("scan-finalize"),
        headers=_auth_headers(),
        json=payload,
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


def _run_confirmation(session_id: str) -> tuple[list[float], str | None, str | None]:
    """confirm1 + confirm2; returns confidences, focus description, last image path."""
    confidences: list[float] = []
    final_path: str | None = None
    focus_rock: str | None = None

    time.sleep(0.5)
    final_path = capture_frame_only()
    preview_id = _safe_upload(final_path)
    result = _post_frame(session_id, "confirm1", final_path, preview_object_id=preview_id)
    c1 = float(result.get("best_confidence", 0))
    confidences.append(c1)
    focus_rock = result.get("focus_rock_description")
    qualifying = result.get("qualifying_rocks") or []
    print(
        f"[worker] confirm1 conf={c1:.2f} "
        f"qualifying={len(qualifying)} focus={focus_rock!r} preview={preview_id}"
    )

    time.sleep(0.5)
    final_path = capture_frame_only()
    preview_id = _safe_upload(final_path)
    if focus_rock:
        result = _post_frame(
            session_id,
            "confirm2",
            final_path,
            preview_object_id=preview_id,
            focus_rock_description=focus_rock,
        )
        c2 = float(result.get("best_confidence", 0))
        print(f"[worker] confirm2 conf={c2:.2f} (focused rock only) preview={preview_id}")
    else:
        c2 = 0.0
        print("[worker] confirm2 skipped — no focus rock from stop frame")
    confidences.append(c2)

    return confidences, focus_rock, final_path


def run_session(session: dict) -> None:
    session_id = session["session_id"]
    target = session.get("target_mineral", "?")
    print(f"[worker] scanning for '{target}' (session {session_id})")
    print(
        f"[worker] interval={SCAN_INTERVAL_SECONDS}s timeout={SCAN_TIMEOUT_SECONDS}s "
        f"stop>{CONFIDENCE_STOP_THRESHOLD}"
    )

    deadline = time.time() + SCAN_TIMEOUT_SECONDS

    while time.time() < deadline:
        stop_triggered = False

        while time.time() < deadline and not stop_triggered:
            image_path = capture_frame_only()
            preview_id = _safe_upload(image_path)
            result = _post_frame(
                session_id, "scan", image_path, preview_object_id=preview_id
            )
            conf = result.get("best_confidence", 0)
            action = result.get("action", "continue")
            print(
                f"[worker] scan frame conf={conf:.2f} action={action} preview={preview_id}"
            )
            if action == "stop":
                stop_triggered = True
                break
            time.sleep(SCAN_INTERVAL_SECONDS)

        if not stop_triggered:
            print("[worker] scan timed out — no promising rocks found")
            _timeout(session_id)
            return

        confidences, _focus, final_path = _run_confirmation(session_id)

        outcome = _finalize(
            session_id,
            confirm_confidence_1=confidences[0] if confidences else 0.0,
            confirm_confidence_2=confidences[1] if len(confidences) > 1 else 0.0,
        )
        print(f"[worker] decision: {json.dumps(outcome, indent=2)}")

        if outcome.get("mistake_continue"):
            print("[worker] confirmation below threshold — resuming scan")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        if outcome.get("needs_geometry"):
            final_path = final_path or capture_frame_only()
            dist = distance_tool(final_path)
            ang = angle_tool(final_path)
            distance_cm = dist.get("distance_cm", -1)
            angle_deg = ang.get("angle_deg", 0.0)
            print(
                f"[worker] approach geometry distance={distance_cm}cm angle={angle_deg}°"
            )
            final_frame_id = _safe_upload(final_path)
            overlay_id = _make_overlay_upload(final_path)
            print(f"[worker] uploaded final_frame={final_frame_id} overlay={overlay_id}")
            outcome = _finalize(
                session_id,
                distance_cm=float(distance_cm) if distance_cm != -1 else -1,
                angle_deg=float(angle_deg),
                geometry=True,
                overlay_object_id=overlay_id,
                final_frame_object_id=final_frame_id,
            )

        print(f"[worker] done: {json.dumps(outcome, indent=2)}")
        return


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
