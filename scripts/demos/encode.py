"""Compose actual UI captures into compact, captioned GitHub walkthroughs.

Requires Pillow 12.0.0. Raw frames stay outside Git. Export only GIFs,
still images, and public media metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
PAPER, INK, MUTED, ACCENT, FRAME = "#eff2ec", "#263b30", "#657469", "#3b7158", "#d6dfd1"
WIDTH, MARGIN, SCREEN_TOP, SCREEN_HEIGHT, HEIGHT = 1120, 20, 82, 675, 856


def font(size: int, bold: bool = False) -> Any:
    candidates = [
        Path("/System/Library/Fonts/Supplemental") / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu")
        / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2")
        / ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face: Any, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        next_line = f"{current} {word}".strip()
        if current and draw.textlength(next_line, font=face) > width:
            lines.append(current)
            current = word
        else:
            current = next_line
    if current:
        lines.append(current)
    return lines


def compose(source: Image.Image, title: str, caption: str, index: int, count: int) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((MARGIN, 21, MARGIN + 40, 61), radius=9, fill=INK)
    draw.text((MARGIN + 11, 27), "V", font=font(26, True), fill="#edf2e9")
    draw.text((74, 18), "EVERVAL  /  PRODUCT WALKTHROUGH", font=font(10, True), fill=MUTED)
    title_font = font(25, True)
    if draw.textlength(title, font=title_font) > 770:
        raise ValueError("Clip title is too long for the title area")
    draw.text((74, 34), title, font=title_font, fill=INK)
    draw.rounded_rectangle((WIDTH - 182, 28, WIDTH - MARGIN, 55), radius=13, fill="#dfe8da")
    draw.text((WIDTH - 170, 36), "LOCAL FIXTURE DEMO", font=font(10, True), fill=ACCENT)
    screen_box = (MARGIN, SCREEN_TOP, WIDTH - MARGIN, SCREEN_TOP + SCREEN_HEIGHT)
    draw.rounded_rectangle(
        (MARGIN - 1, SCREEN_TOP - 1, WIDTH - MARGIN + 1, SCREEN_TOP + SCREEN_HEIGHT + 2),
        radius=10,
        fill=FRAME,
    )
    screen = Image.new("RGB", (WIDTH - MARGIN * 2, SCREEN_HEIGHT), "#f8faf5")
    fitted = ImageOps.contain(source.convert("RGB"), screen.size, Image.Resampling.LANCZOS)
    screen.paste(fitted, ((screen.width - fitted.width) // 2, (screen.height - fitted.height) // 2))
    mask = Image.new("L", screen.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, screen.width, screen.height), radius=9, fill=255)
    canvas.paste(screen, screen_box[:2], mask)
    caption_font = font(19)
    lines = wrap(draw, caption, caption_font, WIDTH - 155)
    if len(lines) > 2:
        raise ValueError("Caption exceeds two lines: " + caption)
    for line_index, line in enumerate(lines):
        draw.text((MARGIN + 2, 774 + line_index * 23), line, font=caption_font, fill=INK)
    draw.text((WIDTH - 83, 779), f"{index + 1:02d} / {count:02d}", font=font(13, True), fill=MUTED)
    draw.text(
        (MARGIN + 2, 829),
        "Actual interface · edited pacing · fixture results",
        font=font(11),
        fill=MUTED,
    )
    track_left, track_right = 420, WIDTH - MARGIN
    draw.rounded_rectangle((track_left, 832, track_right, 835), radius=2, fill=FRAME)
    draw.rounded_rectangle(
        (track_left, 832, track_left + (track_right - track_left) * (index + 1) / count, 835),
        radius=2,
        fill=ACCENT,
    )
    return canvas


def load_manifest(path: Path, value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is None:
        value = json.loads(path.read_text())
    name = value.get("name", "")
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("Use a lowercase, hyphenated clip name")
    frames = value.get("frames", [])
    if not 2 <= len(frames) <= 60:
        raise ValueError("Each walkthrough needs 2–60 frames")
    if not value.get("fixture_note"):
        raise ValueError("Declare the capture's fixture and interpretation limits")
    for frame in frames:
        source = (path.parent / frame["file"]).resolve()
        if not source.is_relative_to(path.parent.resolve()) or not source.is_file():
            raise ValueError("Frame must be a file inside its capture directory")
        if not 100 <= frame["duration_ms"] <= 8000:
            raise ValueError("Frame duration must be between 100 and 8000 milliseconds")
    if sum(frame["duration_ms"] for frame in frames) > 90000:
        raise ValueError("Keep each walkthrough under 90 seconds")
    return value


def encode(
    path: Path, out: Path, colors: int = 192, value: dict[str, Any] | None = None
) -> dict[str, Any]:
    manifest = load_manifest(path, value)
    frames, durations = [], []
    for index, entry in enumerate(manifest["frames"]):
        with Image.open(path.parent / entry["file"]) as source:
            frames.append(
                compose(source, manifest["title"], entry["caption"], index, len(manifest["frames"]))
            )
        durations.append(entry["duration_ms"])
    # Shared colors keep stationary pixels stable and permit differential frames.
    thumb_size = (280, 214)
    atlas = Image.new("RGB", (thumb_size[0] * len(frames), thumb_size[1]))
    for index, frame in enumerate(frames):
        atlas.paste(frame.resize(thumb_size, Image.Resampling.LANCZOS), (index * thumb_size[0], 0))
    palette = atlas.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    out.mkdir(parents=True, exist_ok=True)
    destination = out / (manifest["name"] + ".gif")
    quantized[0].save(
        destination,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    poster_index = min(int(manifest.get("poster_frame", len(frames) - 1)), len(frames) - 1)
    poster = out / (manifest["name"] + ".png")
    frames[poster_index].save(poster, optimize=True)
    with Image.open(destination) as check:
        if not check.is_animated or check.n_frames != len(frames) or check.size != (WIDTH, HEIGHT):
            raise ValueError("Encoded animation failed its frame/dimension check")
        observed_duration = 0
        for index in range(check.n_frames):
            check.seek(index)
            observed_duration += check.info["duration"]
            if ImageChops.difference(
                check.convert("RGB"), quantized[index].convert("RGB")
            ).getbbox():
                raise ValueError("Decoded animation differs from the expected frame")
        if abs(observed_duration - sum(durations)) > len(frames) * 10:
            raise ValueError("Encoded frame timing differs from requested pacing")
    if destination.stat().st_size > 5_000_000:
        raise ValueError("GIF exceeds 5 MB; shorten or reduce its visual scope")
    return {
        "name": manifest["name"],
        "title": manifest["title"],
        "fixture_note": manifest["fixture_note"],
        "gif": destination.name,
        "poster": poster.name,
        "width": WIDTH,
        "height": HEIGHT,
        "frames": len(frames),
        "duration_ms": observed_duration,
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "captions": [entry["caption"] for entry in manifest["frames"]],
        "redactions": manifest.get("redactions", []),
    }


def tour(specs: list[tuple[Path, dict[str, Any]]], out: Path) -> dict[str, Any]:
    private = ROOT / ".local"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="everval-tour-", dir=private) as temporary:
        directory = Path(temporary)
        manifest: dict[str, Any] = {
            "name": "everval-tour",
            "title": "From a question to a decision.",
            "fixture_note": "Edited tour of actual interfaces using installation controls. Repository validation remains pending; checkpoint observations are synthetic.",
            "frames": [],
            "redactions": sorted(
                {note for _, clip in specs for note in clip.get("redactions", [])}
            ),
        }
        for path, clip in specs:
            poster = min(
                int(clip.get("poster_frame", len(clip["frames"]) - 1)), len(clip["frames"]) - 1
            )
            for index in (0, poster):
                entry = clip["frames"][index]
                name = f"{len(manifest['frames']):02d}.png"
                shutil.copyfile(path.parent / entry["file"], directory / name)
                manifest["frames"].append(
                    {
                        "file": name,
                        "duration_ms": 1500,
                        "caption": entry["caption"],
                    }
                )
            if clip["name"] == "rankings":
                manifest["poster_frame"] = len(manifest["frames"]) - 1
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return encode(manifest_path, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/demos")
    parser.add_argument(
        "--tour", action="store_true", help="Also compose a short tour from all clips."
    )
    args = parser.parse_args()
    specs = []
    for path in args.manifests:
        value = json.loads(path.read_text())
        for clip in value.get("clips", [value]):
            specs.append((path, load_manifest(path, clip)))
    if len({value["name"] for _, value in specs}) != len(specs):
        raise ValueError("Clip names must be unique")
    clips = [encode(path, args.output, value=value) for path, value in specs]
    if args.tour:
        clips.insert(0, tour(specs, args.output))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = {
        "application_revision": revision,
        "source": "Actual UI captures from isolated fixture workspaces",
        "pacing": "Edited for readability; not a latency measurement",
        "capture_policy": "Original installation controls; no paid model calls are required.",
        "encoder": "Pillow 12.0.0",
        "clips": clips,
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for clip in clips:
        print(
            f"{clip['name']}: {clip['frames']} frames, {clip['duration_ms'] / 1000:.1f}s, {clip['bytes'] / 1_000_000:.2f} MB"
        )


if __name__ == "__main__":
    main()
