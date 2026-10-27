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
FPS, WIDTH, POSTER_WIDTH = 20, 960, 1120
COLORS, PIXEL_TOLERANCE = 64, 6


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


def stabilize(
    ffmpeg: str,
    source_args: tuple[str, ...],
    output: Path,
    height: int,
    log: Path,
) -> int:
    """Suppress recording noise without changing the sampled frame timeline.

    A previous pixel is retained only if every RGB channel is within six levels
    of the current recorded pixel. Larger changes, including typing, scrolling
    and cursor movement, replace it immediately. The six-level bound applies
    before palette quantization; no frames are skipped, interpolated or repeated.
    """
    threshold = [255 if value > PIXEL_TOLERANCE else 0 for value in range(256)]
    frame_bytes = WIDTH * height * 3
    common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2"]
    frames = 0
    previous = None
    with log.open("w+b") as errors:
        decoder = subprocess.Popen(
            [
                *common,
                "-filter_threads",
                "2",
                *source_args,
                "-vf",
                f"fps={FPS},scale={WIDTH}:{height}:flags=lanczos",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        encoder = None
        try:
            encoder = subprocess.Popen(
                [
                    *common,
                    "-y",
                    "-f",
                    "rawvideo",
                    "-s",
                    f"{WIDTH}x{height}",
                    "-r",
                    str(FPS),
                    "-pix_fmt",
                    "rgb24",
                    "-i",
                    "pipe:0",
                    "-an",
                    "-threads",
                    "2",
                    "-c:v",
                    "ffv1",
                    "-level",
                    "3",
                    "-pix_fmt",
                    "bgr0",
                    str(output),
                ],
                stdin=subprocess.PIPE,
                stderr=errors,
            )
            assert decoder.stdout is not None and encoder.stdin is not None
            while raw := decoder.stdout.read(frame_bytes):
                if len(raw) != frame_bytes:
                    raise RuntimeError("Recording ended inside a frame")
                current = Image.frombytes("RGB", (WIDTH, height), raw)
                if previous is not None:
                    red, green, blue = ImageChops.difference(current, previous).split()
                    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(threshold)
                    current = Image.composite(current, previous, mask)
                encoder.stdin.write(current.tobytes())
                previous = current
                frames += 1
            encoder.stdin.close()
            statuses = [decoder.wait(), encoder.wait()]
            if any(statuses):
                raise RuntimeError("Recording noise stabilization failed")
        except (OSError, RuntimeError) as error:
            errors.seek(0)
            details = errors.read().decode(errors="replace")[-2000:]
            raise RuntimeError(f"{error}: {details}") from error
        finally:
            for process in (decoder, encoder):
                if process is None:
                    continue
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
    if not frames:
        raise ValueError("Recording contained no frames")
    return frames


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
    source_args = ("-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source))
    private = ROOT / ".local"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="continuous-palette-", dir=private) as temporary:
        temporary_path = Path(temporary)
        palette = temporary_path / "palette.png"
        stabilized = temporary_path / "stabilized.mkv"
        source_width, source_height = source_info["source_size"]
        height = 2 * int(source_height * WIDTH / source_width / 2 + 0.5)
        sampled_frames = stabilize(
            ffmpeg, source_args, stabilized, height, temporary_path / "stabilize.log"
        )
        if sampled_frames < math.floor(duration * FPS) - 3:
            raise ValueError("Noise stabilization omitted recorded frames")
        run(
            ffmpeg,
            "-i",
            str(stabilized),
            "-vf",
            f"palettegen=max_colors={COLORS}:stats_mode=full",
            "-frames:v",
            "1",
            str(palette),
        )
        run(
            ffmpeg,
            "-i",
            str(stabilized),
            "-i",
            str(palette),
            "-filter_complex",
            "[0:v][1:v]paletteuse=dither=none:diff_mode=rectangle",
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
        f"scale={POSTER_WIDTH}:-2:flags=lanczos",
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
        "gif_colors": COLORS,
        "stabilization_max_channel_difference": PIXEL_TOLERANCE,
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
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/demos/recordings")
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
        "gif_encoding": (
            "960 pixels wide at 20 fps, bounded per-pixel temporal noise suppression "
            "(at most 6/255 per RGB channel before palette quantization), 64 colors"
        ),
        "video_encoding": "Source dimensions and frame rate, H.264, unchanged playback speed",
        "encoder": "FFmpeg via imageio-ffmpeg 0.6.0; Pillow 12.0.0 verification",
        "clips": clips,
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
