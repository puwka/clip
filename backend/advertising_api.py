import asyncio
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .advertising import BannerPreview, MAX_BANNER_BYTES, banner_canvas, banner_path, store_banner
from .media import MediaError

router = APIRouter(prefix="/campaign-banners", tags=["Рекламные баннеры"])


@router.post("", status_code=201)
async def upload_banner(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BANNER_BYTES:
            raise HTTPException(413, "Баннер должен весить не более 10 МБ.")
        data.extend(chunk)
    try:
        return await asyncio.to_thread(store_banner, data)
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Не удалось сохранить баннер на диск.") from exc


@router.post("/preview")
async def preview_banner(payload: BannerPreview):
    def render():
        image = banner_canvas(payload.banner_id, payload.promo_code, payload.placement, guide=True)
        output = BytesIO()
        image.resize((270, 480)).save(output, format="PNG")
        return output.getvalue()

    try:
        return Response(await asyncio.to_thread(render), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    except MediaError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{banner_id}")
async def get_banner(banner_id: UUID):
    path = banner_path(banner_id)
    if not path.is_file():
        raise HTTPException(404, "Баннер не найден.")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})
