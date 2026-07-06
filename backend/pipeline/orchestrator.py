import os
import traceback

from .models import Job
from .scene_splitter import split_script_into_scenes
from .voice_align import align_scenes_to_audio
from .footage_search import find_candidates, download_candidate
from .ai_generator import build_ai_candidate, generate_for_candidate
from .subtitles import build_srt
from .video_assembler import assemble_video
from .tts import generate_voiceover

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")


def job_dir(job: Job) -> str:
    d = os.path.join(STORAGE_DIR, job.job_id)
    os.makedirs(d, exist_ok=True)
    return d


def prepare_pipeline(job: Job, script: str) -> None:
    """
    Runs every stage up to and including building the timeline, then stops
    for user review. Nothing here downloads full-resolution media or spends
    money on AI generation — it only fetches cheap remote thumbnail URLs so
    the review UI has something to show.
    """
    try:
        jobdir = job_dir(job)

        if not job.voiceover_path:
            job.status, job.message, job.progress = "voiceover", f"Generating AI voiceover ({job.voice_provider})", 0.03
            generated_path = os.path.join(jobdir, "generated_voiceover.mp3")
            generate_voiceover(job.voice_provider, script, generated_path, voice=job.voice_id)
            job.voiceover_path = generated_path

        job.status, job.message, job.progress = "splitting", "Splitting script into scenes and tagging keywords/emotion", 0.1
        job.scenes = split_script_into_scenes(script)

        job.status, job.message, job.progress = "timing", "Aligning scenes to voiceover timing", 0.3
        job.scenes = align_scenes_to_audio(job.scenes, job.voiceover_path)

        job.status, job.message, job.progress = "sourcing", "Searching stock footage candidates", 0.45
        total = len(job.scenes)
        for i, scene in enumerate(job.scenes):
            if scene.visual_type in ("stock_footage", "stock_image"):
                candidates = find_candidates(scene.search_query)
                scene.candidates = candidates
                if candidates:
                    scene.selected_candidate_id = candidates[0].id
                else:
                    # nothing found in stock -> offer an AI placeholder instead
                    scene.visual_type = "ai_image"

            if scene.visual_type in ("ai_video", "ai_image") and not scene.candidates:
                ai_candidate = build_ai_candidate(scene.search_query, job.ai_quality)
                scene.candidates = [ai_candidate]
                scene.selected_candidate_id = ai_candidate.id

            job.progress = 0.45 + 0.35 * ((i + 1) / total)
            job.message = f"Sourced candidates for scene {i + 1}/{total}"

        job.status, job.message, job.progress = "ready_for_review", "Timeline ready for review", 0.85

    except Exception as e:
        job.status = "error"
        job.error = f"{e}\n{traceback.format_exc()}"
        job.message = f"Failed: {e}"


def export_pipeline(job: Job) -> None:
    """
    Runs after the user has reviewed/edited the timeline: downloads or
    generates whichever candidate is selected per scene, rebuilds subtitles
    from the (possibly edited) text, and renders the final video with music
    mixed in silently.
    """
    try:
        jobdir = job_dir(job)
        assets_dir = os.path.join(jobdir, "assets")
        total = len(job.scenes)

        job.status, job.message, job.progress = "exporting", "Fetching selected clips", 0.05
        for i, scene in enumerate(job.scenes):
            candidate = scene.selected_candidate()
            if candidate is None:
                raise RuntimeError(f"Scene {scene.index} has no selected candidate")

            if candidate.source == "ai":
                scene.asset_path = generate_for_candidate(candidate, assets_dir, scene.index, ai_quality=job.ai_quality)
            else:
                scene.asset_path = download_candidate(candidate, assets_dir, scene.index)

            job.progress = 0.05 + 0.35 * ((i + 1) / total)
            job.message = f"Fetched asset for scene {i + 1}/{total}"

        job.status, job.message, job.progress = "assembling", "Building subtitle file", 0.45
        srt_path = os.path.join(jobdir, "subtitles.srt")
        build_srt(job.scenes, srt_path)

        job.status, job.message, job.progress = "assembling", "Rendering scenes, mixing music, exporting", 0.55
        output_path = os.path.join(jobdir, "output.mp4")
        assemble_video(
            scenes=job.scenes,
            voiceover_path=job.voiceover_path,
            srt_path=srt_path,
            output_path=output_path,
            music_path=job.music_path,
            workdir=os.path.join(jobdir, "render"),
        )

        job.output_path = output_path
        job.status, job.message, job.progress = "done", "Export complete", 1.0

    except Exception as e:
        job.status = "error"
        job.error = f"{e}\n{traceback.format_exc()}"
        job.message = f"Failed: {e}"
