"""
Shared data structures passed between pipeline stages.
Kept as plain dicts-friendly dataclasses so they serialize easily to JSON
for the frontend (both the prep-progress feed and the review timeline).
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Literal

VisualType = Literal["stock_footage", "stock_image", "ai_video", "ai_image"]


@dataclass
class Candidate:
    """One option for a scene's visual, shown in the review UI's swap panel."""
    id: str
    source: str                      # "pexels" | "pixabay" | "ai"
    kind: str                        # "video" | "image"
    thumbnail_url: Optional[str] = None     # remote URL, shown directly by <img>, no download needed
    download_url: Optional[str] = None      # remote URL, fetched only if this candidate is selected at export
    local_path: Optional[str] = None        # filled in once downloaded/generated
    prompt: Optional[str] = None            # for ai candidates: the generation prompt

    def to_dict(self):
        return asdict(self)


@dataclass
class Scene:
    index: int
    text: str                          # original sentence(s) from the script
    start: float = 0.0                 # seconds, from voice alignment (editable in review)
    end: float = 0.0
    keywords: list[str] = field(default_factory=list)
    emotion: str = "neutral"           # editable in review
    visual_type: Optional[VisualType] = None
    search_query: Optional[str] = None
    motion: Optional[str] = None       # editable in review
    subtitle_text: Optional[str] = None  # editable in review

    candidates: list[Candidate] = field(default_factory=list)
    selected_candidate_id: Optional[str] = None

    # transient: set at export time from the selected candidate's local_path,
    # consumed by video_assembler. Not meaningful during review.
    asset_path: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    def selected_candidate(self) -> Optional[Candidate]:
        for c in self.candidates:
            if c.id == self.selected_candidate_id:
                return c
        return None


@dataclass
class Job:
    job_id: str
    # queued|splitting|analyzing|sourcing|timing|ready_for_review|exporting|assembling|done|error
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    scenes: list[Scene] = field(default_factory=list)
    voiceover_path: Optional[str] = None
    music_path: Optional[str] = None
    output_path: Optional[str] = None
    error: Optional[str] = None

    # Set when the user chooses "generate with AI voice" instead of
    # uploading a recording. voiceover_path stays None until prepare_pipeline
    # fills it in with the generated audio's local path.
    voice_provider: Optional[str] = None   # "elevenlabs" | "google" | None
    voice_id: Optional[str] = None
    elevenlabs_model: Optional[str] = None  # e.g. "eleven_multilingual_v2" — only used if voice_provider == "elevenlabs"
    ai_quality: str = "fast"                # "fast" | "high" | "video"
    visual_mode: str = "stock_or_ai"        # "stock_or_ai" | "ai_only"

    def to_dict(self):
        return asdict(self)

    def get_scene(self, index: int) -> Scene:
        for s in self.scenes:
            if s.index == index:
                return s
        raise KeyError(f"No scene with index {index}")
