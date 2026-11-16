import json
import math
import re
import subprocess

from .config import FFMPEG
from .media import CREATE_FLAGS, MediaError, encode

SAMPLE_RATE = 48000
TARGET_I = -16.0
CEILING = -1.5
TARGET_LRA = 11


def loudness(path):
    settings = f"loudnorm=I={TARGET_I}:TP={CEILING}:LRA={TARGET_LRA}:print_format=json"
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", str(path),
             "-map", "0:a:0", "-af", settings, "-f", "null", "-"],
            capture_output=True, check=True, timeout=180, creationflags=CREATE_FLAGS,
        )
        items = re.findall(r'\{\s*"input_i"\s*:[^{}]*\}', result.stderr.decode("utf-8", errors="replace"))
        return {key: float(value) for key, value in json.loads(items[-1]).items()
                if key in {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}}
    except (subprocess.SubprocessError, IndexError, ValueError) as exc:
        raise MediaError("FFmpeg не смог измерить громкость аудиодорожки.") from exc


def encode_audio(master, destination, duration, check):
    original = loudness(master)
    silent = not math.isfinite(original["input_i"])
    target_i = TARGET_I
    # Give AAC and the final resample a small true-peak margin.
    ceiling = CEILING - 0.20
    output = None
    for _ in range(4):
        check()
        resample = f"aresample={SAMPLE_RATE}"
        if not silent:
            filt = (f"loudnorm=I={target_i:.3f}:TP={ceiling:.3f}:LRA={TARGET_LRA}"
                    f":measured_I={original['input_i']}:measured_TP={original['input_tp']}"
                    f":measured_LRA={original['input_lra']}:measured_thresh={original['input_thresh']}"
                    f":offset={original['target_offset']}:linear=true,{resample}")
        else:
            filt = resample
        encode(["-i", str(master), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                "-af", filt, "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-t", f"{duration:.6f}",
                "-movflags", "+faststart", str(destination)], timeout=180)
        output = loudness(destination)
        if silent:
            break
        difference = TARGET_I - output["input_i"]
        overshoot = output["input_tp"] - CEILING
        if abs(difference) <= 0.20 and overshoot <= 0:
            break
        if not math.isfinite(difference):
            raise MediaError("AAC не сохранил слышимую аудиодорожку.")
        target_i = min(TARGET_I + 4, max(TARGET_I - 4, target_i + difference))
        if overshoot > 0:
            ceiling = max(-9, ceiling - overshoot - 0.10)
    if output["input_tp"] > CEILING:
        raise MediaError("Пик AAC превысил −1,5 dBTP. Готовый файл не опубликован.")
    return {
        "target_lufs_i": TARGET_I, "maximum_dbtp": CEILING, "sample_rate": SAMPLE_RATE,
        "input_lufs_i": None if silent else original["input_i"],
        "export_lufs_i": None if silent else output["input_i"],
        "export_dbtp": output["input_tp"] if math.isfinite(output["input_tp"]) else None,
    }
