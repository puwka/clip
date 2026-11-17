import json
import math
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .config import CLIP_DIR, FFMPEG, FFPROBE, SOURCE_DIR
from .editor_layout import quality_args

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
VIDEO_INPUT = [
    "-protocol_whitelist", "file,pipe", "-format_whitelist",
    "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,flv,mpegts",
]
FFMPEG_BASE = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-filter_threads", "2"]


class MediaError(Exception):
    pass


def probe(path: Path):
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", *VIDEO_INPUT, "-show_entries",
             "format=duration:stream=codec_type,duration:stream_disposition=attached_pic",
             "-of", "json", str(path)],
            capture_output=True, check=True, timeout=45, creationflags=CREATE_FLAGS,
        )
        metadata = json.loads(result.stdout)
        duration = float(metadata["format"]["duration"])
        streams = metadata["streams"]
        video = next((s for s in streams if s["codec_type"] == "video"
                      and not s.get("disposition", {}).get("attached_pic")), None)
        if not video or not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid video")
        try:
            video_duration = float(video.get("duration", "nan"))
        except (TypeError, ValueError):
            video_duration = math.nan
        if math.isfinite(video_duration) and video_duration > 0:
            duration = min(duration, video_duration)
        if duration > 24 * 3600:
            raise MediaError("Длительность записи должна быть меньше 24 часов.")
        return duration, any(s["codec_type"] == "audio" for s in streams)
    except (subprocess.SubprocessError, KeyError, ValueError) as exc:
        raise MediaError("FFprobe не смог прочитать видеодорожку или длительность записи.") from exc


def read_media(path, start, length, options, *, check):
    check()
    try:
        result = subprocess.run(
            [*FFMPEG_BASE, "-ss", f"{start:.6f}", "-t", f"{length:.6f}",
             *VIDEO_INPUT, "-i", str(path), *options, "pipe:1"],
            capture_output=True, check=True, timeout=240, creationflags=CREATE_FLAGS,
        )
    except subprocess.SubprocessError as exc:
        raise MediaError("FFmpeg не смог прочитать часть записи.") from exc
    check()
    return result.stdout

def encode(args, timeout, *, cwd=None):
    try:
        subprocess.run(
            [*FFMPEG_BASE, "-y", *args], stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True, timeout=timeout, creationflags=CREATE_FLAGS, cwd=cwd,
        )
    except subprocess.CalledProcessError as exc:
        lines = exc.stderr.decode("utf-8", errors="replace").splitlines()
        detail = " ".join(lines[-4:])[-440:] if lines else "проверьте видеокодек и свободное место"
        raise MediaError(f"Ошибка экспорта FFmpeg: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("FFmpeg превысил время экспорта клипа.") from exc


def cut_highlight(path, start, end, master, preview, poster, check):
    # Pad only when needed. Keep source pixels, cadence and audio channels.
    blocks = "ceil(max(iw*sar/32,ih/18))"
    already_wide = "eq(iw*sar*9,ih*16)"
    frame = (f"pad=w='if({already_wide},iw,ceil(32*{blocks}/sar/2)*2)':"
             f"h='if({already_wide},ih,18*{blocks})':x=(ow-iw)/2:y=(oh-ih)/2,"
             "setsar=16*h/(9*w):max=100000")
    encode(["-ss", f"{start:.6f}", *VIDEO_INPUT, "-i", str(path), "-t", f"{end-start:.6f}",
            "-map", "0:V:0", "-map", "0:a?", "-vf", frame, "-fps_mode", "passthrough",
            "-c:v", "ffv1", "-level", "3", "-slicecrc", "1", "-threads:v", "4",
            "-c:a", "pcm_f64le", str(master)], timeout=900)
    check()
    encode(["-i", str(master), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(iw,1280)':h=-2:flags=lanczos,format=yuv420p",
            *quality_args("compact"), "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart",
            str(preview)], timeout=900)
    check()
    encode(["-i", str(preview), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
            "-update", "1", str(poster)], timeout=30)


def process_source(source: dict, progress: Callable, check: Callable, outputs: list[Path]):
    path = SOURCE_DIR / f"{source['id']}{source['extension']}"
    if not path.is_file():
        raise MediaError("Исходное видео отсутствует в локальной папке sources.")
    from .highlights import analyze_highlights

    segments = analyze_highlights(path, source, progress, check)

    clips = []
    for index, (candidate, rating) in enumerate(segments):
        start, end = candidate.start, candidate.end
        check()
        progress(86 + int(12 * index / len(segments)))
        clip_id = str(uuid4())
        master = CLIP_DIR / f"{clip_id}.mkv"
        preview = CLIP_DIR / f"{clip_id}.mp4"
        poster = CLIP_DIR / f"{clip_id}.jpg"
        outputs.extend([master, preview, poster])
        cut_highlight(path, start, end, master, preview, poster, check)
        clips.append({
            "id": clip_id,
            "title": rating.title,
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "score": rating.score / 100,
            "category": rating.category,
            "reason": rating.reason,
        })
    progress(98)
    return clips
