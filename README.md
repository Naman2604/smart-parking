# Smart Parking Control Center

Live parking-lot monitoring: **YOLO26** detects vehicles in a camera video, matches them to
parking slots, and a **Next.js** dashboard shows the annotated video, free/occupied counts and a
slot grid in real time.

```
 parking.mp4 ──► backend (Flask + YOLO26 via ONNX Runtime) ──► /video_feed  /api/stats  /api/slots
                                                                   │
                                          frontend (Next.js dashboard, polls every second)
```

## Project layout

```
backend/
  app.py               Flask app + background video thread (routes below)
  detection.py         YOLO26 detection, slot matching, smoothing, drawing
  analytics.py         Thread-safe stats shared by the thread and the API
  calibrate_slots.py   Finds the parking slots in a video -> slots.json
  slots.json           Slot rectangles + the video frame range they apply to
  models/yolo26n-960.onnx   YOLO26 nano, NMS-free, 576x960 input (default)
  models/yolo26n-1280.onnx  Same model at 768x1280 - spots more distant cars, ~100 MB more RAM
  slots-1280.json           Slots calibrated for the 1280 model (43 instead of 37)
  parking.mp4          Sample footage
  requirements.txt
frontend/              Next.js dashboard (cards, video panel, slot grid, charts, insights)
render.yaml            Optional Render blueprint for the backend
```

## Run it locally

**Backend** (Python 3.10+):
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                                          # http://localhost:5000
```
Check `http://localhost:5000/health` (wait for `"status": "ok"`), then `/api/stats` and `/video_feed`.

**Frontend** (Node 20+), in a second terminal:
```bash
cd frontend
cp .env.example .env.local        # points the dashboard at http://127.0.0.1:5000
npm install
npm run dev                       # http://localhost:3000
```

## API

| Endpoint | Returns |
|---|---|
| `GET /video_feed` | MJPEG stream of the annotated video |
| `GET /api/stats` | `total_slots`, `occupied`, `available`, `occupancy_rate` (%), `updated_at`, and `zones` (utilization per area of the lot) |
| `GET /api/slots` | `[{"id": "P1", "zone": "Back Left", "occupied": true}, ...]` |
| `GET /api/history?points=120` | Recorded occupancy over time, oldest first: `[{"t": <unix seconds>, "occupied", "available", "occupancy_rate"}, ...]` |
| `GET /api/insights` | Peak time, average occupancy, trend (`collecting`/`increasing`/`decreasing`/`stable`) and rule-based alerts, all computed from the recorded history |
| `GET /health` | Always HTTP 200. `status`, `stage` (`loading_model` → `warming_up` → `opening_video` → `running`), `stage_seconds`, `uptime_seconds`, `slots_loaded`, `detections_run`, `restarts`, `thread_alive`, `worker_pid`, `last_error`, `cpu`, `low_cpu_mode`, and — if a startup stage takes over a minute — `stuck: true` plus `stuck_trace` showing the exact line it stopped on |

The very first request to a freshly started server is what starts the background worker,
so that first response shows `stage: loading_model`. Request it again a few seconds later.

## Deploy

