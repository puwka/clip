import asyncio
import re
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .advertising import AdPlacement, Campaign, banner_path, campaign_state
from .advertising_api import router as advertising_router
from .config import BANNER_DIR, CLIP_DIR, EDIT_DIR, EXTENSIONS, FFMPEG, FFPROBE, FRONTEND_ORIGIN, IMPORT_DIR, MAX_BYTES, OLLAMA_MODEL, SOURCE_DIR, TWITCH_DIR, WHISPER_MODEL
from .db import Database
from .editor_api import router as editor_router
from .highlights import MAX_CLIPS, MAX_CLIP_SECONDS, MIN_CLIP_SECONDS, ollama_available, whisper_installed
from .import_api import router as import_router
from .media import MediaError, probe
from .twitch_api import router as twitch_router, run_twitch_handoffs
from .worker import run_worker

SOURCE_FIELDS = (
    "id,name,byte_size,duration,status,progress,error,created_at,updated_at,"
    "campaign_id,cf_campaigns(name),aspect"
)
CLIP_FIELDS = (
    "id,source_id,title,start_seconds,end_seconds,score,category,reason,created_at,"
    "cf_sources!inner(name,aspect,campaign_id,cf_campaigns(name))"
)
CLIP_ORDER = "created_at.desc,score.desc,start_seconds.asc,id.desc"
PAGE_SIZE = 24


@asynccontextmanager
async def lifespan(app):
    SOURCE_DIR.mkdir(exist_ok=True, parents=True)
    CLIP_DIR.mkdir(exist_ok=True, parents=True)
    EDIT_DIR.mkdir(exist_ok=True, parents=True)
    BANNER_DIR.mkdir(exist_ok=True, parents=True)
    IMPORT_DIR.mkdir(exist_ok=True, parents=True)
    TWITCH_DIR.mkdir(exist_ok=True, parents=True)
    app.state.db = Database()
    app.state.recording = None
    app.state.recording_task = None
    app.state.twitch_error = None
    stop = asyncio.Event()
    workers = []
    if app.state.db.configured and shutil.which(FFMPEG) and shutil.which(FFPROBE):
        workers = [
            asyncio.create_task(run_worker(app.state.db, stop)),
            asyncio.create_task(run_worker(app.state.db, stop, ("import",))),
        ]
    app.state.workers = workers
    handoffs = asyncio.create_task(run_twitch_handoffs(app.state, stop))
    try:
        yield
    finally:
        stop.set()
        if app.state.recording_task and not app.state.recording_task.done():
            app.state.recording.stop_capture()
            await app.state.recording_task
        await asyncio.gather(*workers, handoffs)
        await app.state.db.client.aclose()


