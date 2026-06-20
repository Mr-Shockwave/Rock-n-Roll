"""debug_overlay — visualize what the tools detect.

Draws the detected contour (green), its bounding box (cyan), and the
minAreaRect used for the angle (magenta) onto the image, plus the distance
and angle values. Saves to captures/debug.jpg so you can SEE whether
detection is locking onto the rock or onto background clutter.

Usage:
    python debug_overlay.py                  # uses captures/latest.jpg
    python debug_overlay.py path/to/img.jpg
    python debug_overlay.py --capture        # capture a fresh frame first
"""

import sys

import cv2
import numpy as np

import tools

OUT_PATH = "captures/debug.jpg"


def overlay(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        raise SystemExit(f"could not read image: {image_path}")

    contour = tools.find_largest_contour(img)
    dist = tools.distance_tool(image_path)
    ang = tools.angle_tool(image_path)

    if contour is not None:
        cv2.drawContours(img, [contour], -1, (0, 255, 0), 3)          # contour: green
        x, y, w, h = cv2.boundingRect(contour)
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 0), 3)  # bbox: cyan
        box = cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.intp)
        cv2.drawContours(img, [box], 0, (255, 0, 255), 3)             # minAreaRect: magenta
    else:
        cv2.putText(img, "NO OBJECT DETECTED", (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4)

    quality = tools.assess_capture(image_path)
    verdict = "OK" if quality["ok"] else "RETAKE"
    color = (0, 180, 0) if quality["ok"] else (0, 0, 255)
    cv2.putText(img, verdict, (20, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 2, color, 4)

    label = f"distance_cm={dist.get('distance_cm')}  angle_deg={ang.get('angle_deg')}"
    cv2.putText(img, label, (20, img.shape[0] - 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
    cv2.putText(img, quality["feedback"][:70], (20, img.shape[0] - 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    cv2.imwrite(OUT_PATH, img)
    print(f"distance: {dist}")
    print(f"angle:    {ang}")
    print(f"quality:  {quality}")
    print(f"overlay saved -> {OUT_PATH}")
    return OUT_PATH


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--capture" in args:
        args.remove("--capture")
        cam = tools.camera_tool("debug capture")
        path = cam["image_path"]
    else:
        path = args[0] if args else tools.CAPTURE_PATH
    overlay(path)
