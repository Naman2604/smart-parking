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
  models/yolo26n.onnx  YOLO26 nano, exported NMS-free, 384x640 input
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
| `GET /health` | Always HTTP 200. `status`, `stage` (`loading_model` → `opening_video` → `running`), `slots_loaded`, `detections_run`, `last_error` |

## Deploy

**Backend on Render** — New → Web Service → connect the repo, then:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --workers 1 --threads 16 --timeout 120` |
| Health Check Path | `/health` |

(or use `render.yaml` as a Blueprint). Keep `--workers 1`; more threads are fine, since every open
video stream uses one. The app needs roughly 180 MB of RAM, so Render's free 512 MB plan is enough.
Free instances sleep when idle and take a minute or more to wake up.

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
| `ORT_THREADS` | `2` | CPU threads for the model |
| `HISTORY_INTERVAL` | `5` | Seconds between recorded history points |
| `HISTORY_MAX_POINTS` | `720` | History points kept (720 x 5 s = the last hour) |

If Render is slow, set `DETECT_INTERVAL=1.0` and `STREAM_FPS=5`.

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