app = FastAPI(title="Clip Factory", lifespan=lifespan)
app.include_router(editor_router)
app.include_router(advertising_router)
app.include_router(import_router)
app.include_router(twitch_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.add_middleware(
    CORSMiddleware, allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-Clip-Factory"],
)


@app.middleware("http")
async def local_writes(request, call_next):
    if request.method in {"POST", "PATCH"}:
        origin = request.headers.get("origin")
        if (origin and origin != FRONTEND_ORIGIN) or request.headers.get("x-clip-factory") != "local":
            return JSONResponse({"detail": "Запрос записи отклонён локальным сервером."}, status_code=403)
    return await call_next(request)


class Model(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Settings(Model):
    clip_seconds: int = Field(ge=MIN_CLIP_SECONDS, le=MAX_CLIP_SECONDS)
    clip_count: int = Field(default=MAX_CLIPS, ge=1, le=MAX_CLIPS)
    aspect: Literal["landscape", "portrait"]
    twitch_segment_minutes: Literal[15, 60] | None = None


class Assignment(Model):
    campaign_id: UUID | None


Offset = Annotated[int, Query(ge=0)]


def page(rows, offset):
    return {"items": rows[:PAGE_SIZE], "next": offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None}


def with_clip_score(rows):
    return [{**row, "score_percent": round(row["score"] * 100)} for row in rows]


async def require_campaign(db, campaign_id):
    if campaign_id and not await db.get("cf_campaigns", select="id", id=f"eq.{campaign_id}", limit=1):
        raise HTTPException(404, "Кампания не найдена.")


@app.get("/health")
async def health(request: Request):
    db = request.app.state.db
    details = None
    settings = None
    try:
        rows, _, _, _ = await asyncio.gather(
            db.get("cf_settings", id="eq.1", limit=1),
            db.get("cf_clips", select="category,reason", limit=0),
            db.get("cf_imports", select="id", limit=0),
            db.get("cf_edits", select="batch_id", limit=0),
        )
        if not rows:
            raise HTTPException(503, "Выполните supabase/schema.sql в проекте Supabase.")
        settings = rows[0]
    except HTTPException as exc:
        details = exc.detail
    workers = request.app.state.workers
    return {
        "database": settings is not None,
        "ffmpeg": bool(shutil.which(FFMPEG) and shutil.which(FFPROBE)),
        "worker": bool(workers) and all(not worker.done() for worker in workers),
        "ollama": await ollama_available(),
        "ollama_model": OLLAMA_MODEL,
        "whisper": whisper_installed(),
        "whisper_model": WHISPER_MODEL,
        "max_upload_bytes": MAX_BYTES,
        "error": details,
    }


@app.get("/summary")
async def summary(request: Request):
    db = request.app.state.db
    counts, sources, clips = await asyncio.gather(
        db.rpc("cf_summary"),
        db.get("cf_sources", select=SOURCE_FIELDS, order="created_at.desc,id.desc", limit=5),
        db.get("cf_clips", select=CLIP_FIELDS, order=CLIP_ORDER, limit=3),
    )
    return {"counts": counts, "sources": sources, "clips": with_clip_score(clips)}


@app.get("/sources")
async def sources(request: Request, offset: Offset = 0, campaign: UUID | None = None, pending: bool = False):
    filters = {"campaign_id": f"eq.{campaign}"} if campaign else {}
    if pending:
        filters["status"] = "in.(queued,processing)"
    rows = await request.app.state.db.get(
        "cf_sources", select=SOURCE_FIELDS, order="created_at.desc,id.desc",
        limit=PAGE_SIZE + 1, offset=offset, **filters,
    )
    return page(rows, offset)


@app.post("/sources", status_code=201)
async def upload(request: Request, filename: Annotated[str, Query(min_length=1, max_length=255)],
                 campaign: UUID | None = None):
    if not (shutil.which(FFMPEG) and shutil.which(FFPROBE)):
        raise HTTPException(503, "Установите FFmpeg и FFprobe, затем перезапустите сервер.")
    if not whisper_installed():
        raise HTTPException(503, "Установите faster-whisper из backend/requirements.txt.")
    if not await ollama_available():
        raise HTTPException(503, f"Запустите Ollama и установите модель: ollama pull {OLLAMA_MODEL}.")
    name = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    extension = Path(name).suffix.lower()
    if extension not in EXTENSIONS:
        raise HTTPException(415, "Выберите файл MP4, MOV, MKV, WEBM, AVI, FLV, M4V или TS.")
    declared = request.headers.get("content-length")
    if declared and int(declared) > MAX_BYTES:
        raise HTTPException(413, "Размер файла превышает лимит загрузки.")
    db = request.app.state.db
    await require_campaign(db, campaign)
    settings = await db.get("cf_settings", id="eq.1", limit=1)
    if not settings:
        raise HTTPException(503, "Выполните supabase/schema.sql в проекте Supabase.")
    source_id = str(uuid4())
    staging = SOURCE_DIR / f"{source_id}.upload"
    target = SOURCE_DIR / f"{source_id}{extension}"
    size = 0
    keep_file = False
    try:
        with staging.open("xb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Размер файла превышает лимит загрузки.")
                if chunk:
                    await asyncio.to_thread(file.write, chunk)
        if size == 0:
            raise HTTPException(422, "Файл пуст.")
        duration, has_audio = await asyncio.to_thread(probe, staging)
        if duration < MIN_CLIP_SECONDS:
            raise HTTPException(422, "Для анализа нужна запись длительностью не менее 15 секунд.")
        staging.replace(target)
        values = {"clip_seconds": settings[0]["clip_seconds"], "aspect": "portrait"}
        # The queue can claim the row as soon as the INSERT commits.
        keep_file = True
        try:
            await db.request("POST", "cf_sources", body={
                "id": source_id, "name": name, "extension": extension, "byte_size": size,
                "duration": duration, "has_audio": has_audio,
                "campaign_id": str(campaign) if campaign else None, "clip_count": MAX_CLIPS, **values,
            })
        except HTTPException as exc:
            if exc.status_code == 409:
                keep_file = False
            else:
                # A timeout can happen after the row is committed.
                found = await db.get("cf_sources", select="id", id=f"eq.{source_id}")
                if not found:
                    keep_file = False
                    raise exc
        return {"id": source_id}
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Не удалось сохранить файл. Проверьте локальный диск.") from exc
    finally:
        await asyncio.to_thread(staging.unlink, missing_ok=True)
        if not keep_file:
            await asyncio.to_thread(target.unlink, missing_ok=True)


@app.post("/sources/{source_id}/retry")
async def retry_source(request: Request, source_id: UUID):
    db = request.app.state.db
    rows = await db.request(
        "PATCH", "cf_sources", params={"id": f"eq.{source_id}", "status": "eq.failed"},
        body={"status": "queued", "error": None, "progress": 0, "attempts": 0,
              "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not rows:
        raise HTTPException(409, "Повторный запуск доступен для задач с ошибкой.")
    return {"id": str(source_id)}


@app.post("/sources/{source_id}/reanalyze")
async def reanalyze_source(request: Request, source_id: UUID):
    db = request.app.state.db
    records = await db.get("cf_sources", select="extension,duration", id=f"eq.{source_id}", limit=1)
    if not records:
        raise HTTPException(404, "Источник не найден.")
    source = records[0]
    if not (SOURCE_DIR / f"{source_id}{source['extension']}").is_file():
        raise HTTPException(409, "Исходное видео отсутствует на диске.")
    if source["duration"] < MIN_CLIP_SECONDS:
        raise HTTPException(422, "Для анализа нужна запись длительностью не менее 15 секунд.")
    rows = await db.request(
        "PATCH", "cf_sources", params={"id": f"eq.{source_id}", "status": "eq.completed"},
        body={"status": "queued", "error": None, "progress": 0, "attempts": 0,
              "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not rows:
        raise HTTPException(409, "Повторный анализ доступен для завершённых задач.")
    return {"id": str(source_id)}


@app.patch("/sources/{source_id}/campaign")
async def assign(request: Request, source_id: UUID, data: Assignment):
    db = request.app.state.db
    await require_campaign(db, data.campaign_id)
    rows = await db.request("PATCH", "cf_sources", params={"id": f"eq.{source_id}"},
                            body=data.model_dump(mode="json"))
    if not rows:
        raise HTTPException(404, "Источник не найден.")
    return {"id": str(source_id)}


@app.get("/clips")
async def clips(request: Request, offset: Offset = 0, campaign: UUID | None = None, source: UUID | None = None):
    filters = {"cf_sources.campaign_id": f"eq.{campaign}"} if campaign else {}
    if source:
        filters["source_id"] = f"eq.{source}"
    rows = await request.app.state.db.get(
        "cf_clips", select=CLIP_FIELDS, order=CLIP_ORDER,
        limit=PAGE_SIZE + 1, offset=offset, **filters,
    )
    return page(with_clip_score(rows), offset)


@app.get("/clips/{clip_id}/{kind}")
@app.head("/clips/{clip_id}/{kind}", include_in_schema=False)
async def clip_file(request: Request, clip_id: UUID, kind: Literal["poster", "play", "download"]):
    path = CLIP_DIR / f"{clip_id}{'.jpg' if kind == 'poster' else '.mp4'}"
    if not path.is_file():
        raise HTTPException(404, "Файл клипа отсутствует на диске.")
    if kind == "poster":
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    if kind == "play":
        return FileResponse(path, media_type="video/mp4")
    rows = await request.app.state.db.get("cf_clips", select="title", id=f"eq.{clip_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Клип не найден.")
    title = re.sub(r'[<>:"/\\|?*]', "_", rows[0]["title"])[:130]
    return FileResponse(path, media_type="video/mp4", filename=f"{title}.mp4")


@app.get("/clips/{clip_id}")
async def clip_detail(request: Request, clip_id: UUID):
    rows = await request.app.state.db.get("cf_clips", select=CLIP_FIELDS, id=f"eq.{clip_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Клип не найден.")
    return with_clip_score(rows)[0]


def campaign_response(row):
    return {**row, "placement": AdPlacement.model_validate(row["placement"]).model_dump(),
            "availability": campaign_state(row)}


@app.get("/campaigns")
async def campaigns(request: Request, offset: Offset = 0, for_export: bool = False):
    filters = {}
    if for_export:
        today = date.today().isoformat()
        filters = {
            "status": "eq.active", "banner_id": "not.is.null",
            "and": f"(or(starts_on.is.null,starts_on.lte.{today}),or(ends_on.is.null,ends_on.gte.{today}))",
        }
    rows = await request.app.state.db.get(
        "cf_campaigns", select="*", order="created_at.desc,id.desc", limit=PAGE_SIZE + 1, offset=offset, **filters,
    )
    result = page(rows, offset)
    result["items"] = [campaign_response(row) for row in result["items"]]
    return result


def require_banner(banner_id):
    if banner_id and not banner_path(banner_id).is_file():
        raise HTTPException(422, "Загрузите баннер заново: файл отсутствует на диске.")


@app.post("/campaigns", status_code=201)
async def add_campaign(request: Request, data: Campaign):
    require_banner(data.banner_id)
    rows = await request.app.state.db.request("POST", "cf_campaigns", body=data.model_dump(mode="json"))
    return campaign_response(rows[0])


@app.patch("/campaigns/{campaign_id}")
async def edit_campaign(request: Request, campaign_id: UUID, data: Campaign):
    require_banner(data.banner_id)
    rows = await request.app.state.db.request(
        "PATCH", "cf_campaigns", params={"id": f"eq.{campaign_id}"}, body=data.model_dump(mode="json", exclude_unset=True),
    )
    if not rows:
        raise HTTPException(404, "Кампания не найдена.")
    return campaign_response(rows[0])


@app.get("/campaigns/{campaign_id}")
async def campaign_detail(request: Request, campaign_id: UUID):
    rows = await request.app.state.db.get("cf_campaigns", select="*", id=f"eq.{campaign_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Кампания не найдена.")
    return campaign_response(rows[0])


@app.get("/settings")
async def get_settings(request: Request):
    rows = await request.app.state.db.get("cf_settings", id="eq.1", limit=1)
    if not rows:
        raise HTTPException(503, "Выполните supabase/schema.sql в проекте Supabase.")
    return rows[0]


@app.patch("/settings")
async def update_settings(request: Request, data: Settings):
    rows = await request.app.state.db.request(
        "PATCH", "cf_settings", params={"id": "eq.1"},
        body={**data.model_dump(exclude_none=True), "clip_count": MAX_CLIPS, "aspect": "portrait"},
    )
    if not rows:
        raise HTTPException(503, "Выполните supabase/schema.sql в проекте Supabase.")
    if request.app.state.recording and data.twitch_segment_minutes is not None:
        request.app.state.recording.minutes = data.twitch_segment_minutes
    return rows[0]
