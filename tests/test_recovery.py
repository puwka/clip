import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from backend import recovery, worker


class RecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_lost_finish_response_reuses_completed_work_on_new_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = directory / "clip.mkv"
            record = {"id": str(uuid4()), "extension": ".mp4", "duration": 20}
            first_stop = asyncio.Event()
            db = Mock()
            db.get = AsyncMock(return_value=[])

            def render(record, progress, check, outputs):
                output.write_bytes(b"finished video")
                outputs.append(output)
                return [{"id": str(uuid4())}]

            async def lose_response(*args):
                first_stop.set()
                raise HTTPException(503)

            db.rpc = AsyncMock(side_effect=lose_response)
            with patch.object(recovery, "DIRECTORY", directory / "recovery"), patch.object(worker, "process_source", side_effect=render) as process:
                await worker.process_job(db, record, str(uuid4()), first_stop, "source")
                self.assertTrue(output.is_file())
                self.assertEqual(len(list(recovery.DIRECTORY.glob("*.json"))), 1)
                db.rpc = AsyncMock(return_value=True)
                await worker.process_job(db, record, str(uuid4()), asyncio.Event(), "source")
                process.assert_called_once()
                self.assertTrue(output.is_file())
                self.assertFalse(list(recovery.DIRECTORY.glob("*.json")))

    def test_changed_options_do_not_reuse_wrong_export(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = directory / "video.mp4"
            output.write_bytes(b"video")
            record = {"id": str(uuid4()), "options": {"title": "Original"}}
            with patch.object(recovery, "DIRECTORY", directory / "recovery"):
                recovery.checkpoint("edit", record, str(uuid4()), 15, [output])
                changed = {**record, "options": {"title": "New"}}
                self.assertEqual(recovery.previous("edit", changed), (None, None))
