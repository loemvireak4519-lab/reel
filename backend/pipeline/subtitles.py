"""Stage: "Add subtitles" — builds a standard .srt from scene timing."""
from .models import Scene


def _fmt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(scenes: list[Scene], dest_path: str, max_chars_per_line: int = 42) -> str:
    lines = []
    for i, scene in enumerate(scenes, start=1):
        text = (scene.subtitle_text or scene.text).strip()
        if not text:
            continue

        # simple wrap to two lines max for readability
        words = text.split()
        wrapped, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= max_chars_per_line:
                cur = f"{cur} {w}".strip()
            else:
                wrapped.append(cur)
                cur = w
        if cur:
            wrapped.append(cur)
        display_text = "\n".join(wrapped[:2])

        lines.append(str(i))
        lines.append(f"{_fmt_timestamp(scene.start)} --> {_fmt_timestamp(scene.end)}")
        lines.append(display_text)
        lines.append("")

    with open(dest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return dest_path
