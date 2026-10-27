# Continuous recording verification — 2026-10-26

Twelve uninterrupted recordings replace the earlier keyframe slideshows. The main tour follows one browser session from a typed request through a completed fixture comparison and its rankings. The remaining recordings show individual workflows, including separate reviewer and coordinator sessions.

The browser was recorded at 1280×800 and 25 frames per second. Terminal captures ran the actual Everval application in a PTY and streamed its ANSI output into a local xterm.js renderer. Pointer motion, typing, scrolling and application transitions were recorded directly. Each published clip uses one continuous source segment, with beginning/end trims only.

| Recording | Duration | GIF frames |
| --- | ---: | ---: |
| everval-tour | 46.20s | 924 |
| chat-and-reports | 31.85s | 637 |
| rankings | 29.95s | 599 |
| tasks-and-comparisons | 47.00s | 940 |
| production-cases | 34.05s | 681 |
| checkpoint-monitoring | 39.35s | 787 |
| double-blind | 35.05s | 701 |
| blind-reviewer | 27.00s | 540 |
| terminal-workbench | 16.05s | 321 |
| repository-onboarding | 22.60s | 452 |
| autopilot-budget | 15.75s | 315 |
| failure-research | 27.25s | 545 |

GIFs use 960×600 at 20 frames per second and a 64-color palette. Temporal noise suppression retains a previous pixel only when each channel differs from the recorded pixel by at most 6/255 before palette quantization. Larger changes update immediately. MP4s retain the native dimensions and frame rate. Neither export changes playback speed, assembles screenshots or inserts internal cuts.

The October 27 presentation update reduces the hero from 12.89 MB to 2.99 MB and total GIF storage from 130.09 MB to 31.80 MB. The README loads one animation. The gallery loads that animation plus 240 KB of previews; individual workflow pages each load one recording. Source segments, frame counts, durations, MP4s and PNG previews are unchanged.

## Verification

- Decoded every GIF frame and checked dimensions, loop metadata, frame timing and total duration against its source segment.
- Verified GIF and MP4 hashes, PNG decoding, and MP4 dimensions, frame rate and duration.
- Inspected exported typing sequences, workflow previews and source interactions for readable text, continuous motion and completed UI actions.
- Recorded zero browser JavaScript errors and zero model API calls. Capture-owned servers, browser contexts and terminal processes were closed. The demonstration's autopilot policy was left paused.
- Checked capture and encoder lint/formatting, local documentation links and Git whitespace.

[Machine-readable checks](summary.json) · [Media manifest](../../assets/demos/recordings/manifest.json) · [Capture recipes](../../../scripts/demos/README.md)

## Scope

These are interface demonstrations with original installation controls and explicitly synthetic checkpoint observations. They do not establish model quality, latency, customer adoption or completed real-repository validation. Repository onboarding ends with isolated validation pending. Blind-review accounts and judgments are scripted demonstrations, not an independent human study.

The application code is unchanged. Raw recordings, workspaces, credentials and capture logs stay on the maintainer's iMac outside Git. The source application revision is recorded in the media manifest. The [previous verification record](../2026-10-26-demos/README.md) remains labeled as superseded.
