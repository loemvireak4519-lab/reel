"""
Stage: "Generate AI image/video if needed"

Used when a scene is tagged ai_image/ai_video by scene_splitter, or when
footage_search finds no usable stock clip. The user picks a quality tier
that determines which model actually generates the visual:
  - "fast"  -> Stability Core (image, cheap/quick)
  - "high"  -> Stability Ultra (image, higher quality, slower/costlier)
  - "video" -> Runway (actual video generation, slowest/priciest)

NOTE: Runway's API surface changes between versions — this targets their
async task-based Gen-3 style API (POST to start, GET to poll). If your
account uses a different API version, adjust RUNWAY_BASE_URL and the
request/response shape accordingly; everything else in the pipeline is
decoupled from this detail (it just wants a local file path back).
"""
import os
import time
import uuid
import requests

from .models import Candidate

STABILITY_CORE_URL = "https://api.stability.ai/v2beta/stable-image/generate/core"
STABILITY_ULTRA_URL = "https://api.stability.ai/v2beta/stable-image/generate/ultra"
RUNWAY_BASE_URL = "https://api.dev.runwayml.com/v1"

QUALITY_TIERS = ("fast", "high", "video")
DEFAULT_QUALITY = "fast"


def build_ai_candidate(prompt: str, ai_quality: str = DEFAULT_QUALITY) -> Candidate:
    """A placeholder candidate shown in the review UI for AI-sourced scenes.
    Nothing is generated yet — the review panel just shows the prompt text
    ('AI will generate: ...') until export, when the actual media is made."""
    kind = "video" if ai_quality == "video" else "image"
    return Candidate(
        id=f"ai_{uuid.uuid4().hex[:8]}",
        source="ai",
        kind=kind,
        thumbnail_url=None,
        download_url=None,
        prompt=prompt,
    )


def generate_ai_image(prompt: str, dest_path: str, tier: str = "fast") -> str:
    api_key = os.environ.get("STABILITY_API_KEY")
    if not api_key:
        raise RuntimeError("STABILITY_API_KEY is not set")

    url = STABILITY_ULTRA_URL if tier == "high" else STABILITY_CORE_URL
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "image/*"},
        files={"none": ""},
        data={"prompt": prompt, "output_format": "png", "aspect_ratio": "16:9"},
        timeout=120,
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return dest_path


def generate_ai_video(prompt: str, dest_path: str, duration: int = 5, poll_interval: int = 5, timeout: int = 300) -> str:
    api_key = os.environ.get("RUNWAY_API_KEY")
    if not api_key:
        raise RuntimeError("RUNWAY_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Runway-Version": "2024-11-06",
        "Content-Type": "application/json",
    }

    start = requests.post(
        f"{RUNWAY_BASE_URL}/text_to_video",
        headers=headers,
        json={"promptText": prompt, "duration": duration, "ratio": "1280:768"},
        timeout=30,
    )
    start.raise_for_status()
    task_id = start.json()["id"]

    elapsed = 0
    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval
        poll = requests.get(f"{RUNWAY_BASE_URL}/tasks/{task_id}", headers=headers, timeout=30)
        poll.raise_for_status()
        status = poll.json()
        if status.get("status") == "SUCCEEDED":
            video_url = status["output"][0]
            with requests.get(video_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            return dest_path
        if status.get("status") == "FAILED":
            raise RuntimeError(f"Runway generation failed: {status.get('failure')}")

    raise TimeoutError(f"Runway generation for task {task_id} did not finish within {timeout}s")


def generate_asset_for_scene(ai_quality: str, prompt: str, dest_dir: str, scene_index: int) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    if ai_quality == "video":
        dest_path = os.path.join(dest_dir, f"scene_{scene_index:03d}_ai.mp4")
        return generate_ai_video(prompt, dest_path)
    else:
        dest_path = os.path.join(dest_dir, f"scene_{scene_index:03d}_ai.png")
        return generate_ai_image(prompt, dest_path, tier=ai_quality)


def generate_for_candidate(candidate: Candidate, dest_dir: str, scene_index: int, ai_quality: str = DEFAULT_QUALITY) -> str:
    """Generates the actual media for an 'ai' candidate at export time and
    fills in candidate.local_path. Called only for whichever candidate the
    user has selected — never during the review phase."""
    if candidate.local_path and os.path.exists(candidate.local_path):
        return candidate.local_path
    path = generate_asset_for_scene(
        ai_quality,
        candidate.prompt or "",
        dest_dir,
        scene_index,
    )
    candidate.local_path = path
    return path
