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
of a YouTube video, provided only as reference material to understand its general register \
(e.g. energetic vs. calm, formal vs. casual, comedic vs. serious) and overall topic area.

Your job: write a brand-new, original narration script for a different video. It should be \
recognizably different content that merely shares a similar energy level and formality — \
NOT a retelling, reskinning, or parallel version of the reference.

Critical rules — violating any of these makes the output unusable:
- If the reference is song lyrics or has a sung/rhyming chorus: your output must be **plain \
  prose narration, never verse, never rhyming, never a chorus/hook structure**. Do not mimic \
  meter, rhyme scheme, or a repeated hook line under any circumstances — a paraphrased chorus \
  ("never gonna X" -> "never gonna Y") is exactly the kind of close copy this prohibits, even \
  with every word changed.
- Do not mirror sentence-by-sentence or line-by-line structure at all. Do not produce a version \
  where each line of your output has an obvious 1:1 correspondence to a line in the reference.
- Do not reuse any distinctive phrase, hook, refrain, or repeated line pattern from the reference.
- Do not reuse character names, brand names, proper nouns, or the specific subject matter/plot \
  from the reference — pick a genuinely different topic in a similar register instead.
- Before finalizing, check your own output: if a person who knows the reference would recognize \
  it as "the same thing with words swapped," rewrite it more freely until they would not.

Return ONLY the new script text as plain prose, no preamble, no explanation, no markdown, no \
line breaks that mimic verse structure.
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


def _word_ngrams(text: str, n: int = 4) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z']+", text.lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _similarity_ratio(a: str, b: str, n: int = 4) -> float:
    """Rough measure of verbatim phrase reuse via shared word n-grams."""
    ngrams_a = _word_ngrams(a, n)
    ngrams_b = _word_ngrams(b, n)
    if not ngrams_b:
        return 0.0
    shared = ngrams_a & ngrams_b
    return len(shared) / len(ngrams_b)


def _has_repeated_hook(text: str, prefix_len: int = 2, min_repeats: int = 3) -> bool:
    """Detects a repeated opening phrase across multiple lines/sentences —
    the actual telltale structural signature of a song chorus or strong
    anaphora ("Never gonna X... never gonna Y... never gonna Z..."). This
    matters more than exact word reuse: a listener recognizes a derivative
    by this repetition pattern even when every word has been swapped, which
    is exactly what a plain word-overlap check misses."""
    lines = re.split(r"[.!?\n]+", text.lower())
    prefixes = []
    for line in lines:
        words = re.findall(r"[a-z']+", line)
        if len(words) >= prefix_len:
            prefixes.append(tuple(words[:prefix_len]))

    if not prefixes:
        return False

    counts: dict[tuple[str, ...], int] = {}
    for p in prefixes:
        counts[p] = counts.get(p, 0) + 1

    return max(counts.values(), default=0) >= min_repeats


def rewrite_transcript_as_original_script(transcript_text: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    # Reference material is capped so extremely long source videos don't
    # blow the context window; the style is usually clear well before this.
    reference = transcript_text[:20000]

    def _call_claude(extra_instruction: str = "") -> str:
        system = REWRITE_SYSTEM_PROMPT + extra_instruction
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
                "system": system,
                "messages": [{"role": "user", "content": reference}],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(text_blocks).strip()

    result = _call_claude()
    similarity = _similarity_ratio(reference, result)
    risky_structure = _has_repeated_hook(result)

    # First attempt failed either check — retry once with a blunt, explicit
    # instruction rather than silently shipping a risky result.
    if similarity > 0.15 or risky_structure:
        result = _call_claude(
            "\n\nYour previous attempt was rejected: it either reused source phrasing, or it "
            "used a repeated opening hook/refrain across multiple lines (a chorus-like pattern), "
            "which is unsafe even with different words. Write plain, varied prose with NO "
            "repeated line-opening phrase anywhere, and no wording shared with the source."
        )
        similarity = _similarity_ratio(reference, result)
        risky_structure = _has_repeated_hook(result)

    if similarity > 0.15 or risky_structure:
        raise RuntimeError(
            "Could not generate a sufficiently original rewrite of this video after two "
            "attempts — the source material may be too distinctive (e.g. song lyrics with a "
            "very recognizable hook) to safely rewrite automatically. Try a different video, "
            "or write your script from scratch."
        )

    return result


def generate_script_from_youtube(url: str) -> str:
    transcript = fetch_transcript_text(url)
    if not transcript:
        raise RuntimeError("Fetched transcript was empty.")
    return rewrite_transcript_as_original_script(transcript)
