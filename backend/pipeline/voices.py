"""
Powers the "pick a real voice" dropdown in the review UI, instead of asking
the user to paste a raw voice ID they'd have to look up themselves.
"""
import os
import requests

ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
GOOGLE_VOICES_URL = "https://texttospeech.googleapis.com/v1/voices"


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

    # Studio and Neural2 voices sound the most natural - surface those first.
    def sort_key(v):
        name = v["name"]
        if "Studio" in name:
            return 0
        if "Neural2" in name:
            return 1
        if "Wavenet" in name:
            return 2
        return 3

    voices.sort(key=sort_key)

    return [
        {
            "id": v["name"],
            "name": v["name"],
            "gender": v.get("ssmlGender", ""),
            "description": "Studio (most natural)"
            if "Studio" in v["name"]
            else "Neural2"
            if "Neural2" in v["name"]
            else "Wavenet"
            if "Wavenet" in v["name"]
            else "Standard",
        }
        for v in voices
    ]


def list_voices(provider: str, language_code: str = "en-US") -> list[dict]:
    if provider == "elevenlabs":
        return list_elevenlabs_voices()
    elif provider == "google":
        return list_google_voices(language_code)
    raise ValueError(f"Unknown TTS provider: {provider!r}")
