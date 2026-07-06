"""
Stages: "Match with voice timing" (consumed, not produced, here) ->
"Add motion" -> "Add subtitles" -> "Background music" -> "Sound effects" -> "Export"

Everything below is driven through ffmpeg subprocess calls rather than a
Python video library, since ffmpeg is what's actually installed and it's
far faster for this kind of batch render.
"""
import os
import subprocess
import tempfile

from .models import Scene

W, H, FPS = 1920, 1080, 30

MOTION_FILTERS = {
    "zoom_in": "z='min(zoom+0.0012,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "zoom_out": "z='if(eq(on,0),1.25,max(1.0,zoom-0.0012))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "pan_left": "z=1.15:x='iw/2-(iw/zoom/2)-on*0.6':y='ih/2-(ih/zoom/2)'",
    "pan_right": "z=1.15:x='iw/2-(iw/zoom/2)+on*0.6':y='ih/2-(ih/zoom/2)'",
}

VIDEO_ENCODE_ARGS = [
    "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
    "-r", str(FPS), "-vsync", "cfr",
]


def _run(cmd: list[str]):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def render_scene_clip(scene: Scene, out_path: str) -> str:
    """Turns one scene's asset (image or clip) into a silent WxH video of exact duration."""
    duration = max(scene.end - scene.start, 0.3)
    is_image = scene.asset_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))

    if is_image:
        frames = max(int(duration * FPS), 1)
        if scene.motion and scene.motion in MOTION_FILTERS:
            expr = MOTION_FILTERS[scene.motion]
            vf = f"scale={W*2}:{H*2},zoompan={expr}:d={frames}:s={W}x{H}:fps={FPS}"
        else:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", scene.asset_path,
            "-vf", vf, "-t", f"{duration:.3f}", *VIDEO_ENCODE_ARGS, "-an", out_path,
        ]
    else:
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", scene.asset_path,
            "-vf", vf, "-t", f"{duration:.3f}", *VIDEO_ENCODE_ARGS, "-an", out_path,
        ]

    _run(cmd)
    return out_path


def concat_clips(clip_paths: list[str], out_path: str, workdir: str) -> str:
    list_file = os.path.join(workdir, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path]
    _run(cmd)
    return out_path


def burn_subtitles(video_in: str, srt_path: str, out_path: str) -> str:
    # ffmpeg's subtitles filter needs an escaped path on some platforms
    escaped = srt_path.replace(":", r"\:")
    style = "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=1,Outline=2,Alignment=2,MarginV=60"
    cmd = [
        "ffmpeg", "-y", "-i", video_in,
        "-vf", f"subtitles='{escaped}':force_style='{style}'",
        *VIDEO_ENCODE_ARGS, "-an", out_path,
    ]
    _run(cmd)
    return out_path


def mix_audio(
    voiceover_path: str,
    total_duration: float,
    out_path: str,
    music_path: str | None = None,
    music_volume: float = 0.15,
    sfx_events: list[tuple[str, float]] | None = None,  # (path, start_seconds)
) -> str:
    """Mixes voiceover (full volume) + looped/trimmed background music (ducked)
    + any one-off sound effects placed at specific timestamps."""
    inputs = ["-i", voiceover_path]
    filter_parts = []
    mix_labels = ["0:a"]

    input_idx = 1
    if music_path:
        inputs += ["-stream_loop", "-1", "-i", music_path]
        filter_parts.append(f"[{input_idx}:a]volume={music_volume}[music]")
        mix_labels.append("music")
        input_idx += 1

    if sfx_events:
        for path, start in sfx_events:
            inputs += ["-i", path]
            label = f"sfx{input_idx}"
            delay_ms = max(int(start * 1000), 0)
            filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[{label}]")
            mix_labels.append(label)
            input_idx += 1

    mix_inputs = "".join(f"[{lbl}]" for lbl in mix_labels)
    filter_parts.append(f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[aout]")
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]", "-t", f"{total_duration:.3f}",
        "-c:a", "aac", "-b:a", "192k", out_path,
    ]
    _run(cmd)
    return out_path


def mux_video_audio(video_path: str, audio_path: str, out_path: str) -> str:
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", out_path,
    ]
    _run(cmd)
    return out_path


def assemble_video(
    scenes: list[Scene],
    voiceover_path: str,
    srt_path: str,
    output_path: str,
    music_path: str | None = None,
    sfx_events: list[tuple[str, float]] | None = None,
    workdir: str | None = None,
) -> str:
    """Runs the full render: per-scene clips -> concat -> subtitles -> audio mix -> mux."""
    own_tmp = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="videopipeline_")
    clips_dir = os.path.join(workdir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    clip_paths = []
    for scene in scenes:
        if not scene.asset_path:
            continue
        clip_path = os.path.join(clips_dir, f"scene_{scene.index:03d}.mp4")
        render_scene_clip(scene, clip_path)
        clip_paths.append(clip_path)

    if not clip_paths:
        raise RuntimeError("No scene assets were available to render — check earlier pipeline stages.")

    silent_video = os.path.join(workdir, "silent.mp4")
    concat_clips(clip_paths, silent_video, workdir)

    subtitled_video = os.path.join(workdir, "subtitled.mp4")
    burn_subtitles(silent_video, srt_path, subtitled_video)

    total_duration = scenes[-1].end
    mixed_audio = os.path.join(workdir, "mixed_audio.m4a")
    mix_audio(voiceover_path, total_duration, mixed_audio, music_path=music_path, sfx_events=sfx_events)

    mux_video_audio(subtitled_video, mixed_audio, output_path)

    if own_tmp:
        # leave workdir for debugging by default; caller may clean up if desired
        pass

    return output_path
