WIDTH = 1080
HEIGHT = 1920
TITLE_BAND = (96, 216, 888, 408)
AD_BAND = (96, 420, 888, 610)
CAPTION_BAND = (96, 1140, 888, 1470)

SHARP_SCALE = "lanczos+accurate_rnd+full_chroma_int"


def scene_filter(options=None):
    x, y = 0.5, 0.5
    if options is not None and options.crop == "fixed":
        x, y = options.center_x / 100, options.center_y / 100

    return (
        f"scale={WIDTH}:{HEIGHT}:flags={SHARP_SCALE}:force_original_aspect_ratio=increase:"
        "force_divisible_by=2:reset_sar=1,"
        f"crop={WIDTH}:{HEIGHT}:iw*{x}-ow/2:ih*{y}-oh/2,setsar=1"
    )


def quality_args(quality="high"):
    crf, ceiling = {"compact": (17, "16M"), "standard": (14, "24M"), "high": (12, "32M")}[quality]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-maxrate", ceiling,
            "-bufsize", str(int(ceiling[:-1]) * 2) + "M", "-threads:v", "4", "-pix_fmt", "yuv420p"]
