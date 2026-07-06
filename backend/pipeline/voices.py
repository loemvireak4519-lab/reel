"""
Powers the "pick a real voice" dropdown in the review UI, instead of asking
the user to paste a raw voice ID they'd have to look up themselves.

ElevenLabs voices come with a free, pre-recorded preview_url — no generation
needed. Google Cloud TTS doesn't offer pre-recorded previews via its API, so
previewing a Google voice means synthesizing a short sample on demand (see
generate_preview_sample below) — a small real cost per preview click, unlike
the free ElevenLabs previews.
"""
import os
import requests

ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
GOOGLE_VOICES_URL = "https://texttospeech.googleapis.com/v1/voices"

PREVIEW_SAMPLE_TEXT = "Hello, this is a preview of this voice."


def list_elevenlabs_voices() -> list[dict]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    resp = requests.get(
        ELEVENLABS_VOICES_URL,
        headers={"xi-api-key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    voices = resp.json().get("voices", [])

    return [
        {
            "id": v["voice_id"],
            "name": v.get("name", "Unnamed voice"),
            "preview_url": v.get("preview_url"),
            "description": (v.get("labels") or {}).get("description")
            or (v.get("labels") or {}).get("accent")
            or "",
        }
        for v in voices
    ]


def list_google_voices(language_code: str = "en-US") -> list[dict]:
    api_key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_TTS_API_KEY is not set")

    resp = requests.get(
        GOOGLE_VOICES_URL,
        params={"key": api_key, "languageCode": language_code},
        timeout=30,
    )
    resp.raise_for_status()
    voices = resp.json().get("voices", [])

    # Ranked roughly by how human/natural each tier actually sounds (and,
    # not coincidentally, roughly by price): Studio and Chirp3-HD are the
    # most natural — Chirp3-HD in particular adds real disfluencies and
    # emotional intonation. Gemini-TTS models (when available on the
    # account) support natural-language style prompts and are newer still.
    # Older tiers were previously all lumped into "Standard" once they
    # didn't match Studio/Neural2/Wavenet, which mislabeled genuinely
    # natural-sounding Chirp3-HD voices as the lowest tier.
    def _tier(name: str) -> tuple[int, str]:
        if "Gemini" in name:
            return (0, "Gemini-TTS (natural-language style control)")
        if "Studio" in name:
            return (1, "Studio (most natural)")
        if "Chirp3-HD" in name or "Chirp-HD" in name:
            return (1, "Chirp3-HD (very natural, human-like)")
        if "Neural2" in name:
            return (3, "Neural2")
        if "Polyglot" in name:
            return (3, "Polyglot")
        if "Wavenet" in name:
            return (4, "Wavenet")
        return (5, "Standard")

    voices.sort(key=lambda v: _tier(v["name"])[0])

    return [
        {
            "id": v["name"],
            "name": v["name"],
            "gender": v.get("ssmlGender", ""),
            "description": _tier(v["name"])[1],
        }
        for v in voices
    ]


def list_voices(provider: str, language_code: str = "en-US") -> list[dict]:
    if provider == "elevenlabs":
        return list_elevenlabs_voices()
    elif provider == "google":
        return list_google_voices(language_code)
    raise ValueError(f"Unknown TTS provider: {provider!r}")


def generate_preview_sample(provider: str, voice_id: str, dest_path: str) -> str:
    """Only used for providers without a free pre-recorded preview (Google).
    ElevenLabs previews should just use the preview_url directly instead —
    calling this for ElevenLabs would waste a real API call unnecessarily."""
    from .tts import generate_google_voiceover, generate_elevenlabs_voiceover

    if provider == "google":
        return generate_google_voiceover(PREVIEW_SAMPLE_TEXT, dest_path, voice_name=voice_id)
    elif provider == "elevenlabs":
        return generate_elevenlabs_voiceover(PREVIEW_SAMPLE_TEXT, dest_path, voice_id=voice_id)
    raise ValueError(f"Unknown TTS provider: {provider!r}")
