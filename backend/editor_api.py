import asyncio
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import UUID, uuid5
from zipfile import ZIP_STORED, ZipFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .advertising import campaign_state
from .config import EDIT_DIR, FFMPEG, FFPROBE, SOURCE_DIR
from .editor import FPS, EditRequest, plan_edit
from .highlights import whisper_installed
from .media import MediaError

router = APIRouter(prefix="/edits", tags=["Auto Editor"])
FIELDS = "id,segments,options,campaign_id,ad,batch_id,batch_position,status,progress,error,duration,created_at,updated_at"
PAGE_SIZE = 16


@router.get("")
async def list_edits(request: Request, offset: Annotated[int, Query(ge=0)] = 0, pending: bool = False):
    filters = {"status": "in.(queued,processing)"} if pending else {}
    rows = await request.app.state.db.get(
        "cf_edits", select=FIELDS, order="created_at.desc,id.desc", offset=offset, limit=PAGE_SIZE + 1, **filters,
    )
    return {"items": rows[:PAGE_SIZE], "next": offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None}


@router.post("", status_code=201)
async def create_edit(request: Request, payload: EditRequest):
    return (await queue_edits(request.app.state.db, payload, batch=False))[0]


@router.post("/batches", status_code=201)
async def create_batch(request: Request, payload: EditRequest):
    if len(payload.clip_ids) < 2:
        raise HTTPException(422, "Выберите минимум два фрагмента для пакета.")
    return {"id": str(payload.id), "items": await queue_edits(request.app.state.db, payload, batch=True)}


async def queue_edits(db, payload, *, batch):
    edit_id = str(payload.id)
    ids = [str(clip_id) for clip_id in payload.clip_ids]
    options = payload.options.model_dump()
    campaign_id = str(payload.ad.campaign_id) if payload.ad else None
    batch_id = edit_id if batch else None
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Каждый фрагмент можно добавить один раз.")

    async def existing():
        filters = {"batch_id": f"eq.{edit_id}", "order": "batch_position.asc"} if batch else {"id": f"eq.{edit_id}", "limit": 1}
        rows = await db.get("cf_edits", select=FIELDS, **filters)
        if not rows:
            return None
        requested_groups = [[clip_id] for clip_id in ids] if batch else [ids]
        if len(rows) != len(requested_groups) or any(
            not edit_matches(row, group, options, payload.ad, campaign_id, batch_id)
            for row, group in zip(rows, requested_groups)
        ):
            raise HTTPException(409, "Этот экспорт уже создан с другими параметрами.")
        return rows

    row = await existing()
    if row:
        return row
    if not (shutil.which(FFMPEG) and shutil.which(FFPROBE)):
        raise HTTPException(503, "Для монтажа требуются FFmpeg и FFprobe.")
    if payload.options.captions and not whisper_installed():
        raise HTTPException(503, "Для субтитров установите faster-whisper.")
    rows = await db.get(
        "cf_clips", select="id,title,source_id,start_seconds,end_seconds,cf_sources!inner(extension,has_audio,has_webcam,webcam_region)",
        id="in.(" + ",".join(ids) + ")",
    )
    indexed = {row["id"]: row for row in rows}
    if len(indexed) != len(ids):
        raise HTTPException(409, "Некоторые клипы недоступны. Обновите выбор фрагментов.")
    segments = []
    for clip_id in ids:
        clip = indexed[clip_id]
        source = clip["cf_sources"]
        if not (SOURCE_DIR / f"{clip['source_id']}{source['extension']}").is_file():
            raise HTTPException(409, "Исходник одного из фрагментов отсутствует на диске.")
        segments.append({
            "clip_id": clip_id, "title": clip["title"], "source_id": clip["source_id"],
            "start_seconds": clip["start_seconds"], "end_seconds": clip["end_seconds"], **source,
        })
    groups = [[segment] for segment in segments] if batch else [segments]
    durations = []
    for group in groups:
        try:
            frames, overlap = plan_edit(group, payload.options)
        except MediaError as exc:
            raise HTTPException(422, str(exc)) from exc
        durations.append((sum(frames) - (len(frames) - 1) * overlap) / FPS)
    ad = None
    if payload.ad:
        rows = await db.get("cf_campaigns", id=f"eq.{campaign_id}", limit=1)
        if not rows or campaign_state(rows[0]) != "available":
            raise HTTPException(409, "Кампания недоступна. Проверьте статус, период и баннер.")
        if payload.ad.placement.start >= min(durations):
            raise HTTPException(422, "Начало показа рекламы должно находиться внутри каждого ролика.")
        campaign = rows[0]
        ad = {key: campaign[key] for key in ("name", "brand", "banner_id", "promo_code", "cpm", "currency")}
        ad.update(placement=payload.ad.placement.model_dump(), expected_views=payload.ad.expected_views)
    values = [{
        "id": str(uuid5(payload.id, group[0]["clip_id"])) if batch else edit_id,
        "segments": group, "options": options, "campaign_id": campaign_id, "ad": ad,
        "batch_id": batch_id, "batch_position": index if batch else None,
    } for index, group in enumerate(groups, start=1)]
    try:
        rows = await db.request("POST", "cf_edits", params={"select": FIELDS}, body=values)
        return sorted(rows, key=lambda row: row["batch_position"] or 0)
    except HTTPException as exc:
        if exc.status_code in (409, 503):
            row = await existing()
            if row:
                return row
        raise


