from io import BytesIO
from statistics import median

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .editor_layout import SHARP_SCALE, scene_filter
from .media import MediaError, read_media


class WebcamRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, lt=100)
    y: float = Field(ge=0, lt=100)
    width: float = Field(ge=2, le=100)
    height: float = Field(ge=2, le=100)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.width > 100 or self.y + self.height > 100:
            raise ValueError("Область веб-камеры должна находиться внутри кадра.")
        return self


class WebcamSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    has_webcam: bool = False
    webcam_region: WebcamRegion | None = None

    @model_validator(mode="after")
    def region_requires_camera(self):
        if self.webcam_region is not None and not self.has_webcam:
            raise ValueError("Включите веб-камеру, чтобы задать её область.")
        return self


def detect_webcam(path, start, end, check):
    import cv2

    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    samples = []
    for fraction in (0.08, 0.28, 0.48, 0.68, 0.88):
        raw = read_media(path, start + fraction * (end - start), 1, [
            "-map", "0:V:0", "-frames:v", "1", "-vf", "scale=960:-2",
            "-c:v", "png", "-threads:v", "1", "-f", "image2pipe",
        ], check=check)
        if not raw:
            continue
        with Image.open(BytesIO(raw)) as frame:
            gray = np.asarray(frame.convert("L"))
        h, w = gray.shape
        faces = detector.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=5,
                                          minSize=(18, 18), maxSize=(w // 3, h // 2))
        samples.append([((x + width / 2) / w, (y + height / 2) / h, width / w, height / h)
                        for x, y, width, height in faces])

    # A webcam stays in the same region across gameplay cuts. Count at most
    # one supporting face per sampled frame and reject ambiguous clusters.
    candidates = []
    for anchor in (face for frame in samples for face in frame):
        x, y, width, height = anchor
        neighbors = []
        for frame in samples:
            matches = [face for face in frame if abs(face[0] - x) <= max(width * .55, .025)
                       and abs(face[1] - y) <= max(height * .55, .035)
                       and .55 <= face[2] / width <= 1.8]
            if matches:
                neighbors.append(min(matches, key=lambda face: abs(face[0] - x) + abs(face[1] - y)))
        candidates.append((len(neighbors), tuple(median(axis) for axis in zip(*neighbors))))

    if candidates:
        count, (x, y, width, height) = max(candidates, key=lambda item: item[0])
        ambiguous = any(n >= count and (abs(face[0] - x) > width or abs(face[1] - y) > height)
                        for n, face in candidates)
        if count >= 3 and not ambiguous:
            # Leave room for the streamer's head, shoulders and natural movement.
            left, right = max(0, x - width * 1.25), min(1, x + width * 1.25)
            top, bottom = max(0, y - height * 1.15), min(1, y + height * 1.35)
            return WebcamRegion(x=round(left * 100, 4), y=round(top * 100, 4),
                                width=round(right * 100, 4) - round(left * 100, 4),
                                height=round(bottom * 100, 4) - round(top * 100, 4))
    raise MediaError("Не удалось уверенно определить веб-камеру. Укажите область в настройках источника и создайте новый экспорт.")


def webcam_filter(region, fps, options):
    fit = f"flags={SHARP_SCALE}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1"
    crop = f"crop=iw*{region.width / 100}:ih*{region.height / 100}:iw*{region.x / 100}:ih*{region.y / 100}"
    # The alpha mask affects only the four corners of the fitted camera image.
    radius = "min(18,min(W,H)/2)"
    dx, dy = (f"max(0,{radius}-min({axis}, {size}-1-{axis}))" for axis, size in (("X", "W"), ("Y", "H")))
    rounded = f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='255*clip({radius}+0.5-hypot({dx},{dy}),0,1)'"
    return (
        f"[0:V:0]fps={fps},setpts=PTS-STARTPTS,split[gamein][camin];"
        f"[gamein]{scene_filter(options)}[game];"
        f"[camin]{crop},scale=360:256:{fit},format=yuva420p,{rounded}[cam];"
        "[game][cam]overlay=x=96:y=264:shortest=1"
    )
