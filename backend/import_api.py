from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .link_import import import_link_path, validate_link
from .media import MediaError

router = APIRouter(prefix="/imports", tags=["Импорт записей"])
PAGE_SIZE = 24
FIELDS = "id,name,host,status,progress,error,source_id,created_at,updated_at"


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    url: str = Field(min_length=1, max_length=4096, repr=False)
    name: str = Field(default="", max_length=225)
    allowed: StrictBool
    campaign_id: UUID | None = None
    has_webcam: bool = False


@router.get("")
async def list_imports(request: Request, offset: Annotated[int, Query(ge=0)] = 0,
                       pending: bool = False):
    filters = {"status": "in.(queued,processing)"} if pending else {}
    rows = await request.app.state.db.get(
        "cf_imports", select=FIELDS, order="created_at.desc,id.desc", offset=offset,
        limit=PAGE_SIZE + 1, **filters,
    )
    return {"items": rows[:PAGE_SIZE], "next": offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None}


@router.post("", status_code=201)
async def create_import(request: Request, data: ImportRequest):
    if not data.allowed:
        raise HTTPException(422, "Подтвердите разрешение на скачивание и обработку видео.")
    try:
        url = validate_link(data.url)
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from None

    db = request.app.state.db
    job_id = str(data.id)
    path = import_link_path(job_id)

    async def existing():
        rows = await db.get("cf_imports", select=FIELDS, id=f"eq.{job_id}", limit=1)
        if rows:
            row = rows[0]
            if row["host"] != url.host or row["name"] != data.name:
                raise HTTPException(409, "Этот импорт уже создан с другими параметрами.")
            return row
        return None

    row = await existing()
    if row:
        return row

    settings = await db.get("cf_settings", id="eq.1", limit=1)
    if not settings:
        raise HTTPException(503, "Выполните supabase/schema.sql.")
    campaign = str(data.campaign_id) if data.campaign_id else None
    if campaign and not await db.get("cf_campaigns", select="id", id=f"eq.{campaign}", limit=1):
        raise HTTPException(404, "Кампания не найдена.")
    options = {"clip_seconds": settings[0]["clip_seconds"], "aspect": "portrait"}
    options.update(clip_count=10, campaign_id=campaign, has_webcam=data.has_webcam)

    try:
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(str(url))
        except FileExistsError:
            if path.read_text(encoding="utf-8") != str(url):
                raise HTTPException(409, "Этот импорт уже создан с другой ссылкой.")
    except OSError as exc:
        raise HTTPException(507, "Не удалось сохранить ссылку на этом ПК.") from exc

    try:
        rows = await db.request(
            "POST", "cf_imports", params={"select": FIELDS},
            body={"id": job_id, "name": data.name, "host": url.host, "options": options},
        )
        return rows[0]
    except HTTPException:
        row = await existing()
        if row:
            return row
        # Keep the private link locally if the request timed out. The same ID can be retried.
        raise


@router.post("/{import_id}/retry")
async def retry_import(request: Request, import_id: UUID):
    if not import_link_path(import_id).is_file():
        raise HTTPException(409, "Ссылка отсутствует на этом ПК. Создайте новый импорт.")
    rows = await request.app.state.db.request(
        "PATCH", "cf_imports", params={"id": f"eq.{import_id}", "status": "eq.failed"},
        body={"status": "queued", "progress": 0, "attempts": 0, "error": None,
              "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not rows:
        raise HTTPException(409, "Повторный запуск доступен для импорта с ошибкой.")
    return {"id": str(import_id)}