def edit_matches(row, ids, options, ad, campaign_id, batch_id):
    if row["batch_id"] != batch_id or row["campaign_id"] != campaign_id or row["options"] != options:
        return False
    if [part["clip_id"] for part in row["segments"]] != ids:
        return False
    actual = row["ad"]
    return (actual is None if not ad else actual is not None and
            actual["placement"] == ad.placement.model_dump() and actual["expected_views"] == ad.expected_views)


async def batch_rows(request, batch_id, fields=FIELDS):
    rows = await request.app.state.db.get("cf_edits", batch_id=f"eq.{batch_id}", select=fields, order="batch_position.asc")
    if not rows:
        raise HTTPException(404, "Пакет экспорта не найден.")
    return rows


@router.get("/batches/{batch_id}")
async def get_batch(request: Request, batch_id: UUID):
    return {"id": str(batch_id), "items": await batch_rows(request, batch_id)}


def export_name(row):
    title = row["options"].get("title", "") or row.get("segments", [{}])[0].get("title", "")
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:72] or "Clip Factory Short"


def write_archive(files):
    with NamedTemporaryFile(prefix="batch-", suffix=".zip", dir=EDIT_DIR, delete=False) as temporary:
        target = Path(temporary.name)
    try:
        with ZipFile(target, "w", compression=ZIP_STORED) as archive:
            for path, name in files:
                archive.write(path, name)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


@router.get("/batches/{batch_id}/download")
async def download_batch(request: Request, batch_id: UUID):
    rows = await batch_rows(request, batch_id, FIELDS + ",artifact_id")
    files = []
    for row in rows:
        if row["status"] == "completed" and row["artifact_id"]:
            path = EDIT_DIR / f"{row['artifact_id']}.mp4"
            if not path.is_file():
                raise HTTPException(404, "Один из готовых MP4 отсутствует на диске.")
            files.append((path, f"{row['batch_position']:02d} - {export_name(row)}.mp4"))
    if not files:
        raise HTTPException(409, "В пакете пока нет готовых роликов.")
    try:
        archive = await asyncio.to_thread(write_archive, files)
    except OSError as exc:
        raise HTTPException(507, "Не удалось создать ZIP. Проверьте свободное место.") from exc
    return FileResponse(archive, media_type="application/zip", filename="Clip Factory Shorts.zip",
                        background=BackgroundTask(archive.unlink, missing_ok=True))


@router.get("/{edit_id}")
async def get_edit(request: Request, edit_id: UUID):
    rows = await request.app.state.db.get("cf_edits", select=FIELDS, id=f"eq.{edit_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Монтаж не найден.")
    return rows[0]


@router.post("/{edit_id}/retry")
async def retry_edit(request: Request, edit_id: UUID):
    rows = await request.app.state.db.request(
        "PATCH", "cf_edits", params={"id": f"eq.{edit_id}", "status": "eq.failed"},
        body={"status": "queued", "progress": 0, "error": None, "attempts": 0,
              "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not rows:
        raise HTTPException(409, "Повторить можно монтаж, завершившийся ошибкой.")
    return {"id": str(edit_id)}


@router.get("/{edit_id}/{kind}")
@router.head("/{edit_id}/{kind}", include_in_schema=False)
async def edit_file(request: Request, edit_id: UUID, kind: Literal["play", "download"]):
    rows = await request.app.state.db.get(
        "cf_edits", select="options,artifact_id", id=f"eq.{edit_id}", status="eq.completed", limit=1,
    )
    if not rows or not rows[0]["artifact_id"]:
        raise HTTPException(404, "Готовый монтаж не найден.")
    path = EDIT_DIR / f"{rows[0]['artifact_id']}.mp4"
    if not path.is_file():
        raise HTTPException(404, "MP4 отсутствует на диске.")
    if kind == "play":
        return FileResponse(path, media_type="video/mp4")
    title = rows[0]["options"].get("title", "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:72]
    return FileResponse(path, media_type="video/mp4", filename=f"{name or 'Clip Factory Short'}.mp4")
