"""ffmpeg wrappers: normalisation, probing, silence-aware splitting, concatenation.

All of it is subprocess work, so every entry point is async and shells out without blocking the
event loop. PRD §6.2 specifies 16 kHz mono WAV for STT — every provider here wants that, and
normalising once up front means the extract stage never has to care what the user uploaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..errors import MediaError

log = logging.getLogger(__name__)

#: WAV at 16 kHz mono is ~32 kB/s, so this is roughly 12 minutes per chunk — comfortably under
#: the 25 MB ceiling the Whisper endpoints impose.
SPLIT_TARGET_BYTES = 20 * 1024 * 1024
#: PRD §6.3: chunks overlap by 2 seconds so a sentence spanning a cut is not lost.
OVERLAP_SEC = 2.0


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise MediaError(
            "ffmpeg and ffprobe must be on PATH to process audio or video. "
            "Install ffmpeg from https://ffmpeg.org/download.html and restart quicklearn."
        )


async def _run(*args: str, timeout: float = 1800.0) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise MediaError(f"{args[0]} timed out after {timeout:.0f}s.") from None
    return process.returncode or 0, stdout, stderr


@dataclass(slots=True)
class MediaInfo:
    duration_sec: float
    has_audio: bool
    has_video: bool
    format_name: str = ""


async def probe(path: Path) -> MediaInfo:
    """Inspect a media file with ffprobe."""
    require_ffmpeg()
    code, stdout, stderr = await _run(
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
        timeout=120,
    )
    if code != 0:
        raise MediaError(
            f"Could not read '{path.name}' — it may be corrupt or not a media file. "
            f"ffprobe: {stderr.decode(errors='replace')[:300]}"
        )

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise MediaError(f"Could not parse media information for '{path.name}'.") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    duration_raw = fmt.get("duration")
    duration = float(duration_raw) if duration_raw not in (None, "N/A") else 0.0

    if duration == 0.0:
        # Some containers omit format-level duration; fall back to the longest stream.
        for stream in streams:
            value = stream.get("duration")
            if value not in (None, "N/A"):
                duration = max(duration, float(value))

    return MediaInfo(
        duration_sec=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        has_video=any(s.get("codec_type") == "video" for s in streams),
        format_name=str(fmt.get("format_name") or ""),
    )


async def normalize_audio(source: Path, destination: Path) -> MediaInfo:
    """Transcode to 16 kHz mono PCM WAV (PRD §6.2).

    Any video stream is discarded — v1 does no visual frame analysis, and carrying video
    through would multiply file sizes for nothing.
    """
    require_ffmpeg()
    info = await probe(source)
    if not info.has_audio:
        raise MediaError(f"'{source.name}' has no audio track, so there is nothing to transcribe.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    code, _, stderr = await _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",  # drop video
        "-ac",
        "1",  # mono
        "-ar",
        "16000",  # 16 kHz
        "-c:a",
        "pcm_s16le",
        str(destination),
    )
    if code != 0 or not destination.exists():
        raise MediaError(
            f"ffmpeg could not convert '{source.name}' to audio. "
            f"{stderr.decode(errors='replace')[:400]}"
        )

    return MediaInfo(
        duration_sec=info.duration_sec,
        has_audio=True,
        has_video=False,
        format_name="wav",
    )


async def detect_silences(
    path: Path, *, noise_db: float = -32.0, min_silence_sec: float = 0.6
) -> list[float]:
    """Midpoints of detected silences, as candidate split points.

    Cutting inside a word produces two garbled partial transcriptions at the seam. Cutting in a
    silence usually means cutting between sentences, which the overlap then covers.
    """
    require_ffmpeg()
    code, _, stderr = await _run(
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    )
    if code != 0:
        return []

    points: list[float] = []
    start: float | None = None
    for line in stderr.decode(errors="replace").splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0])
                points.append((start + end) / 2.0)
            except (IndexError, ValueError):
                pass
            start = None
    return points


@dataclass(slots=True)
class AudioChunk:
    path: Path
    #: Offset of this chunk in the original timeline, used to shift transcript timestamps back.
    offset_sec: float
    duration_sec: float


async def split_audio(
    path: Path,
    workdir: Path,
    *,
    max_bytes: int,
    overlap_sec: float = OVERLAP_SEC,
) -> list[AudioChunk]:
    """Split oversized audio on silence boundaries with overlap (PRD §6.3).

    Returns a single unsplit chunk when the file already fits, so callers need no special case.
    """
    size = path.stat().st_size
    info = await probe(path)
    duration = info.duration_sec

    if size <= max_bytes or duration <= 0:
        return [AudioChunk(path=path, offset_sec=0.0, duration_sec=duration)]

    target_bytes = min(max_bytes, SPLIT_TARGET_BYTES)
    piece_count = max(2, -(-size // target_bytes))  # ceiling division
    nominal = duration / piece_count

    silences = await detect_silences(path)
    boundaries: list[float] = []
    for index in range(1, piece_count):
        ideal = nominal * index
        # Snap to the nearest silence within 25% of a piece length, else cut at the ideal point.
        nearby = [s for s in silences if abs(s - ideal) < nominal * 0.25]
        boundaries.append(min(nearby, key=lambda s: abs(s - ideal)) if nearby else ideal)

    boundaries = sorted(set(boundaries))
    cuts = [0.0, *boundaries, duration]

    workdir.mkdir(parents=True, exist_ok=True)
    chunks: list[AudioChunk] = []
    for index in range(len(cuts) - 1):
        # Overlap backwards into the previous chunk so a sentence crossing the seam appears
        # whole in at least one of them.
        start = max(0.0, cuts[index] - (overlap_sec if index > 0 else 0.0))
        end = cuts[index + 1]
        if end - start < 0.5:
            continue
        out = workdir / f"chunk_{index:03d}.wav"
        code, _, stderr = await _run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out),
        )
        if code != 0 or not out.exists():
            raise MediaError(
                f"Could not split audio for transcription. {stderr.decode(errors='replace')[:300]}"
            )
        chunks.append(AudioChunk(path=out, offset_sec=start, duration_sec=end - start))

    log.info("split %s (%.1f MB) into %d chunks", path.name, size / 1e6, len(chunks))
    return chunks


async def concat_audio(
    parts: list[Path],
    destination: Path,
    *,
    gap_ms: int = 150,
    crossfade_ms: int = 0,
    bitrate: str = "128k",
    metadata: dict[str, str] | None = None,
) -> float:
    """Concatenate clips into one MP3 with a gap between turns (PRD §7.6).

    Uses the concat *demuxer* over a file list rather than a filtergraph: a 20-minute podcast is
    hundreds of clips, and a filtergraph that wide hits ffmpeg's argument limits and takes far
    longer. Gaps are inserted as generated silence entries in the list.
    """
    require_ffmpeg()
    if not parts:
        raise MediaError("No audio to assemble.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    workdir = destination.parent
    silence_path = workdir / f"_gap_{gap_ms}ms.mp3"

    if gap_ms > 0 and not silence_path.exists():
        code, _, stderr = await _run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=mono:d={gap_ms / 1000:.3f}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(silence_path),
        )
        if code != 0:
            log.warning(
                "could not build gap clip, assembling without gaps: %s",
                stderr.decode(errors="replace")[:200],
            )

    listing = workdir / "_concat.txt"
    lines = []
    for index, part in enumerate(parts):
        if index > 0 and gap_ms > 0 and silence_path.exists():
            lines.append(f"file '{silence_path.as_posix()}'")
        lines.append(f"file '{part.as_posix()}'")
    listing.write_text("\n".join(lines), encoding="utf-8")

    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
    ]
    if crossfade_ms > 0:
        # Light smoothing at the joins. Applied as a filter over the concatenated stream
        # rather than pairwise, which would be O(n) nested filters.
        args += ["-af", f"aresample=44100,afade=t=in:d={crossfade_ms / 1000:.3f}"]
    for key, value in (metadata or {}).items():
        args += ["-metadata", f"{key}={value}"]
    args += ["-c:a", "libmp3lame", "-b:a", bitrate, str(destination)]

    code, _, stderr = await _run(*args)
    listing.unlink(missing_ok=True)

    if code != 0 or not destination.exists():
        raise MediaError(
            f"Could not assemble the podcast audio. {stderr.decode(errors='replace')[:400]}"
        )

    info = await probe(destination)
    return info.duration_sec


async def transcode_to_mp3(source: Path, destination: Path, *, bitrate: str = "128k") -> None:
    require_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    code, _, stderr = await _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(destination),
    )
    if code != 0:
        raise MediaError(f"Could not convert audio to MP3. {stderr.decode(errors='replace')[:300]}")
