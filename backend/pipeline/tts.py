"""
Optional stage inserted before "split into scenes": if the user doesn't
upload their own recording, generate a human-sounding voiceover from the
script text itself via ElevenLabs or Google Cloud TTS, then feed that
generated audio into the exact same voice_align (Whisper) step as an
uploaded recording would use — so timing accuracy doesn't depend on
which path was taken.

Both providers cap how much text one request can synthesize (Google: 5,000
bytes; ElevenLabs: 10,000 characters for the model used here). A 10+ minute
script easily exceeds either, so long text is split into provider-safe
chunks on sentence boundaries and the resulting audio segments are
concatenated with ffmpeg into a single voiceover file.
"""
import os
import re
import base64
import subprocess
import tempfile
import requests

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Sensible, natural-sounding defaults so a voice ID/name is optional.
DEFAULT_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" - warm, neutral narration voice
DEFAULT_GOOGLE_VOICE = "en-US-Studio-O"            # Google's Studio tier - their most natural voice

# Kept comfortably under each provider's hard limit so multi-byte characters
# don't tip a chunk over the edge. ElevenLabs' limit varies by model.
ELEVENLABS_MODEL_MAX_CHARS = {
    "eleven_multilingual_v2": 9000,   # hard limit 10,000
    "eleven_flash_v2_5": 38000,       # hard limit 40,000
    "eleven_turbo_v2_5": 38000,       # hard limit 40,000
    "eleven_v3": 2800,                # hard limit 3,000
}
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
GOOGLE_MAX_CHARS = 4500


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Splits text into <= max_chars pieces on sentence boundaries where
    possible, so a chunk seam doesn't land mid-sentence and to keep prosody
    reasonably natural across the join."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(sentence) <= max_chars:
                current = sentence
            else:
                # A single sentence longer than the limit (rare) - hard-split it.
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i : i + max_chars])
                current = ""

    if current:
        chunks.append(current)

    return chunks


def _concat_audio(paths: list[str], dest_path: str) -> str:
    if len(paths) == 1:
        os.replace(paths[0], dest_path)
        return dest_path

    workdir = os.path.dirname(paths[0])
    list_file = os.path.join(workdir, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", dest_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio concat failed: {result.stderr[-1000:]}")
    return dest_path


def _call_elevenlabs(text: str, api_key: str, voice_id: str, model_id: str, dest_path: str) -> str:
    resp = requests.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return dest_path


def _call_google(text: str, api_key: str, voice_name: str, language_code: str, dest_path: str) -> str:
    resp = requests.post(
        f"{GOOGLE_TTS_URL}?key={api_key}",
        json={
            "input": {"text": text},
            "voice": {"languageCode": language_code, "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3"},
        },
        timeout=120,
    )
    resp.raise_for_status()
    audio_bytes = base64.b64decode(resp.json()["audioContent"])
    with open(dest_path, "wb") as f:
        f.write(audio_bytes)
    return dest_path


def generate_elevenlabs_voiceover(
    text: str, dest_path: str, voice_id: str | None = None, model_id: str | None = None
) -> str:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = voice_id or DEFAULT_ELEVENLABS_VOICE
    model_id = model_id or DEFAULT_ELEVENLABS_MODEL
    max_chars = ELEVENLABS_MODEL_MAX_CHARS.get(model_id, 9000)

    chunks = _split_into_chunks(text, max_chars)
    if len(chunks) == 1:
        return _call_elevenlabs(chunks[0], api_key, voice_id, model_id, dest_path)

    workdir = tempfile.mkdtemp(prefix="tts_chunks_")
    part_paths = []
    for i, chunk in enumerate(chunks):
        part_path = os.path.join(workdir, f"part_{i:03d}.mp3")
        _call_elevenlabs(chunk, api_key, voice_id, model_id, part_path)
        part_paths.append(part_path)

    return _concat_audio(part_paths, dest_path)


def generate_google_voiceover(
    text: str, dest_path: str, voice_name: str | None = None, language_code: str = "en-US"
) -> str:
    api_key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_TTS_API_KEY is not set")
    voice_name = voice_name or DEFAULT_GOOGLE_VOICE

    chunks = _split_into_chunks(text, GOOGLE_MAX_CHARS)
    if len(chunks) == 1:
        return _call_google(chunks[0], api_key, voice_name, language_code, dest_path)

    workdir = tempfile.mkdtemp(prefix="tts_chunks_")
    part_paths = []
    for i, chunk in enumerate(chunks):
        part_path = os.path.join(workdir, f"part_{i:03d}.mp3")
        _call_google(chunk, api_key, voice_name, language_code, part_path)
        part_paths.append(part_path)

    return _concat_audio(part_paths, dest_path)


def generate_voiceover(provider: str, text: str, dest_path: str, voice: str | None = None, model: str | None = None) -> str:
    if provider == "elevenlabs":
        return generate_elevenlabs_voiceover(text, dest_path, voice_id=voice, model_id=model)
    elif provider == "google":
        return generate_google_voiceover(text, dest_path, voice_name=voice)
    raise ValueError(f"Unknown TTS provider: {provider!r} (expected 'elevenlabs' or 'google')")