**Backend on Render** — New → Web Service → connect the repo, then:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --workers 1 --threads 16 --timeout 120 --bind 0.0.0.0:$PORT` |
| Health Check Path | `/health` |

(or use `render.yaml` as a Blueprint). Keep `--workers 1`; more threads are fine, since every open
video stream uses one. The app peaks at about 240 MB of RAM, so Render's free 512 MB plan is enough,
though not by a huge margin (about 330 MB of 512 MB). Free instances sleep when idle, and `/health` answers while the app is
still starting — so a freshly woken service shows `stage: loading_model` for the first few seconds.
Check `uptime_seconds` before assuming it is stuck.

**Frontend on Vercel** — import the repo, set **Root Directory** to `frontend`, add the environment
variable `NEXT_PUBLIC_API_URL` = your Render URL (https, no trailing slash), then deploy. Redeploy
after changing the variable; it is read at build time.

## Configuration (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `0.10` | Minimum detection score. YOLO26 scores run low, so ~0.10 is normal |
| `DETECT_INTERVAL` | `0.3` | Seconds between detections (raise to use less CPU) |
| `STREAM_FPS` | `15` | Video frames per second sent to browsers |
| `DEBOUNCE_DETECTIONS` | `5` | Detections in a row before a slot changes state (reduces flicker) |
| `SLOT_IOU_THRESHOLD` | `0.4` | How much a car must overlap a slot to count |
| `ORT_THREADS` | `1` | CPU threads for the model (raise on a bigger machine) |
| `CPU_QUOTA_CORES` | auto | Override the detected CPU allowance if auto-detection fails |
| `CV_THREADS` | `1` | CPU threads for OpenCV |
| `STUCK_AFTER` | `60` | Seconds before a stalled startup stage is reported in `/health` |
| `HISTORY_INTERVAL` | `5` | Seconds between recorded history points |
| `HISTORY_MAX_POINTS` | `720` | History points kept (720 x 5 s = the last hour) |

**Detection accuracy.** The model runs the video (640x360) at a larger input size, which is what
lets it pick out cars parked further from the camera. Two sizes are bundled:

| Model | Slots found | Peak RAM | Notes |
|---|---|---|---|
| `yolo26n-960.onnx` (default) | 37 | ~330 MB | Safe on a 512 MB free instance |
| `yolo26n-1280.onnx` | 43 | ~430 MB | Better on distant cars; close to the 512 MB limit |

To switch, set **both** environment variables, because slots are learned from what a given model
sees and the two sets are not interchangeable:

```
YOLO_MODEL=models/yolo26n-1280.onnx
SLOTS_FILE=slots-1280.json
```

Cars parked where no slot was ever calibrated are outlined in blue rather than counted. Rows of the
lot that were empty for the whole calibration video get no slots at all, which is why the count is
37 rather than the lot's full capacity.

**Small-CPU mode.** Free instances often allow only a fraction of one core (Render's free plan
gives about 0.15) while reporting 8 CPUs. When the app detects an allowance under half a core it
automatically detects once every 3 s and streams 4 fps instead of 0.3 s and 15 fps, and needs a
change confirmed twice instead of five times so updates still appear within about 6 seconds.
`/health` shows this as `low_cpu_mode: true`. Any value you set yourself always wins.

## Using your own video

1. Put it in `backend/` and set `VIDEO_PATH` (or replace `parking.mp4`).
2. `cd backend && python calibrate_slots.py --video your.mp4 --end <last frame of one camera view>`
3. Open the generated `slots_preview.jpg` and check the boxes sit on the parking spaces.

Slots are fixed rectangles, so they only fit **one camera view**. The bundled video changes
camera angle after about frame 4650, so `slots.json` records the valid range and the app loops only
that part. The script warns when it spots a possible camera cut.

## Known limitations

- **Accuracy:** YOLO26n is the smallest model. On this footage it sometimes misses dark cars in
  shadow and splits a large truck into two slots. A bigger model (e.g. YOLO26s) is more accurate
  but several times slower; export it with
  `YOLO('yolo26s.pt').export(format='onnx', imgsz=(384, 640), opset=12, simplify=True, end2end=True)`,
  point `YOLO_MODEL` at it, and re-run `calibrate_slots.py`.
- **History is in memory only:** the usage chart, peak time, average and trend are computed from
  readings recorded while the server runs. They start empty after every restart or wake-up from
  sleep (free hosting does this often) and fill up again; the trend needs about 45 seconds of data.
- **Demo video loops:** with the bundled `parking.mp4` the "history" is the same ~2.5-minute clip
  repeating, so the chart and trend rise and fall with that loop. With a real camera feed they
  show real activity.
- **Areas, not zones:** the area chart groups the detected slots by position in the camera view
  (back/middle/front x left/right). These are not marked parking zones.
- **Insights are plain statistics, not predictions:** peak = busiest recorded moment, trend =
  recent occupancy vs earliest occupancy, alerts = fixed rules (lot or area above 90%, lot full,
  detection stalled). Nothing forecasts future demand.
- **Slots come from the video itself:** `calibrate_slots.py` only finds spaces where a car was
  parked at some point, so spaces that are always empty are not tracked.

## Troubleshooting

Open `/health` first.
- `stage: loading_model` for a long time → the server is still starting (slow free CPU); wait.
- `stage: error` → read `last_error`.
- `slots_loaded: 0` → `slots.json` is missing; run `calibrate_slots.py`.
- Dashboard shows 0 → wrong `NEXT_PUBLIC_API_URL`, or the backend is asleep.
