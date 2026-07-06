"""
Optional stage inserted before "split into scenes": if the user doesn't
upload their own recording, generate a human-sounding voiceover from the
script text itself via ElevenLabs or Google Cloud TTS, then feed that
generated audio into the exact same voice_align (Whisper) step as an
uploaded recording would use — so timing accuracy doesn't depend on
which path was taken.
"""
import os
import base64
import requests

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Sensible, natural-sounding defaults so a voice ID/name is optional.
DEFAULT_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" - warm, neutral narration voice
DEFAULT_GOOGLE_VOICE = "en-US-Studio-O"            # Google's Studio tier - their most natural voice


def generate_elevenlabs_voiceover(text: str, dest_path: str, voice_id: str | None = None) -> str:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    resp = requests.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id or DEFAULT_ELEVENLABS_VOICE),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return dest_path


def generate_google_voiceover(
    text: str, dest_path: str, voice_name: str | None = None, language_code: str = "en-US"
) -> str:
    api_key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_TTS_API_KEY is not set")

    resp = requests.post(
        f"{GOOGLE_TTS_URL}?key={api_key}",
        json={
            "input": {"text": text},
            "voice": {"languageCode": language_code, "name": voice_name or DEFAULT_GOOGLE_VOICE},
            "audioConfig": {"audioEncoding": "MP3"},
        },
        timeout=120,
    )
    resp.raise_for_status()
    audio_bytes = base64.b64decode(resp.json()["audioContent"])
    with open(dest_path, "wb") as f:
        f.write(audio_bytes)
    return dest_path


def generate_voiceover(provider: str, text: str, dest_path: str, voice: str | None = None) -> str:
    if provider == "elevenlabs":
        return generate_elevenlabs_voiceover(text, dest_path, voice_id=voice)
    elif provider == "google":
        return generate_google_voiceover(text, dest_path, voice_name=voice)
    raise ValueError(f"Unknown TTS provider: {provider!r} (expected 'elevenlabs' or 'google')")
