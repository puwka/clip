import asyncio
import json
import logging
import re
import shutil
from importlib.util import find_spec
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field

from .advertising import EditAd, banner_path, campaign_state
from .config import FFMPEG, FFPROBE, SOURCE_DIR, TWITCH_DIR
from .editor import EditOptions, EditRequest
from .editor_api import queue_edits
from .highlights import MAX_CLIPS
from .media import MediaError, probe
from .twitch_capture import Recording, check_space

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twitch", tags=["Twitch"])
CHANNEL = re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/([a-z0-9_]{1,25})/?", re.IGNORECASE | re.ASCII)
RESERVED = {"directory", "downloads", "settings", "subscriptions", "inventory", "drops", "videos"}


class StartRecording(BaseModel):
    url: str = Field(max_length=200)
    campaign_id: UUID | None = None


@router.get("")
async def status(request: Request):
    state = request.app.state
    return {"recording": state.recording.snapshot() if state.recording else None,
            "handoff_error": state.twitch_error}


@router.post("", status_code=201)
async def start(request: Request, data: StartRecording):
    match = CHANNEL.fullmatch(data.url.strip())
    if not match or match[1].lower() in RESERVED:
        raise HTTPException(422, "Укажите ссылку на канал Twitch, например https://www.twitch.tv/канал.")
    if not find_spec("streamlink") or not (shutil.which(FFMPEG) and shutil.which(FFPROBE)):
        raise HTTPException(503, "Для записи установите Streamlink, FFmpeg и FFprobe.")
    try:
        check_space()
    except MediaError as exc:
        raise HTTPException(507, str(exc)) from exc

    state = request.app.state
    rows = await state.db.get("cf_settings", id="eq.1", limit=1)
    if not rows or "twitch_segment_minutes" not in rows[0]:
        raise HTTPException(503, "Обновите схему Supabase для записи Twitch.")
    settings = rows[0]
    ad = None
    if data.campaign_id:
        campaigns = await state.db.get("cf_campaigns", id=f"eq.{data.campaign_id}", limit=1)
        if (not campaigns or campaign_state(campaigns[0]) != "available" or
                not banner_path(campaigns[0]["banner_id"]).is_file()):
            raise HTTPException(409, "Выберите действующую кампанию с доступным баннером.")
        campaign = campaigns[0]
        ad = EditAd(campaign_id=data.campaign_id, placement=campaign["placement"],
                    expected_views=campaign["expected_views"]).model_dump(mode="json")

    if state.recording_task and not state.recording_task.done():
        raise HTTPException(409, "На этом компьютере уже идёт запись Twitch.")
    lock = FileLock(TWITCH_DIR / "recording.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise HTTPException(409, "На этом компьютере уже идёт запись Twitch.") from exc
    recording = Recording(match[1].lower(), settings["twitch_segment_minutes"], settings["clip_seconds"], ad)
    state.recording = recording

    async def run():
        try:
            await asyncio.to_thread(recording.run)
        finally:
            lock.release()

    state.recording_task = asyncio.create_task(run())
    return recording.snapshot()


@router.post("/{recording_id}/stop")
async def stop(request: Request, recording_id: UUID):
    recording = request.app.state.recording
    if not recording or recording.id != str(recording_id):
        raise HTTPException(404, "Запись не найдена.")
    if recording.status not in {"finished", "failed"}:
        recording.status = "stopping"
        recording.stop_capture()
    return recording.snapshot()


async def handoff_segment(db, manifest):
    segment = json.loads(manifest.read_text(encoding="utf-8"))
    source_id = str(UUID(segment["source_id"]))
    path = SOURCE_DIR / f"{source_id}{segment['extension']}"
    if not path.is_file():
        return
    sources = await db.get("cf_sources", select="status", id=f"eq.{source_id}", limit=1)
    ad = segment["ad"]
    if not sources:
        duration, has_audio = await asyncio.to_thread(probe, path)
        if duration < 1:
            raise MediaError(f"{segment['name']}: фрагмент короче одной секунды. Файл сохранён.")
        await db.request("POST", "cf_sources", body={
            "id": source_id, "name": segment["name"], "extension": segment["extension"],
            "byte_size": path.stat().st_size, "duration": duration, "has_audio": has_audio,
            "clip_seconds": segment["clip_seconds"], "clip_count": MAX_CLIPS, "aspect": "portrait",
            "campaign_id": ad["campaign_id"] if ad else None, "twitch_capture": True,
        })
        return
    if sources[0]["status"] != "completed":
        return
    edits = await db.get("cf_edits", select="id", batch_id=f"eq.{source_id}", limit=1)
    if not edits:
        clips = await db.get("cf_clips", select="id,end_seconds,start_seconds", source_id=f"eq.{source_id}",
                             order="score.desc,start_seconds.asc,id.asc", limit=MAX_CLIPS)
        if clips:
            if ad:
                # Keep the banner visible even in a short final segment.
                shortest = min(clip["end_seconds"] - clip["start_seconds"] for clip in clips)
                if ad["placement"]["start"] >= shortest - 1 / 30:
                    ad["placement"]["start"] = 0
            await queue_edits(db, EditRequest(id=source_id, clip_ids=[clip["id"] for clip in clips],
                                             options=EditOptions(), ad=ad), batch=True)
    manifest.unlink(missing_ok=True)


async def run_twitch_handoffs(state, stopped):
    while not stopped.is_set():
        error = None
        for manifest in sorted(TWITCH_DIR.glob("*.json")):
            if stopped.is_set():
                break
            try:
                await handoff_segment(state.db, manifest)
            except MediaError as exc:
                error = str(exc)
                manifest.replace(manifest.with_suffix(".rejected"))
                logger.warning("%s", error)
            except HTTPException as exc:
                error = str(exc.detail)
                if exc.status_code == 503:
                    break
            except FileNotFoundError:
                continue
            except Exception:
                logger.exception("Ошибка передачи сегмента Twitch %s", manifest.name)
                error = "Не удалось передать сегмент Twitch. Подробности в журнале API."
        if not error:
            rejected = next(TWITCH_DIR.glob("*.rejected"), None)
            if rejected:
                error = "Один или несколько сегментов не прошли проверку FFprobe или короче секунды. Файлы сохранены."
        state.twitch_error = error
        try:
            await asyncio.wait_for(stopped.wait(), timeout=3)
        except TimeoutError:
            pass
