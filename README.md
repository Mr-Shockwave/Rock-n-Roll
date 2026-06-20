# Rock-n-Roll — vision tools (Person A)

Camera capture + distance + angle estimation for the rock-picking demo.
Person B imports `tools.py`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install opencv-python numpy requests
```

(The repo already has a `.venv` if you cloned the dev machine.)

## The contract (what Person B calls)

```python
from tools import run_vision_pipeline
result = run_vision_pipeline("identify this rock")
print(result)
# {"ok": true,
#  "feedback": "Input looks good.",
#  "issues": [],
#  "image_path": "captures/latest.jpg",
#  "description": "...",
#  "distance_cm": 23.4,
#  "angle_deg": 15.2}
```

Individual tools are also exported:

```python
from tools import camera_tool, distance_tool, angle_tool
camera_tool(prompt)      -> {"image_path", "description"}
distance_tool(image_path)-> {"distance_cm", "bbox"}   # or {"distance_cm": -1, "error"}
angle_tool(image_path)   -> {"angle_deg"}             # or {"angle_deg": 0.0, "error"}
```

## Feedback loop (`ok` / `feedback` / `issues`)

`run_vision_pipeline` doesn't return silent garbage when the shot is bad. It
runs `assess_capture(image_path)` and adds:

- **`ok`** — `false` when the image isn't good enough for reliable distance/angle.
- **`feedback`** — a human/robot-readable message saying what to fix.
- **`issues`** — machine-readable codes: `no_object`, `object_not_isolated`,
  `blurry`, `too_dark`, `too_bright`, `implausible_distance`, `unreadable_image`.

The agent/robot reads `ok` and decides whether to retake, reposition, or ask the
user — that's the bidirectional loop. `description` (the vision model) is **not**
gated, since it works regardless of background. The `distance_cm`/`angle_deg`
numbers are still returned but should be ignored when `ok` is `false`.

```python
from tools import assess_capture
assess_capture("captures/latest.jpg")
# {"ok": false, "feedback": "Couldn't separate the rock from the background ...",
#  "issues": ["object_not_isolated"]}
```

## Run it

```bash
python tools.py "identify this rock"      # full pipeline, prints JSON
python distance_tool.py test.jpg          # one tool against an image
python angle_tool.py test.jpg
python camera_tool.py                      # capture + describe only
```

## Configuration (env vars)

| Var | Purpose | Default |
|---|---|---|
| `CAMERA_INDEX` | Force a camera index (skip auto-detect) | auto |
| `BUTTERBASE_API_KEY` | `bb_sk_...` key with `ai:gateway` scope — enables real rock IDs | (none) |
| `BUTTERBASE_API_URL` | AI gateway base URL | `https://api.butterbase.ai` |
| `BUTTERBASE_VISION_MODEL` | Vision model for descriptions | `anthropic/claude-haiku-4.5` |

Without `BUTTERBASE_API_KEY`, descriptions fall back to a local OpenCV summary
(color + size) so the pipeline still runs.

### Using a `.env` file

Easiest way to set the key: copy the template and fill it in. `tools.py` and
`camera_tool.py` auto-load `.env` on import (no extra dependency), and real
environment variables still override it.

```bash
cp .env.example .env
# then edit .env and paste your BUTTERBASE_API_KEY=bb_sk_...
```

`.env` is gitignored, so your key is never committed.

## Notes

- **Camera index:** the iPhone (Continuity Camera) is usually index **0** and the
  built-in laptop cam is **1**. Auto-detect can't reliably probe a sleeping
  Continuity Camera, so **pin it** with `CAMERA_INDEX` (set in `.env`).
- **Autofocus:** 10 warmup frames are discarded before saving so close-up shots
  aren't blurry.
- **Light vs dark rocks:** contour detection combines Otsu (both polarities) and
  Canny edges, so quartz and obsidian both work.
- **Distance heuristic:** `distance_cm = (8 * 800) / bbox_width_px`. To
  recalibrate, measure a rock at a known distance and set
  `FOCAL_LENGTH_PX = (distance_cm * bbox_width_px) / 8` in `tools.py`.
