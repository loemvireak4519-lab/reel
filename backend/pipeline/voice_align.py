"""
Stage: "Match with voice timing"

Sends the uploaded voiceover to OpenAI's Whisper transcription endpoint with
word-level timestamps, then aligns those words back onto the scene texts
produced by scene_splitter (which together reconstruct the full script).

This lets scenes come from clean literary text (proper punctuation/casing)
while timing comes from what was actually spoken.
"""
import os
import re
import requests

from .models import Scene

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


def _normalize(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def transcribe_with_word_timestamps(audio_path: str) -> list[dict]:
    """Returns a flat list of {"word": str, "start": float, "end": float}."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    with open(audio_path, "rb") as f:
        resp = requests.post(
            OPENAI_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(audio_path), f)},
            data={
                "model": "whisper-1",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            },
            timeout=300,
        )
    resp.raise_for_status()
    data = resp.json()
    words = data.get("words")
    if words:
        return words

    # Fallback: some API versions nest under "segments" -> "words"
    out = []
    for seg in data.get("segments", []):
        out.extend(seg.get("words", []))
    if not out:
        raise RuntimeError(
            "Transcription returned no word-level timestamps. "
            "Check that the audio is non-empty and OPENAI_API_KEY has access to whisper-1."
        )
    return out


def align_scenes_to_audio(scenes: list[Scene], audio_path: str) -> list[Scene]:
    """
    Mutates and returns `scenes` with `start`/`end` filled in, by greedily
    consuming transcribed words in order to match each scene's text.
    """
    words = transcribe_with_word_timestamps(audio_path)
    norm_words = [_normalize(w["word"]) for w in words]

    cursor = 0
    n = len(words)

    for scene in scenes:
        scene_words = [w for w in re.findall(r"[A-Za-z0-9']+", scene.text)]
        norm_scene_words = [_normalize(w) for w in scene_words if _normalize(w)]

        if not norm_scene_words:
            # Empty/punctuation-only scene text — keep zero-length, will be
            # merged into neighbor's timing at assembly time.
            continue

        start_idx = cursor
        matched = 0
        j = cursor
        # Walk forward through the transcript trying to match this scene's words
        # in order. We don't require perfect alignment (ASR punctuation/casing
        # differs from the script) — we match on normalized word tokens and
        # allow the transcript to have extra filler words in between.
        target_len = len(norm_scene_words)
        k = 0
        while j < n and k < target_len:
            if norm_words[j] == norm_scene_words[k] or not norm_words[j]:
                k += 1
                matched += 1
            j += 1

        end_idx = max(j - 1, start_idx)
        end_idx = min(end_idx, n - 1)

        scene.start = words[start_idx]["start"] if n > 0 else 0.0
        scene.end = words[end_idx]["end"] if n > 0 else 0.0

        if scene.end <= scene.start:
            scene.end = scene.start + max(0.5, len(scene.text.split()) * 0.35)

        cursor = min(j, n - 1) if j < n else n - 1

    # Guard against any overlap/zero-length weirdness from imperfect matching
    for i in range(1, len(scenes)):
        if scenes[i].start < scenes[i - 1].end:
            scenes[i].start = scenes[i - 1].end

    return scenes
