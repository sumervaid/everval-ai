"""Export one continuous recording as a smooth GIF, MP4 and still preview.

Trim only the beginning/end. Never concatenate workflows, assemble screenshots,
interpolate stills, insert caption cards or change playback speed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[2]
FPS, WIDTH = 20, 1120


def run(ffmpeg: str, *arguments: str) -> None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Video export failed: " + result.stderr[-2000:])


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get("continuous") is not True or "frames" in value:
        raise ValueError("Only uninterrupted video captures are accepted")
    name = value.get("name", "")
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("Use a lowercase, hyphenated clip name")
    video = (path.parent / value["video"]).resolve()
    if not video.is_relative_to(path.parent.resolve()) or not video.is_file():
        raise ValueError("Source video must be inside its private capture directory")
    if not value.get("fixture_note"):
        raise ValueError("Declare the source and interpretation limits")
    start, end = float(value["start_s"]), float(value["end_s"])
    if not 0 <= start < end or not 3 <= end - start <= 180:
        raise ValueError("Choose one continuous segment between 3 and 180 seconds")
    return value


def probe(video: Path) -> dict[str, Any]:
    with closing(imageio_ffmpeg.read_frames(str(video))) as reader:
        return next(reader)


def encode(path: Path, output: Path) -> dict[str, Any]:
    value = load(path)
    source = (path.parent / value["video"]).resolve()
    source_info = probe(source)
    if source_info["fps"] < 20 or source_info["source_size"][0] < WIDTH:
        raise ValueError("Record at full viewport resolution and at least 20 fps")
    start = float(value["start_s"])
    end = min(float(value["end_s"]), float(source_info["duration"]))
    if float(value["end_s"]) - end > 0.75 or end <= start:
        raise ValueError("Segment timing exceeds the recorded video")
    duration = end - start
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output.mkdir(parents=True, exist_ok=True)
    gif = output / (value["name"] + ".gif")
    mp4 = output / (value["name"] + ".mp4")
    poster = output / (value["name"] + ".png")
    sampling = f"fps={FPS},scale={WIDTH}:-2:flags=lanczos,hqdn3d=1.2:1.2:6:6"
    source_args = ("-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source))
    private = ROOT / ".local"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="continuous-palette-", dir=private) as temporary:
        palette = Path(temporary) / "palette.png"
        run(
            ffmpeg,
            *source_args,
            "-vf",
            sampling + ",palettegen=max_colors=128:stats_mode=diff",
            "-frames:v",
            "1",
            str(palette),
        )
        run(
            ffmpeg,
            *source_args,
            "-i",
            str(palette),
            "-filter_complex",
            f"[0:v]{sampling}[screen];[screen][1:v]paletteuse=dither=none:diff_mode=rectangle",
            "-loop",
            "0",
            "-gifflags",
            "+transdiff",
            str(gif),
        )
    run(
        ffmpeg,
        *source_args,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4),
    )
    poster_time = max(start, min(float(value.get("poster_s", end - 0.5)), end - 0.1))
    run(
        ffmpeg,
        "-ss",
        f"{poster_time:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={WIDTH}:-2:flags=lanczos",
        str(poster),
    )
    with Image.open(gif) as animation:
        count, dimensions = animation.n_frames, animation.size
        elapsed_ms, changed = 0, 0
        previous = None
        for index in range(count):
            animation.seek(index)
            current = animation.convert("RGB")
            if previous is not None and ImageChops.difference(previous, current).getbbox():
                changed += 1
            previous = current
            elapsed_ms += animation.info.get("duration", 0)
        if count < math.floor(duration * FPS) - 3 or abs(elapsed_ms / 1000 - duration) > 0.2:
            raise ValueError("GIF does not preserve continuous frame cadence")
        if changed < 40:
            raise ValueError("Too few changing frames to demonstrate interaction")
        if animation.info.get("loop") != 0:
            raise ValueError("GIF loop metadata is missing")
    with Image.open(poster) as still:
        still.verify()
    encoded_video = probe(mp4)
    if abs(encoded_video["duration"] - duration) > 0.2:
        raise ValueError("Video export changed the recorded duration")
    return {
        "name": value["name"],
        "title": value["title"],
        "fixture_note": value["fixture_note"],
        "continuous": True,
        "source_fps": source_info["fps"],
        "gif_fps": FPS,
        "width": dimensions[0],
        "height": dimensions[1],
        "frames": count,
        "changing_frames": changed,
        "duration_ms": elapsed_ms,
        "gif": gif.name,
        "video": mp4.name,
        "poster": poster.name,
        "bytes": gif.stat().st_size,
        "video_bytes": mp4.stat().st_size,
        "sha256": hashlib.sha256(gif.read_bytes()).hexdigest(),
        "video_sha256": hashlib.sha256(mp4.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_segment": {"start_s": start, "end_s": end},
        "marks": value.get("marks", []),
        "redactions": value.get("redactions", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/demos/continuous")
    args = parser.parse_args()
    names = [load(path)["name"] for path in args.manifests]
    if len(names) != len(set(names)):
        raise ValueError("Clip names must be unique")
    clips = []
    for path in args.manifests:
        clip = encode(path, args.output)
        clips.append(clip)
        print(
            f"{clip['name']}: {clip['frames']} frames at {FPS} fps, "
            f"{clip['duration_ms'] / 1000:.2f}s, GIF {clip['bytes'] / 1_000_000:.2f} MB, "
            f"video {clip['video_bytes'] / 1_000_000:.2f} MB",
            flush=True,
        )
    metadata = {
        "application_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "capture": "Continuous actual browser recordings and a live terminal PTY",
        "editing": "Beginning/end trims only; no internal cuts, screenshot assembly or speed changes",
        "gif_fps": FPS,
        "gif_encoding": "1120 pixels wide at 20 fps, mild compression-noise reduction and 128-color quantization",
        "video_encoding": "Source dimensions and frame rate, H.264, unchanged playback speed",
        "encoder": "FFmpeg via imageio-ffmpeg 0.6.0; Pillow 12.0.0 verification",
        "clips": clips,
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
