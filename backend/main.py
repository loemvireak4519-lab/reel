import os
import uuid
import base64
import threading

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from pipeline.models import Job
from pipeline.orchestrator import prepare_pipeline, export_pipeline, STORAGE_DIR
from pipeline.footage_search import find_candidates
from pipeline.youtube_import import generate_script_from_youtube
from pipeline.voices import list_voices

app = FastAPI(title="Video Pipeline API")

# ---- Access gate ----------------------------------------------------------
# Set ACCESS_PASSWORD when deploying this somewhere reachable by other people.
# Every visitor is prompted for a username (anything) + this password before
# the site loads, so a shared link can't quietly burn through your Anthropic/
# OpenAI/Pexels/Runway credits. Leave ACCESS_PASSWORD unset for local-only
# use and the gate is skipped entirely.
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ACCESS_PASSWORD:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                _, _, password = decoded.partition(":")
                if password == ACCESS_PASSWORD:
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Reel"'},
        )


app.add_middleware(BasicAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict[str, Job] = {}
os.makedirs(STORAGE_DIR, exist_ok=True)


def _save_upload(upload: UploadFile, dest_path: str) -> str:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(upload.file.read())
    return dest_path


def _get_job(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ---- YouTube import: extract transcript, have Claude write an original script ----

@app.post("/api/youtube/rewrite")
async def youtube_rewrite(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")

    try:
        script = generate_script_from_youtube(url)
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"script": script}


# ---- Voice listing: real voices for the picker, not a raw ID field ----

@app.get("/api/voices")
async def get_voices(provider: str, language_code: str = "en-US"):
    try:
        voices = list_voices(provider, language_code=language_code)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"voices": voices}


# ---- Prepare: upload script + voiceover, run stages up to review ----------

@app.post("/api/jobs")
async def create_job(
    script: str = Form(...),
    voiceover: UploadFile | None = File(None),
    music: UploadFile | None = File(None),
    voice_provider: str | None = Form(None),
    voice_id: str | None = Form(None),
    ai_quality: str = Form("fast"),
):
    if voiceover is None and not voice_provider:
        raise HTTPException(400, "Upload a voiceover file or choose an AI voice provider")

    job_id = uuid.uuid4().hex[:12]
    jobdir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(jobdir, exist_ok=True)

    voiceover_path = None
    if voiceover is not None:
        voiceover_path = _save_upload(voiceover, os.path.join(jobdir, "voiceover_" + voiceover.filename))

    music_path = None
    if music is not None:
        music_path = _save_upload(music, os.path.join(jobdir, "music_" + music.filename))

    job = Job(
        job_id=job_id,
        voiceover_path=voiceover_path,
        music_path=music_path,
        voice_provider=voice_provider,
        voice_id=voice_id,
        ai_quality=ai_quality,
    )
    JOBS[job_id] = job

    threading.Thread(target=prepare_pipeline, args=(job, script), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_job(job_id).to_dict()


# ---- Review: edit scenes, swap clips, re-search --------------------------

@app.patch("/api/jobs/{job_id}/scenes/{scene_index}")
async def update_scene(job_id: str, scene_index: int, updates: dict = Body(...)):
    """Body may include any of: subtitle_text, motion, emotion, start, end,
    selected_candidate_id. Only provided fields are changed."""
    job = _get_job(job_id)
    scene = job.get_scene(scene_index)

    allowed = {"subtitle_text", "motion", "emotion", "start", "end", "selected_candidate_id"}
    for key, value in updates.items():
        if key in allowed:
            setattr(scene, key, value)

    return scene.to_dict()


@app.post("/api/jobs/{job_id}/scenes/{scene_index}/search")
async def search_scene_candidates(job_id: str, scene_index: int, body: dict = Body(...)):
    """Re-searches stock footage with a custom query and appends results to
    the scene's candidate list (existing candidates, and the current
    selection, are left untouched)."""
    job = _get_job(job_id)
    scene = job.get_scene(scene_index)
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    new_candidates = find_candidates(query)
    existing_ids = {c.id for c in scene.candidates}
    for c in new_candidates:
        if c.id not in existing_ids:
            scene.candidates.append(c)

    return {"candidates": [c.to_dict() for c in scene.candidates]}


# ---- Export: fetch selected assets, render final video --------------------

@app.post("/api/jobs/{job_id}/export")
async def export_job(job_id: str):
    job = _get_job(job_id)
    if job.status not in ("ready_for_review", "done", "error"):
        raise HTTPException(409, f"Job is not ready to export (status: {job.status})")

    threading.Thread(target=export_pipeline, args=(job,), daemon=True).start()
    return {"status": "exporting"}


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = _get_job(job_id)
    if not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(404, "Output not ready")
    return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job_id}.mp4")


# Serve the frontend as static files so the whole thing runs from one process
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
