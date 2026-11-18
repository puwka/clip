import json
from pathlib import Path
from uuid import UUID

from .config import DATA
from .twitch_capture import atomic_json

DIRECTORY = DATA / "recovery"


def checkpoint(kind, record, token, result, outputs):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DIRECTORY / f"{UUID(token)}.json"
    atomic_json(path, {"kind": kind, "record": record, "token": token,
                       "result": result, "outputs": [str(p.resolve()) for p in outputs]})
    return path


def previous(kind, record):
    # A reclaimed job receives a new lease. Reuse completed local work only
    # when all rendering inputs match the snapshot that produced it.
    keys = {
        "edit": ("id", "segments", "options", "ad"),
        "source": ("id", "extension", "clip_seconds", "duration", "has_audio"),
        "import": ("id", "options", "name", "host"),
    }[kind]
    for path in sorted(DIRECTORY.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["kind"] != kind:
            continue
        if any(record.get(key) != saved["record"].get(key) for key in keys):
            continue
        if all(Path(item).is_file() for item in saved["outputs"]):
            return path, saved
    return None, None
