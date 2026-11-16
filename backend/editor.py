import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .advertising import AdPlacement, EditAd, banner_canvas
from .config import EDIT_DIR, EXTENSIONS, SOURCE_DIR
from .editor_audio import SAMPLE_RATE, encode_audio
from .editor_captions import prepare_speech, write_overlays
from .editor_layout import quality_args, scene_filter
from .media import VIDEO_INPUT, MediaError, encode, probe

FPS = 30
MAX_PARTS = 10
MAX_SECONDS = 180


class EditOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    crop: Literal["auto", "fixed", "fit"] = "auto"
    sensitivity: int = Field(default=65, ge=0, le=100)
    center_x: int = Field(default=50, ge=0, le=100)
    center_y: int = Field(default=50, ge=0, le=100)
    captions: bool = True
    caption_language: Literal["auto", "ru", "en"] = "auto"
    caption_size: int = Field(default=62, ge=42, le=100)
    caption_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str = Field(default="#FFD633", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_words: bool = True
    caption_y: int = Field(default=76, ge=45, le=88)
    words_per_line: int = Field(default=4, ge=2, le=6)
    title: str = Field(default="", max_length=72)
    title_size: int = Field(default=76, ge=44, le=92)
    title_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    title_y: int = Field(default=8, ge=4, le=24)
    title_seconds: float = Field(default=3, ge=0.5, le=8)
    transition: Literal["cut", "fade", "fadeblack"] = "fade"
    transition_seconds: float = Field(default=0.3, ge=0.1, le=1)
    quality: Literal["compact", "standard", "high"] = "high"


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    clip_ids: list[UUID] = Field(min_length=1, max_length=MAX_PARTS)
    options: EditOptions
    ad: EditAd | None = None


def prepare_part(part, options, check):
    source = SOURCE_DIR / (str(UUID(part["source_id"])) + part["extension"])
    if part["extension"] not in EXTENSIONS or not source.is_file():
        raise MediaError("Исходный файл одного из выбранных фрагментов отсутствует.")
    start, end = float(part["start_seconds"]), float(part["end_seconds"])
    words = []
    if options.captions and part["has_audio"]:
        source_duration, _ = probe(source)
        start, end, words = prepare_speech(source, start, end, source_duration, check, options.caption_language)
    return {**part, "start_seconds": start, "end_seconds": end, "words": words, "path": source}


def render_part(part, frames, options, count, index, transition_frames, workdir, check):
    source = part["path"]
    start = part["start_seconds"]
    duration = frames / FPS
    lo = transition_frames / (2 * FPS) if index else 0.0
    hi = duration - (transition_frames / (2 * FPS) if index < count - 1 else 0.0)
    part_dir = workdir / f"part-{index}"
    part_dir.mkdir()
    cues = write_overlays(part_dir, part["words"], start, options, options.title if index == 0 else "", lo, hi)
    video = (f"fps={FPS},setpts=PTS-STARTPTS,{scene_filter(options)},"
             f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={frames},format=yuv420p")
    if (part_dir / "overlays.ass").is_file():
        video += ",ass=filename=overlays.ass:fontsdir=fonts"
    inputs = ["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", *VIDEO_INPUT, "-i", str(source)]
    if part["has_audio"]:
        audio = "0:a:0"
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "1:a:0"
    samples = frames * (SAMPLE_RATE // FPS)
    af = f"aresample={SAMPLE_RATE}:async=1:first_pts=0,apad,atrim=end_sample={samples},asetpts=N/SR/TB"
    target = part_dir / "scene.mkv"
    check()
    encode([*inputs, "-map", "0:V:0", "-map", audio, "-vf", video, "-af", af,
            "-c:v", "ffv1", "-level", "3", "-slices", "4", "-threads:v", "4",
            "-c:a", "pcm_s16le", "-ac", "2", "-ar", str(SAMPLE_RATE),
            "-t", f"{duration:.6f}", str(target)], timeout=1200, cwd=part_dir)
    return target, cues


def assemble(paths, frame_counts, transition_frames, options, destination, workdir, ad=None):
    count = len(paths)
    inputs = [arg for path in paths for arg in ("-threads", "1", "-i", str(path))]
    graph = []
    for i, frames in enumerate(frame_counts):
        graph += [
            f"[{i}:v]settb=1/{FPS},setpts=N,format=yuv420p[v{i}]",
            f"[{i}:a]atrim=end_sample={frames * (SAMPLE_RATE // FPS)},asetpts=N/SR/TB[a{i}]",
        ]
    video, audio = "v0", "a0"
    total = frame_counts[0]
    if count > 1 and transition_frames:
        overlap = transition_frames / FPS
        for i in range(1, count):
            offset = (total - transition_frames) / FPS
            graph.append(f"[{video}][v{i}]xfade=transition={options.transition}:duration={overlap:.6f}:offset={offset:.6f}[joinv{i}]")
            graph.append(f"[{audio}][a{i}]acrossfade=ns={transition_frames * (SAMPLE_RATE // FPS)}:c1=tri:c2=tri[joina{i}]")
            video, audio = f"joinv{i}", f"joina{i}"
            total += frame_counts[i] - transition_frames
    elif count > 1:
        inputs_to_join = "".join(f"[v{i}][a{i}]" for i in range(count))
        graph.append(f"{inputs_to_join}concat=n={count}:v=1:a=1[video][audio]")
        video, audio = "video", "audio"
        total = sum(frame_counts)
    if ad is not None:
        placement = AdPlacement.model_validate(ad["placement"])
        end = min(placement.end if placement.end is not None else total / FPS, total / FPS)
        if end <= placement.start:
            raise MediaError("Интервал показа рекламы находится за пределами ролика.")
        overlay = workdir / "campaign.png"
        banner_canvas(ad["banner_id"], ad["promo_code"], placement).save(overlay)
        inputs += ["-framerate", str(FPS), "-i", str(overlay)]
        graph += [
            f"[{count}:v]format=yuva420p[ad]",
            f"[{video}][ad]overlay=x=0:y=0:repeatlast=1:format=yuv420:"
            f"enable='gte(t,{placement.start:.6f})*lt(t,{end:.6f})'[advertised]",
        ]
        video = "advertised"
    encode([*inputs, "-filter_complex_threads", "2", "-filter_complex", ";".join(graph),
            "-map", f"[{video}]", "-map", f"[{audio}]", "-t", f"{total / FPS:.6f}", "-r", str(FPS),
            *quality_args(options.quality), "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
            str(destination)], timeout=1800, cwd=workdir)
    return total / FPS


def plan_edit(parts, options):
    frame_counts = [math.floor((p["end_seconds"] - p["start_seconds"]) * FPS + 1e-6) for p in parts]
    if not frame_counts or min(frame_counts) < FPS:
        raise MediaError("Выберите фрагменты длительностью не менее секунды.")
    transition_frames = min(round(options.transition_seconds * FPS), min(frame_counts) // 4)
    if len(parts) == 1 or options.transition == "cut":
        transition_frames = 0
    if sum(frame_counts) - transition_frames * (len(parts) - 1) > MAX_SECONDS * FPS:
        raise MediaError("Shorts должен длиться до 3 минут. Уберите лишние фрагменты.")
    return frame_counts, transition_frames


def render_edit(job, token, progress, check):
    options = EditOptions.model_validate(job["options"])
    requested = job["segments"]
    parts = [prepare_part(part, options, check) for part in requested]
    frame_counts, transition_frames = plan_edit(parts, options)
    output = EDIT_DIR / f"{token}.mp4"
    ad = job.get("ad")
    with TemporaryDirectory(prefix="edit-", dir=EDIT_DIR) as temp:
        workdir = Path(temp)
        paths, timelines = [], []
        offset = 0
        for i, (part, frames) in enumerate(zip(parts, frame_counts)):
            check()
            progress(5 + 75 * i // len(parts))
            rendered, cues = render_part(part, frames, options, len(parts), i, transition_frames, workdir, check)
            paths.append(rendered)
            for cue in cues:
                cue["start"] = round(cue["start"] + offset / FPS, 3)
                cue["end"] = round(cue["end"] + offset / FPS, 3)
            timelines.append({
                "requested_start": requested[i]["start_seconds"], "requested_end": requested[i]["end_seconds"],
                "source_start": round(part["start_seconds"], 3), "source_end": round(part["start_seconds"] + frames / FPS, 3),
                "output_start": round(offset / FPS, 3), "cues": cues,
            })
            offset += frames - transition_frames
        check()
        progress(84)
        # MOV keeps the exact video time base during the final stream-copy.
        master = workdir / "master.mov"
        duration = assemble(paths, frame_counts, transition_frames, options, master, workdir, ad)
        progress(94)
        measurements = encode_audio(master, output, duration, check)
        check()
        output.with_suffix(".review.json").write_text(
            json.dumps({"duration": duration, "audio": measurements, "segments": timelines}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return duration
