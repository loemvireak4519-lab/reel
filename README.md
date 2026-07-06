# Reel — script to video, with a review step

A self-hosted web app that turns a script + your recorded voiceover into a
subtitled rough cut. It pauses before rendering anything so you can review
and edit the AI's choices on a timeline first.

```
Script → Voiceover (upload, OR generate with ElevenLabs/Google TTS)
→ split into scenes → extract keywords → analyze emotion
→ choose visual type → search stock footage → fill timeline → generate
subtitles → USER REVIEWS (swap clips, retime, edit text/motion/emotion)
→ export video (fetch selected assets, AI-generate any that need it,
  mix music/SFX, render)
```

## Voiceover: upload your own, or generate one

The input form has a "Voiceover source" toggle:
- **Upload my own recording** — the original flow, unchanged.
- **Generate with AI voice** — no recording needed. Pick ElevenLabs (most
  natural-sounding) or Google Cloud TTS (their Studio-tier voices), optionally
  give a specific voice ID/name, and `pipeline/tts.py` synthesizes the
  voiceover from the script text itself before anything else runs. From that
  point on it's treated exactly like an uploaded recording — same Whisper
  alignment, same timing accuracy — so nothing downstream needed to change.

## Two phases, not one pass

The earlier version of this tool ran start to finish and handed you a
finished file. This version splits at the review step:

**Prepare** (`pipeline/orchestrator.py: prepare_pipeline`) — runs scene
splitting, keyword/emotion tagging, voice-timing, and stock footage search.
Crucially, footage search returns *several candidates per scene* with only
remote thumbnail URLs (no downloads, no AI generation, no cost) — enough for
the review UI to show options. Job status lands on `ready_for_review`.

**Review** (frontend, backed by `PATCH /api/jobs/{id}/scenes/{i}` and
`POST /api/jobs/{id}/scenes/{i}/search`) — you edit subtitle text, motion,
emotion, start/end time, or swap in a different candidate (from the ones
already fetched, or by searching again with your own query). Every edit
saves immediately; nothing renders yet.

**Export** (`pipeline/orchestrator.py: export_pipeline`, triggered by
`POST /api/jobs/{id}/export`) — only now does it download the actual media
for whichever candidate is selected per scene (or run Runway/Stability if
that candidate is AI-sourced), rebuild the subtitle file from your edited
text, and render with ffmpeg: motion → concat → burned-in subtitles →
music/SFX mix → final mux.

## File map

| Stage | File |
|---|---|
| Split / keywords / emotion / visual type | `pipeline/scene_splitter.py` — one Claude call returns all of these per scene |
| Voice timing | `pipeline/voice_align.py` — Whisper word-timestamps aligned to scene text |
| Stock footage candidates | `pipeline/footage_search.py` — Pexels + Pixabay, returns multiple options with thumbnails, downloads only on selection |
| AI generation | `pipeline/ai_generator.py` — Stability (images) / Runway (video), only called at export for AI-selected candidates |
| Subtitles | `pipeline/subtitles.py` — builds `.srt` from (possibly edited) scene text/timing |
| Motion, concat, mixing, export | `pipeline/video_assembler.py` — all ffmpeg |
| Orchestration | `pipeline/orchestrator.py` — `prepare_pipeline` and `export_pipeline` |
| API | `main.py` — job creation, polling, scene PATCH/search, export trigger, download |
| Review timeline UI | `frontend/index.html` + `app.js` + `style.css` |

## Setup

**Requirements:** Python 3.11+, `ffmpeg` on your PATH.

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in your API keys
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` — the backend serves the frontend directly too.

### API keys

- `ANTHROPIC_API_KEY` — required, scene splitting/keywords/emotion
- `OPENAI_API_KEY` — required, voice-timing alignment (Whisper)
- `PEXELS_API_KEY` and/or `PIXABAY_API_KEY` — required for stock candidates
- `STABILITY_API_KEY` — AI image fallback
- `RUNWAY_API_KEY` — AI video fallback
- `ELEVENLABS_API_KEY` — for AI-generated voiceover (most natural option)
- `GOOGLE_TTS_API_KEY` — for AI-generated voiceover (Google Cloud TTS alternative)

## API reference (for the review step)

```
POST /api/jobs                              multipart: script, voiceover, music?
  -> { job_id }

GET  /api/jobs/{id}
  -> full job state, including scenes[].candidates for the review UI

PATCH /api/jobs/{id}/scenes/{index}          json body, any subset of:
  { subtitle_text, motion, emotion, start, end, selected_candidate_id }
  -> updated scene

POST /api/jobs/{id}/scenes/{index}/search    json: { query }
  -> appends new candidates to that scene, returns the full candidate list

POST /api/jobs/{id}/export
  -> starts the render, job.status becomes "exporting" then "done"

GET  /api/jobs/{id}/download
  -> the finished MP4
