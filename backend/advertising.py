from datetime import date
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import os
from typing import Literal
from uuid import UUID, uuid4

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import BANNER_DIR
from .editor_layout import AD_BAND, HEIGHT, WIDTH
from .media import MediaError

MAX_BANNER_BYTES = 10 * 1024**2
# Conservative app presets for vertical, left-to-right feeds. The joint
# preset is the intersection, leaving space for side controls and text.
SAFE_ZONES = {
    "shorts": (96, 420, 888, 1224),
    "tiktok": (90, 260, 840, 1200),
    "both": (96, 420, 840, 1200),
}


class AdPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["both", "shorts", "tiktok"] = "both"
    width: int = Field(default=62, ge=20, le=100)
    x: int = Field(default=50, ge=0, le=100)
    y: int = Field(default=15, ge=0, le=100)
    opacity: int = Field(default=92, ge=1, le=100)
    start: float = Field(default=1, ge=0, lt=180)
    end: float | None = Field(default=None, gt=0, le=180)
    show_code: bool = True

    @model_validator(mode="after")
    def validate_time(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("Время окончания должно быть позже начала.")
        return self


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    brand: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    status: Literal["draft", "active", "paused", "finished"] = "draft"
    promo_code: str = Field(default="", max_length=32, pattern=r"^[^\x00-\x1f\x7f]*$")
    cpm: Decimal = Field(default=Decimal(0), ge=0, le=1_000_000, max_digits=12, decimal_places=4)
    currency: Literal["RUB", "USD", "EUR"] = "RUB"
    starts_on: date | None = None
    ends_on: date | None = None
    expected_views: int = Field(default=0, ge=0, le=1_000_000_000)
    banner_id: UUID | None = None
    placement: AdPlacement = Field(default_factory=AdPlacement)

    @model_validator(mode="after")
    def validate_period(self):
        if self.starts_on and self.ends_on and self.starts_on > self.ends_on:
            raise ValueError("Дата окончания должна быть не раньше начала.")
        return self


class EditAd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    placement: AdPlacement
    expected_views: int = Field(default=0, ge=0, le=1_000_000_000)


class BannerPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    banner_id: UUID
    promo_code: str = Field(default="", max_length=32, pattern=r"^[^\x00-\x1f\x7f]*$")
    placement: AdPlacement


def banner_path(banner_id):
    return BANNER_DIR / f"{UUID(str(banner_id))}.png"


def store_banner(contents):
    try:
        with Image.open(BytesIO(contents), formats=("PNG", "WEBP")) as raw:
            if raw.width > 4096 or raw.height > 4096 or raw.width * raw.height > 12_000_000:
                raise MediaError("Баннер должен быть до 4096 × 4096 и не более 12 Мп.")
            if raw.n_frames != 1:
                raise MediaError("Выберите статичный PNG или WebP.")
            image = raw.convert("RGBA")
            image.info.clear()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise MediaError("Не удалось прочитать баннер. Выберите статичный PNG или WebP.") from exc

    if image.getchannel("A").getbbox() is None:
        raise MediaError("Баннер полностью прозрачный.")
    banner_id = uuid4()
    path = banner_path(banner_id)
    staging = path.with_suffix(".tmp")
    try:
        image.save(staging, format="PNG")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    return {"id": str(banner_id)}


def campaign_state(row, today=None):
    today = (today or date.today()).isoformat()
    if row["status"] != "active":
        return row["status"]
    if row.get("starts_on") and row["starts_on"] > today:
        return "scheduled"
    if row.get("ends_on") and row["ends_on"] < today:
        return "expired"
    if not row.get("banner_id") or not banner_path(row["banner_id"]).is_file():
        return "no_banner"
    return "available"


@lru_cache(maxsize=48)
def promo_font(size):
    font_files = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    for font in font_files:
        if font.is_file():
            return ImageFont.truetype(str(font), size)
    return ImageFont.load_default(size=size)


def banner_canvas(banner_id, promo_code, placement, *, guide=False, band=AD_BAND):
    left, top, right, bottom = SAFE_ZONES[placement.platform]
    left, top, right, bottom = max(left, band[0]), max(top, band[1]), min(right, band[2]), min(bottom, band[3])
    w, h = right - left, bottom - top
    width_limit = round(w * placement.width / 100)
    max_height = min(370, h)
    code = promo_code if placement.show_code else ""
    try:
        with Image.open(banner_path(banner_id)) as image:
            scale = min(width_limit / image.width, (max_height - (48 if code else 0)) / image.height)
            art = image.convert("RGBA").resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError) as exc:
        raise MediaError("Файл рекламного баннера отсутствует или повреждён.") from exc

    if code:
        label = "Промокод " + code
        font_size = 28
        while font_size > 11 and promo_font(font_size).getlength(label) > width_limit - 24:
            font_size -= 1
        font = promo_font(font_size)
        if font.getlength(label) > width_limit - 14:
            label = code
        while font_size > 7 and promo_font(font_size).getlength(label) > width_limit - 14:
            font_size -= 1
        font = promo_font(font_size)
        pill_width = min(width_limit, max(art.width, round(font.getlength(label)) + 24))
        panel = Image.new("RGBA", (pill_width, art.height + 48))
        panel.alpha_composite(art, ((pill_width - art.width) // 2, 0))
        draw = ImageDraw.Draw(panel)
        draw.rounded_rectangle((0, art.height + 4, pill_width - 1, art.height + 47),
                               radius=11, fill=(18, 15, 18, 242))
        draw.text((pill_width / 2, art.height + 25), label, font=font,
                  anchor="mm", fill=(255, 218, 135, 255))
        art = panel

    art.putalpha(art.getchannel("A").point(lambda alpha: round(alpha * placement.opacity / 100)))
    x = left + round((w - art.width) * placement.x / 100)
    y = top + round((h - art.height) * placement.y / 100)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT))
    if guide:
        guide_draw = ImageDraw.Draw(canvas)
        guide_draw.rectangle((left, top, right - 1, bottom - 1), fill=(90, 184, 151, 34),
                             outline=(118, 224, 186, 235), width=5)
    canvas.alpha_composite(art, (x, y))
    return canvas
