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
