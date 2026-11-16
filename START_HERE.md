# START HERE - get the Smart Parking project running and online

What you have: a **backend** (Python, in `backend/`) that watches a parking video with YOLO26 and counts
free/occupied spaces, and a **dashboard** (Next.js, in `frontend/`) that shows it live.

**It works with a recorded video file** (`backend/parking.mp4` or your own). It does **not** read live CCTV
(RTSP) streams or still images yet.

You will put the backend on **Render** and the dashboard on **Vercel**, both from one GitHub repo.
Accounts needed (all free): GitHub, Render, Vercel.

---------------------------------------------------------------------------------------------------

## Step 1 - Unzip
Double-click `smart-parking.zip`. You get a folder `smart-parking` containing `backend`, `frontend`,
`README.md`, `render.yaml` and this file.

## Step 2 (optional but recommended) - Test on your computer
Needs Python 3.10+ and Node 20+. Open Terminal and run:

    cd ~/Downloads/smart-parking/backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python app.py

Open http://localhost:5000/health in your browser. Wait until it says  "status": "ok"  (about 10-60 seconds).
Then open http://localhost:5000/video_feed - you should see the video with green/red boxes.
Stop it with Ctrl+C. (If you skip this step, nothing is lost.)

Dashboard locally (new Terminal window, leave the backend running):

    cd ~/Downloads/smart-parking/frontend
    cp .env.example .env.local
    npm install
    npm run dev

Open http://localhost:3000

## Step 3 - Put the project on GitHub (new repository)
1. github.com -> **+** (top right) -> **New repository** -> name it `smart-parking`.
   Leave "Add a README", ".gitignore" and "license" UNTICKED. Click **Create repository**.
2. In Terminal:

       cd ~/Downloads/smart-parking
       git init
       git add -A
       git commit -m "Smart parking with YOLO26"
       git branch -M main
       git remote add origin https://github.com/YOUR-USERNAME/smart-parking.git
       git push -u origin main

   - A macOS window asking for your keychain password: type your **Mac login password**, click Allow.
   - If git asks for a password in the terminal, use a GitHub **Personal Access Token**
     (GitHub -> Settings -> Developer settings -> Personal access tokens -> Tokens (classic) -> tick `repo`).
3. Refresh the repo page: you should see `backend`, `frontend`, `README.md`, `render.yaml`.

## Step 4 - Backend on Render
1. render.com -> **New +** -> **Web Service** -> connect GitHub -> choose `smart-parking`.
2. Fill in exactly:

   | Field            | Value                                                          |
   |------------------|----------------------------------------------------------------|
   | Root Directory   | `backend`                                                      |
   | Runtime          | Python 3                                                       |
   | Build Command    | `pip install -r requirements.txt`                              |
   | Start Command    | `gunicorn app:app --workers 1 --threads 16 --timeout 120 --bind 0.0.0.0:$PORT` |
   | Instance Type    | Free                                                           |
   | Health Check Path| `/health` (under Advanced)                                     |

3. Click **Create Web Service**. The first build takes a few minutes.
4. When it says "Live", open  https://YOUR-SERVICE.onrender.com/health

   IMPORTANT - how to read this page. The FIRST request to a sleeping or just-deployed server is
   what starts the detection worker, so the first thing you see is almost always:
       "stage":"loading_model", "slots_loaded":0, "detections_run":0
   That is normal. **Refresh once after ~10 seconds.** You should then get
   "stage":"running", "status":"ok", "slots_loaded":37.

   If it still says loading_model after a minute, look at `stage_seconds` and `stuck`. When
   `"stuck":true` the page also contains `stuck_trace`, naming the exact line it stopped on.
   Copy the whole page and send it - that identifies the cause without the Render logs.
5. Copy your service URL (https://YOUR-SERVICE.onrender.com) - you need it in Step 5.

If the build fails with a Python/numpy/onnxruntime error: Render -> your service -> **Environment** ->
add `PYTHON_VERSION` = `3.12.3`, then redeploy. (The project was tested on Python 3.12.)

## Step 5 - Dashboard on Vercel
1. vercel.com -> **Add New...** -> **Project** -> import `smart-parking`.
2. **Root Directory** -> click Edit -> choose `frontend`. (Framework: Next.js is detected automatically.)
3. **Environment Variables** -> add:
   - Name:  `NEXT_PUBLIC_API_URL`
   - Value: your Render URL from Step 4 (starts with https://, NO slash at the end)
4. Click **Deploy**. When finished, open the Vercel link.
   (If you change the variable later you MUST redeploy: Deployments -> ... -> Redeploy.)

## Step 6 - Check that it works
On the dashboard you should see: real numbers in the four cards (37 total slots), the video with
boxes, a grid of 32 slots, header "System Active" and "1/1 Camera Online". The usage chart and insights
fill in over the first minute.

First load after the backend has been idle can take a minute or more (free Render sleeps). The dashboard
shows "Backend Offline"/"Connecting" meanwhile and recovers by itself.

## If something is wrong
Open  https://YOUR-SERVICE.onrender.com/health  first:

| What you see                                   | Meaning / fix                                                              |
|------------------------------------------------|----------------------------------------------------------------------------|
| Page loads very slowly, then works             | Render was asleep. Normal on the free plan.                                |
| `"stage":"loading_model"` on the first load     | Normal - the first request starts the worker. Refresh after ~10s.          |
| `"stuck": true` (and `stage_seconds` 60+)      | Really stuck. Copy the whole /health page (it contains `stuck_trace`) and send it. |
| `"stage": "error"`                             | Read `last_error` in the same page.                                        |
| `"slots_loaded": 0`                            | `backend/slots.json` is missing from the repo.                             |
| Render says "out of memory" / keeps restarting | App peaks around 330 MB of the 512 MB free limit. Send me the log.             |
| Dashboard cards show 0                         | Wrong `NEXT_PUBLIC_API_URL`, or you did not redeploy Vercel after setting it.|
| Dashboard works but is slow/choppy             | Render -> Environment: add `DETECT_INTERVAL=1.0` and `STREAM_FPS=5`.       |

## Step 7 - Remove the OLD project (only after the new one works)
Deleting cannot be undone, so do this last:
1. Vercel: old project -> Settings -> General -> Delete Project.
2. Render: old `pbl_project` service -> Settings -> Delete Web Service.
3. GitHub: old `pbl_project` repo -> Settings -> Danger Zone -> Delete this repository.
4. Your Mac: move the old `pbl_project` folder to the Trash (not `rm -rf`).

## Using your own video
1. Copy it into `backend/` (e.g. `mylot.mp4`).
2. Edit nothing in code: set the environment variable `VIDEO_PATH=mylot.mp4` (Render -> Environment).
   Locally:  cd backend && python calibrate_slots.py --video mylot.mp4
3. Open the generated `slots_preview.jpg` and check the boxes sit on parking spaces.
   Use `--end <frame>` if the camera view changes partway through the video.
4. Commit and push the new `slots.json` and the video (GitHub accepts files up to 100 MB).
Tips: a fixed camera, high and close to the lot, 1080p, daylight, gives the best results.

More detail (API, settings, limitations): see `README.md`.
