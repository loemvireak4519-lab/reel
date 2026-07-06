"""
New input path: paste a YouTube link -> pull its transcript -> have Claude
study its tone/pacing/structure and write a wholly new, original script in
that same style.

Important: the transcript is used only as internal reference material for
Claude's analysis. It is never returned to the frontend or stored anywhere
persistent, and the rewrite prompt explicitly forbids reproducing sentences,
character names, or specific plot/content from the source — the output must
be new writing that merely shares tone, pacing, and structure. This keeps
the feature on the "inspired by" side of the line, not a verbatim clone with
a reskinned character.
"""
import os
import re
import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

REWRITE_SYSTEM_PROMPT = """You are a scriptwriting assistant. You will be given a transcript \
of a YouTube video, provided only as reference material for its STYLE.

Your job: write a brand-new, original narration script that captures the same
tone, pacing, energy, structure, and rhetorical style as the reference —
but is otherwise a completely different, original piece of writing.

Hard rules:
- Do NOT reuse any sentence, distinctive phrase, or line from the reference
  transcript. Every sentence in your output must be your own original wording.
- Do NOT reuse specific character names, brand names, or proper nouns from
  the reference. Invent new ones if the content involves any named entities.
- Do NOT reuse the specific facts, plot points, or subject matter of the
  reference if it's a story, review, or opinion piece — change the actual
  content/topic/characters while keeping the *shape* (pacing, structure,
  humor style, sentence rhythm, level of formality, narrative arc) the same.
- The output should read as clearly original work a viewer would not
  recognize as sourced from the reference, while "feeling" stylistically
  similar to someone who knows both.

Return ONLY the new script text, no preamble, no explanation, no markdown.
"""


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/shorts\/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a video ID from URL: {url}")


def _build_transcript_api():
    """Priority: a generic PROXY_URL (e.g. a residential/static proxy you
    trust) first, then Webshare if configured, then no proxy at all. Direct
    connections and most free/datacenter proxy tiers get blocked or
    rate-limited by YouTube when run from a cloud host like Render — a
    residential proxy IP is what actually gets past that."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import WebshareProxyConfig, GenericProxyConfig

    generic_proxy_url = os.environ.get("PROXY_URL")
    webshare_user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    webshare_pass = os.environ.get("WEBSHARE_PROXY_PASSWORD")

    if generic_proxy_url:
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(
                http_url=generic_proxy_url,
                https_url=generic_proxy_url,
            )
        )
    if webshare_user and webshare_pass:
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=webshare_user,
                proxy_password=webshare_pass,
            )
        )
    return YouTubeTranscriptApi()


def fetch_transcript_text(url: str) -> str:
    """Internal use only — never exposed directly to the frontend."""
    import requests as requests_lib
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        IpBlocked,
        RequestBlocked,
    )

    video_id = extract_video_id(url)

    try:
        ytt_api = _build_transcript_api()
        fetched = ytt_api.fetch(video_id)
    except TranscriptsDisabled:
        raise RuntimeError("This video has captions/transcripts disabled.")
    except NoTranscriptFound:
        raise RuntimeError("No transcript is available for this video.")
    except VideoUnavailable:
        raise RuntimeError("This video is unavailable (private, deleted, or region-locked).")
    except (IpBlocked, RequestBlocked):
        raise RuntimeError(
            "YouTube is blocking transcript requests from this server's IP address. "
            "Set WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD (a cheap Webshare "
            "rotating-residential proxy plan, ~$1/month, is what youtube-transcript-api "
            "recommends specifically for this) to fix it — see the README."
        )
    except requests_lib.exceptions.RequestException as e:
        # Raw network-level failure (e.g. repeated 429s exhausting urllib3's
        # retry budget before youtube-transcript-api's own error classes ever
        # get a chance to fire) — this happens even through a proxy if that
        # proxy's IPs are themselves rate-limited/blocked by YouTube.
        raise RuntimeError(
            "Couldn't reach YouTube to fetch the transcript (network-level failure, "
            f"likely rate-limiting): {e}. If a Webshare proxy is configured, its IPs "
            "may themselves be rate-limited — a paid Rotating Residential plan is more "
            "reliable for this than the free datacenter tier."
        )

    text = " ".join(snippet.text for snippet in fetched.snippets)
    return re.sub(r"\s+", " ", text).strip()


def rewrite_transcript_as_original_script(transcript_text: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    # Reference material is capped so extremely long source videos don't
    # blow the context window; the style is usually clear well before this.
    reference = transcript_text[:20000]

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 8000,
            "system": REWRITE_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": reference}],
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(text_blocks).strip()


def generate_script_from_youtube(url: str) -> str:
    transcript = fetch_transcript_text(url)
    if not transcript:
        raise RuntimeError("Fetched transcript was empty.")
    return rewrite_transcript_as_original_script(transcript)
