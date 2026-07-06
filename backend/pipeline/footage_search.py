"""
Stage: "AI searches stock footage"

Returns several Candidate options per scene (with remote thumbnail URLs the
frontend can display directly) instead of committing to one — the user picks
or swaps in the review step. The actual video/image file is only downloaded
for whichever candidate ends up selected, at export time.
"""
import os
import uuid
import requests

from .models import Candidate

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"


def _download(url: str, dest_path: str) -> str:
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest_path


def search_pexels(query: str, per_page: int = 4) -> list[Candidate]:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PEXELS_VIDEO_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": "landscape", "per_page": per_page},
        timeout=30,
    )
    resp.raise_for_status()
    videos = resp.json().get("videos", [])

    out = []
    for video in videos:
        files = sorted(video["video_files"], key=lambda f: f.get("width", 0), reverse=True)
        best = next((f for f in files if f.get("width", 0) <= 1920), files[0]) if files else None
        if not best:
            continue
        out.append(
            Candidate(
                id=f"pexels_{video['id']}",
                source="pexels",
                kind="video",
                thumbnail_url=video.get("image"),
                download_url=best["link"],
            )
        )
    return out


def search_pixabay(query: str, per_page: int = 4) -> list[Candidate]:
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PIXABAY_VIDEO_SEARCH_URL,
        params={"key": api_key, "q": query, "per_page": max(per_page, 3)},
        timeout=30,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])

    out = []
    for hit in hits[:per_page]:
        videos = hit.get("videos", {})
        best = videos.get("large") or videos.get("medium") or videos.get("small")
        if not best:
            continue
        out.append(
            Candidate(
                id=f"pixabay_{hit['id']}",
                source="pixabay",
                kind="video",
                thumbnail_url=best.get("thumbnail") or videos.get("tiny", {}).get("thumbnail"),
                download_url=best["url"],
            )
        )
    return out


def find_candidates(query: str, per_source: int = 4) -> list[Candidate]:
    """Returns a merged list of candidate options for the review UI."""
    candidates: list[Candidate] = []
    try:
        candidates += search_pexels(query, per_source)
    except requests.RequestException:
        pass
    try:
        candidates += search_pixabay(query, per_source)
    except requests.RequestException:
        pass
    return candidates


def download_candidate(candidate: Candidate, dest_dir: str, scene_index: int) -> str:
    """Downloads the chosen candidate's actual media file. Called at export
    time, only for the candidate that was actually selected."""
    if candidate.local_path and os.path.exists(candidate.local_path):
        return candidate.local_path

    os.makedirs(dest_dir, exist_ok=True)
    ext = ".mp4" if candidate.kind == "video" else ".jpg"
    dest_path = os.path.join(dest_dir, f"scene_{scene_index:03d}_{candidate.source}_{uuid.uuid4().hex[:6]}{ext}")
    _download(candidate.download_url, dest_path)
    candidate.local_path = dest_path
    return dest_path