```

## What's tested vs. what needs your keys

Verified during development, without external API calls:
- The full ffmpeg render chain (motion → concat → subtitle burn-in → music
  mix → mux) against synthetic clips — confirmed correct 1080p H.264/AAC
  output with visible burned-in subtitles.
- The complete prepare → review-edit → re-search → export flow through the
  actual FastAPI routes, with the AI-service calls mocked — including the
  "no stock match found" path correctly falling back to an AI placeholder
  candidate that the review UI can show and the export step can generate.

Needs your keys to exercise for real: scene splitting (Claude), voice
alignment (Whisper), and actual stock/AI results. Drop your keys into
`backend/.env` and run one short script through it as your first live
smoke test.

## Import a script from YouTube

Paste a YouTube URL and click "Extract & rewrite": `pipeline/youtube_import.py`
pulls the video's transcript (via the `youtube-transcript-api` library) and
sends it to Claude with a prompt that studies its tone, pacing, and
structure, then writes a **brand-new, original script** in that same style —
different characters, different specific content, no reused sentences. The
transcript itself is never shown to you or stored; it's only used as
internal reference material for that one rewrite call. This keeps the
feature on the "inspired by the style" side of the line rather than
producing a reskinned copy of someone else's work.

**This was tested against a real failure and caught it.** An early version
of this feature was tested against a well-known song and produced a rewrite
that mimicked the chorus's repeated hook line with words swapped ("never
gonna give you up" → "never gonna walk away") — recognizably the same thing
despite different wording. The prompt alone didn't reliably prevent this, so
there's now a backend safety check independent of the prompt:
`_has_repeated_hook()` in `pipeline/youtube_import.py` detects when an
output repeats the same opening phrase across multiple lines (the actual
structural signature of a chorus/hook, which matters more than exact word
choice), plus a separate n-gram check for verbatim phrase reuse. Either one
failing triggers one retry with a blunter instruction; if it fails again,
the request errors out instead of silently returning a risky result.

**This needs a proxy to actually work when hosted on Render (or any cloud
platform).** YouTube blocks transcript requests from most datacenter IPs,
including Render's — this isn't a code bug, it's YouTube's own IP-blocking,
confirmed via the `IpBlocked`/`RequestBlocked` errors the library raises.
Fix: sign up for a cheap Webshare rotating-residential proxy plan (~$1/month
— this is specifically what `youtube-transcript-api`'s own docs recommend
for this exact problem), then set two more env vars on Render:
`WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD`. No code changes
needed — `pipeline/youtube_import.py` picks them up automatically. Without
a proxy configured, this feature works when run locally (your home IP isn't
blocked) but will fail on Render with a clear error message telling you why.
A `PROXY_URL` env var (any standard `http://user:pass@host:port` proxy, from
any provider) takes priority over Webshare if you'd rather use a different
service — a residential IP (not datacenter) is what actually matters here.

## Choosing an AI visual generator and a real voice

Two pickers in the input form beyond what's described above:

- **AI visual generator** — a quality-tier dropdown (Fast / High quality /
  Video) used only for scenes where no stock footage matches. Maps to
  Stability Core (fast/cheap), Stability Ultra (slower, higher quality), or
  Runway (actual video generation, slowest and priciest). See
  `pipeline/ai_generator.py`.
- **Voice** — once you pick ElevenLabs or Google, the "Voice" dropdown
  populates with the real voices available on your account (fetched live
  via `GET /api/voices?provider=...`), so you're picking an actual named
  voice instead of pasting an ID you'd have to look up separately. A
  "▶ Preview" button next to the dropdown lets you hear it before choosing —
  ElevenLabs voices have a free pre-recorded sample (`preview_url`, no cost);
  Google has no equivalent, so its preview generates a few words on demand
  via `GET /api/voices/preview` (a small real cost per click, unlike
  ElevenLabs). See `pipeline/voices.py`.

Google's voice picker ranks by how natural each tier actually sounds:
**Gemini-TTS** and **Chirp3-HD**/**Studio** (most human-like, disfluencies +
real emotional intonation) → **Neural2**/**Polyglot** → **Wavenet** →
**Standard** (avoid — sounds robotic). Chirp3-HD voices used to get
mislabeled as "Standard" quality since the code only recognized
Studio/Neural2/Wavenet by name — fixed so the actual best-sounding options
surface at the top instead of being buried at the bottom.

## Theme picker — starter scripts for popular niches

A "Theme" dropdown sits above the Script box with 10 of the strongest niches
for narrated/faceless video in 2026 (based on current CPM and growth data):
Personal Finance, AI & Tech Tutorials, Motivation, Documentary/History,
True Story/Narrative Drama, Health & Wellness, Travel, Product Reviews,
Micro-History, and Sleep/Ambient. Picking one loads a short original starter
script into the Script box — not a template to use verbatim, just a running
start you edit into your own thing. This is entirely client-side
(`THEME_SCRIPTS` in `frontend/app.js`) — no backend call, no cost, instant.

## Long videos (10+ minutes)

A 10-minute script is roughly 1,300-1,500 words — enough to exceed both
providers' per-request limits (Google Cloud TTS: 5,000 bytes; ElevenLabs:
10,000 characters) and to risk truncating the scene-splitter's JSON output
if it's asked to handle the whole script in one Claude call. Both are
handled automatically:

