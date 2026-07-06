"""
Stages: Script -> split into scenes -> AI extracts keywords -> AI analyzes
emotion -> choose visual type.

These are conceptually separate stages in the product workflow, but they're
all facts about the same piece of text, so one structured Claude call
produces all of them together rather than four separate round trips.
"""
import json
import os
import re
import requests

from .models import Scene

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

EMOTIONS = ["neutral", "calm", "joyful", "tense", "sad", "dramatic", "exciting", "mysterious"]

SYSTEM_PROMPT = f"""You are a video pre-production assistant. You will be given a voiceover \
script. Split it into short visual "scenes" (roughly one beat/idea per scene, usually \
1-2 sentences, never more than ~12 seconds of spoken narration).

For every scene, analyze it and return:
1. "keywords": 3-6 concrete visual nouns/phrases for this scene (for a stock footage
   search engine like Pexels/Pixabay) — e.g. ["city skyline", "rush hour traffic"].
2. "emotion": the dominant emotional tone of this scene, one of: {", ".join(EMOTIONS)}.
3. "visual_type": one of "stock_footage", "stock_image", "ai_video", "ai_image".
   - Prefer stock_footage/stock_image for concrete, literal, commonly-filmed subjects.
   - Use ai_video/ai_image only for abstract concepts, imagined scenarios, specific
     fictional scenes, data visualizations, or anything unlikely to exist as real footage.
4. "search_query": a short string combining the keywords into a search query if
   visual_type is stock_*, or a descriptive generation prompt if ai_*.
5. "motion": one of "zoom_in", "zoom_out", "pan_left", "pan_right", "static" — pick
   whichever best fits the emotion/pacing (e.g. tense/exciting -> zoom_in, calm -> static
   or slow pan).

Return ONLY a JSON array (no prose, no markdown fences), one object per scene:
[{{"text": "...", "keywords": [...], "emotion": "...", "visual_type": "...", "search_query": "...", "motion": "..."}}]

The concatenation of all "text" fields must reconstruct the original script exactly
(same words, same order) so it can be matched back to the voiceover audio.
"""


def _call_claude(script: str) -> list[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": script}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "".join(text_blocks).strip()

    raw = re.sub(r"^```(json)?", "", raw.strip())
    raw = re.sub(r"```$", "", raw.strip())

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Scene splitter returned non-JSON output: {e}\nRaw: {raw[:500]}")


def split_script_into_scenes(script: str) -> list[Scene]:
    """
    Splits a raw script into Scene objects with keywords/emotion/visual_type/
    search_query/motion already assigned. Timing (start/end) and candidates
    are filled in by later stages.
    """
    script = script.strip()
    if not script:
        raise ValueError("Script is empty")

    raw_scenes = _call_claude(script)

    scenes: list[Scene] = []
    for i, item in enumerate(raw_scenes):
        emotion = item.get("emotion", "neutral")
        if emotion not in EMOTIONS:
            emotion = "neutral"

        scenes.append(
            Scene(
                index=i,
                text=item["text"].strip(),
                keywords=[k.strip() for k in item.get("keywords", []) if k.strip()],
                emotion=emotion,
                visual_type=item.get("visual_type", "stock_footage"),
                search_query=item.get("search_query", "").strip(),
                motion=item.get("motion", "static"),
                subtitle_text=item["text"].strip(),
            )
        )

    if not scenes:
        raise RuntimeError("Scene splitter returned zero scenes")

    return scenes
