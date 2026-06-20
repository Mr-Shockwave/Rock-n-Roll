"""List available camera device indices.

Run this to find which index is your iPhone vs the built-in cam:
    python list_cameras.py

On macOS the host app (Terminal / VS Code) needs Camera permission first:
System Settings > Privacy & Security > Camera.
"""

import cv2


def list_cameras(max_index: int = 6) -> list:
    backend = getattr(cv2, "CAP_AVFOUNDATION", None)
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend) if backend is not None else cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                found.append(i)
                print(f"index {i}: OK  ({w}x{h})")
            else:
                print(f"index {i}: opened but no frame (permission? in use?)")
        cap.release()
    if not found:
        print("No usable cameras. Check Camera permission and that the iPhone is connected.")
    else:
        print(f"\nUsable indices: {found}  -> set CAMERA_INDEX to the one you want.")
    return found


if __name__ == "__main__":
    list_cameras()