- `pipeline/tts.py` splits long text into provider-safe chunks on sentence
  boundaries, generates each chunk separately, and concatenates the audio
  with ffmpeg into one continuous voiceover file.
- `pipeline/scene_splitter.py` splits very long scripts into ~60-sentence
  chunks, calls Claude per chunk, and keeps scene indices continuous across
  chunks — so a 100+ scene video comes back as one coherent timeline, not
  several disconnected ones.

Practical expectations for a 10+ minute video: expect 60-100+ scenes, so the
prepare phase (scene splitting → timing → footage search) takes noticeably
longer than a short clip since it's doing that many stock-footage searches.
The review timeline will be a long scrollable list — nothing new is needed
there, it's the same per-scene cards, just more of them. Export time scales
with scene count too, since each scene gets its own short ffmpeg render
before they're concatenated.

## Deploying this as a hosted website

This needs a real, always-on process — ffmpeg renders take real wall-clock
time and the app keeps job state in memory while it works. That rules out
serverless platforms (Vercel, Netlify, Cloudflare Pages/Workers): they kill
requests after a short timeout and can't run ffmpeg as a background process.

**Recommended: Render.com** (Railway.app works the same way if you prefer it).
Both take a Dockerfile directly, give you a normal dashboard for environment
variables — that's where the API keys go, no code edits needed — and support
a persistent disk so rendered videos survive a restart.

1. Push this folder to a GitHub repo (it already includes `Dockerfile` and
   `render.yaml`).
2. On Render: **New → Blueprint**, point it at the repo. It reads
   `render.yaml` and sets up the service and the env var slots
   automatically — you just fill in the values in the dashboard:
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`,
   `STABILITY_API_KEY`, `RUNWAY_API_KEY`, and `ACCESS_PASSWORD`.
3. Deploy. You get a public URL.

**This repo's `render.yaml` is set to Render's free tier** (no card required).
The trade-off: no persistent disk, so rendered videos and job state are lost
whenever the service restarts or redeploys, and the free tier spins down
after inactivity (the first request after idling takes ~30-60s to wake back
up). Fine for trying it out or occasional personal use. If you outgrow that,
switch `plan: free` to `plan: starter` in `render.yaml` and add back a
`disk:` block (see git history of this file for the exact block) — Render
will then ask for payment info, which only you can enter.

**Set `ACCESS_PASSWORD`.** The moment this is a public URL, anyone who finds
it can trigger renders that spend *your* API credits. Setting this env var
turns on a real login page (a password field, not your browser's native
Basic-Auth popup — that approach re-prompts unpredictably for background API
calls in a JS app like this one) for the whole site — leave it unset only if you're fine with the link being open to anyone who
has it. Share the password out of band (not in the same message as the URL).

**If you outgrow a single server**: the persistent disk + in-memory job dict
works fine for one person or a small team hitting it casually. If you need
multiple people rendering heavier videos at once, the two things to change
first are (1) move `JOBS` in `main.py` to Redis or a database so job state
survives restarts and is shared across instances, and (2) move the actual
render work (`export_pipeline`) to a separate worker process/queue instead of
a thread in the web process, so a long render can't block new requests.

### Running the container locally first (recommended before deploying)

```bash
docker build -t reel .
docker run -p 8000:8000 --env-file backend/.env reel
```

If this works locally, it'll work identically on Render/Railway — they run
the same Dockerfile.

## Known limitations / next steps

- **Repeated stock footage was a real bug, now fixed.** Scenes with similar
  keywords often get the same top result from Pexels/Pixabay — the code used
  to always pick each scene's #1 candidate, so several scenes in a row could
  end up with the identical clip. `prepare_pipeline` in
  `pipeline/orchestrator.py` now tracks how many times each underlying clip
  has been used across the whole job and picks whichever available option
  has been used least, so repeats (when genuinely unavoidable, e.g. 6 scenes
  sharing only 3 unique search results) spread out evenly instead of
  clustering on one clip.

- **Runway's API surface shifts between versions.** `ai_generator.py` targets
  their async text-to-video task API — adjust `RUNWAY_BASE_URL` and the
  request/response shape if your account differs; nothing downstream needs
  to change since it only wants a local file path back.
- **Retiming a scene doesn't yet auto-shift its neighbors.** If you stretch
  scene 2's end time, scene 3 doesn't automatically start later — you'd need
  to adjust it too. A good next step: add a "snap to previous scene's end"
  option in the review UI.
- **Sound effects** are wired into `video_assembler.mix_audio` (pass a list of
  `(path, start_seconds)` tuples) but nothing in the review UI surfaces them
  yet — natural next step is to let the scene splitter also tag an optional
  SFX keyword per scene and search a free SFX API the same way stock footage
  is searched, then let it show up as another swappable option in review.
- **Job state is in-memory** (`JOBS` dict in `main.py`). Fine for local/single
  -user use; swap for persisted storage if you need jobs to survive a restart
  or want multiple people using it at once.
