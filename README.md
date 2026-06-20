# Rock-n-Roll — mineral scan demo

iPhone (Continuity Camera) capture + Butterbase backend orchestration for a hackathon rock-scanning demo.

## Architecture

| Component | Role |
|---|---|
| **Frontend** | User enters target mineral, clicks Start scan, polls Butterbase for results |
| **Butterbase functions** (`backend/functions/`) | Session state, vision mineral classification, final guidance |
| **Local worker** (`scan_worker.py`) | Claims sessions, captures from iPhone every N seconds, runs distance/angle |
| **tools.py** | OpenCV camera + geometry (Person A contract) |

## Quick start (demo day)

### 1. Setup

```bash
python3 -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install opencv-python numpy requests
cp .env.example .env            # fill in BUTTERBASE_API_KEY + BUTTERBASE_APP_ID
```

### 2. Deploy / update Butterbase functions (after editing `backend/functions/`)

```bash
python backend/deploy_functions.py
```

### 3. Run the camera worker (Mac with iPhone connected)

```bash
python scan_worker.py
```

### 4. Open the frontend

**Live:** https://rock-n-roll.butterbase.dev

Or open `frontend/index.html` locally. Click **Start scan** after entering a target mineral (e.g. `quartz`, `feldspar`, `mica`).

## Scan workflow

1. Frontend → `scan-start` creates a queued session
2. Worker → `scan-claim` picks up the session
3. Every **2 s** (default): capture → `scan-frame` (vision) until confidence **> 0.5** or **30 s** timeout
4. On stop: two confirmation captures → average their confidences
5. Worker runs **distance** + **angle** on the **last confirmation frame**
6. `scan-finalize` returns result to frontend

### Tunables (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_INTERVAL_SECONDS` | `2` | Time between scan-phase captures |
| `SCAN_TIMEOUT_SECONDS` | `30` | Scan timeout → "No promising rocks found" |
| `CONFIDENCE_STOP_THRESHOLD` | `0.5` | Stop scan when frame confidence exceeds this |
| `CONFIDENCE_ANALYSIS_MIN` | `0.5` | Show "needs further analysis" when avg ≥ this |
| `CONFIDENCE_ANALYSIS_MAX` | `0.7` | Documented relaxed upper band |

Function env vars are synced when you run `backend/deploy_functions.py`.

## Output when a rock matches

```
This rock needs further analysis.
Distance: <cm>
Angle: <deg>
<natural language movement guidance>
```

## Searching multiple minerals

`scan-start` accepts a list of minerals (OR semantics — a hit on **any** one
ends the scan):

```jsonc
POST /fn/scan-start
{ "target_minerals": ["quartz", "pumice", "granite"] }   // up to 5
// legacy still works:
{ "target_mineral": "quartz" }                            // -> ["quartz"]
```

Names are trimmed, lowercased, de-duplicated, and capped at **5**. Each frame
scores the single main rock against **every** requested mineral; the scan stops
the moment any one passes `CONFIDENCE_STOP_THRESHOLD`, and that mineral becomes
the **matched mineral** (confirmation + verdict focus on it).

`scan-status` and `scan-finalize` gain:

| Field | Meaning |
|---|---|
| `target_minerals` | the requested list |
| `matched_mineral` | the mineral that crossed the threshold (null until matched) |
| `per_mineral_confidence` | running `{mineral: best_confidence}` map for the UI |

`target_mineral` (singular) is still present (set to the matched mineral, or the
first requested before a match) for backward compatibility.

## Images in the UI (`scan-status` fields)

The worker uploads frames to **Butterbase Storage** and `scan-status` returns
freshly-minted presigned image URLs (regenerated each poll, so they never
expire on the client). Person B's frontend just sets `<img src>`:

| Field | When | What |
|---|---|---|
| `preview_image_url` | during scan (updates each frame) | latest live camera frame |
| `overlay_image_url` | on `complete` | annotated frame — bbox, min-area rect, distance/angle, OK/RETAKE |
| `final_frame_image_url` | on `complete` | the raw confirmation frame |

Any of these is `null` when no image is available yet. The durable references
are stored as `*_object_id` columns; URLs are minted on demand via the API key
inside `scan-status`, so the browser never needs credentials.

Storage plumbing lives in [storage.py](storage.py) (`upload_image`,
`download_url`); the worker calls it in `scan_worker.py`.

## Original vision tools (still available)

```python
from tools import camera_tool, distance_tool, angle_tool, run_vision_pipeline
```

```bash
python tools.py "identify this rock"
python list_cameras.py
```

## Demo rocks

Quartz, pumice, granite, sandstone — see `mineral_hints.json` for associated searchable minerals.

## Butterbase app

Functions deployed to `app_c78wpyrmhljf`:

- `scan-start`, `scan-claim`, `scan-frame`, `scan-finalize`, `scan-timeout`, `scan-status`

API base: `https://api.butterbase.ai/v1/app_c78wpyrmhljf`

## Notes

- Pin iPhone camera with `CAMERA_INDEX=0` in `.env`
- Worker must run on the Mac with Continuity Camera access
- Do **not** commit `.env` (contains API key)
