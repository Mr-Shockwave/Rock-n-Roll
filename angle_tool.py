"""angle_tool — estimate the approach angle (degrees) to the rock.

Finds the largest object-like contour, fits a minimum-area rectangle with
cv2.minAreaRect, and returns its rotation angle. This tells the arm what angle
to approach from.

Uses the same robust (light/dark rock) contour detection as distance_tool.
"""

import cv2
import numpy as np


def find_largest_contour(img: np.ndarray):
    """Return the largest object-like contour, or None.

    Robust to light/dark rocks: combines Otsu (both polarities) and Canny edges,
    and discards contours that fill almost the whole frame (the background).
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

    # The target object sits fully inside the frame, while background boundaries
    # (table edge, cloth seam, floor line) tend to touch the borders. Prefer the
    # largest contour that does NOT touch the border; fall back to any if none do.
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
    return best_inside if best_inside is not None else best_any


def angle_tool(image_path: str) -> dict:
    """Return the rotation angle of the largest object as {"angle_deg": float}."""
    img = cv2.imread(image_path)
    if img is None:
        return {"angle_deg": 0.0, "error": f"could not read image: {image_path}"}

    contour = find_largest_contour(img)
    if contour is None:
        return {"angle_deg": 0.0, "error": "no object detected"}

    rect = cv2.minAreaRect(contour)  # ((cx, cy), (w, h), angle)
    angle = rect[2]
    return {"angle_deg": round(float(angle), 2)}


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "test.jpg"
    print(angle_tool(path))
