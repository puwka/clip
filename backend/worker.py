import asyncio
import logging
import threading
from uuid import uuid4

from fastapi import HTTPException

from .config import CLIP_DIR, EDIT_DIR
from .advertising import banner_path, campaign_state
from .editor import render_edit
from .link_import import import_link_path, process_import
from .media import MediaError, process_source

logger = logging.getLogger(__name__)
QUEUES = {
    "source": ("cf", "p_clips"),
    "edit": ("cf_edit", "p_duration"),
    "import": ("cf_import", "p_source"),
}


class LeaseLost(Exception):
    pass


async def process_job(db, record, token, stop, kind):
    progress = 1
    aborted = threading.Event()
    outputs = []
    committing = False

    def set_progress(value):
        nonlocal progress
        progress = max(progress, value)

    def check():
        if aborted.is_set():
            raise LeaseLost

    identity = {"p_id": record["id"], "p_token": token}
    prefix, result_field = QUEUES[kind]

    async def heartbeat():
        sent = 1
        silence = 0
        while not aborted.is_set():
            await asyncio.sleep(3)
            if stop.is_set():
                aborted.set()
                return
            silence += 3
            if progress == sent and silence < 15:
                continue
            try:
                if not await db.rpc(prefix + "_renew", {**identity, "p_progress": progress}):
                    aborted.set()
                    return
                sent, silence = progress, 0
            except HTTPException:
                logger.warning("Не удалось продлить задачу %s. Будет повторная попытка.", record["id"])

    pulse = asyncio.create_task(heartbeat())
    previous_clips = []
    try:
        try:
            if kind == "edit":
                outputs.extend((EDIT_DIR / f"{token}.mp4", EDIT_DIR / f"{token}.review.json"))
                if record.get("ad"):
                    campaigns = await db.get("cf_campaigns", id=f"eq.{record['campaign_id']}", limit=1)
                    if (not campaigns or campaign_state(campaigns[0]) != "available" or
                            not banner_path(record["ad"]["banner_id"]).is_file()):
                        raise MediaError("Кампания выключена, завершилась или файл баннера недоступен.")
                result = await asyncio.to_thread(render_edit, record, token, set_progress, check)
            elif kind == "import":
                result = await asyncio.to_thread(process_import, record, token, set_progress, check, outputs)
            else:
                previous_clips = await db.get("cf_clips", select="id,category", source_id=f"eq.{record['id']}")
                result = await asyncio.to_thread(process_source, record, set_progress, check, outputs)
            check()
            error = None
        except LeaseLost:
            return
        except MediaError as exc:
            logger.warning("Ошибка обработки задачи %s: %s", record["id"], exc)
            result = [] if kind == "source" else None
            error = str(exc)
        except Exception as exc:
            logger.exception("Ошибка обработки задачи %s", record["id"])
            result = [] if kind == "source" else None
            if isinstance(exc, HTTPException):
                error = str(exc.detail)
            else:
                error = "Ошибка обработки. Проверьте свободное место и журнал API."
        if not aborted.is_set() and not stop.is_set():
            # A lost HTTP response may hide a successful commit. Keep its files.
            committing = error is None
            accepted = await db.rpc(prefix + "_finish", {**identity, result_field: result, "p_error": error})
            committing = accepted and error is None
            if committing:
                if kind == "import":
                    remove_output(import_link_path(record["id"]))
                for clip in previous_clips:
                    for extension in (".mp4", ".jpg"):
                        remove_output(CLIP_DIR / (clip["id"] + extension))
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        if not committing:
            for path in outputs:
                remove_output(path)


def remove_output(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Не удалось удалить файл %s", path.name)


async def run_worker(db, stop, kinds=("source", "edit")):
    first = 0
    delay = 2
    while not stop.is_set():
        processed = False
        for i in range(len(kinds)):
            index = (first + i) % len(kinds)
            kind = kinds[index]
            if stop.is_set():
                return
            token = str(uuid4())
            try:
                jobs = await db.rpc(QUEUES[kind][0] + "_claim", {"p_token": token})
                delay = 2
                if jobs:
                    await process_job(db, jobs[0], token, stop, kind)
                    first, processed = (index + 1) % len(kinds), True
                    break
            except Exception:
                logger.exception("Очередь Clip Factory временно недоступна")
                delay = min(30, delay * 2)
        if processed:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
