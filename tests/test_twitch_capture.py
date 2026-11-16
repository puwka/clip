import json
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import av
from fastapi import HTTPException

from backend import twitch_api, twitch_capture
from backend.config import FFMPEG
from backend.media import probe


def decode(path, digest):
    count = 0
    with av.open(str(path)) as capture:
        for frame in capture.decode(video=0):
            digest.update(frame.to_ndarray().tobytes())
            count += 1
    return count


class TwitchCaptureTest(unittest.TestCase):
    def test_switch_at_keyframe_and_keep_final_segment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "input.ts"
            subprocess.run([
                FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=1",
                "-frames:v", "4650", "-c:v", "libx264", "-g", "5", "-sc_threshold", "0", str(path),
            ], check=True, timeout=60)
            recording = twitch_capture.Recording("test", 15, 30, None)
            with av.open(str(path)) as input_video:
                class Source:
                    streams = input_video.streams
                    format = input_video.format

                    def demux(self, streams):
                        for index, packet in enumerate(input_video.demux(streams)):
                            if index == 25:
                                recording.minutes = 60
                            yield packet

                with (patch.object(twitch_capture, "SOURCE_DIR", directory),
                      patch.object(twitch_capture, "TWITCH_DIR", directory),
                      patch.object(twitch_capture, "TWITCH_RESERVE_BYTES", 0)):
                    twitch_capture.capture_packets(Source(), recording)

            manifests = sorted((json.loads(item.read_text(encoding="utf-8"))
                                for item in directory.glob("*.json")), key=lambda item: item["name"])
            original_digest, output_digest = sha256(), sha256()
            decode(path, original_digest)
            durations, counts = [], []
            for manifest in manifests:
                output = directory / (manifest["source_id"] + manifest["extension"])
                durations.append(probe(output)[0])
                counts.append(decode(output, output_digest))
            self.assertEqual(durations, [900, 3600, 150])
            self.assertEqual(counts, [900, 3600, 150])
            self.assertEqual(original_digest.digest(), output_digest.digest())


class TwitchHandoffTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_after_lost_source_and_export_responses(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_id = str(uuid4())
            (directory / f"{source_id}.ts").write_bytes(b"video")
            manifest = directory / f"{source_id}.json"
            manifest.write_text(json.dumps({
                "source_id": source_id, "extension": ".ts", "name": "Twitch", "clip_seconds": 30, "ad": None,
            }), encoding="utf-8")
            db = Mock()
            db.get = AsyncMock(side_effect=[
                [], [{"status": "queued"}], [{"status": "completed"}], [],
                [{"id": str(uuid4()), "start_seconds": 0, "end_seconds": 15}],
                [{"status": "completed"}], [{"id": str(uuid4())}],
            ])
            db.request = AsyncMock(side_effect=HTTPException(503))
            queue = AsyncMock(side_effect=HTTPException(503))
            with (patch.object(twitch_api, "SOURCE_DIR", directory),
                  patch.object(twitch_api, "probe", return_value=(30, True)),
                  patch.object(twitch_api, "queue_edits", queue)):
                with self.assertRaises(HTTPException):
                    await twitch_api.handoff_segment(db, manifest)
                await twitch_api.handoff_segment(db, manifest)
                with self.assertRaises(HTTPException):
                    await twitch_api.handoff_segment(db, manifest)
                self.assertTrue(manifest.exists())
                await twitch_api.handoff_segment(db, manifest)
            db.request.assert_awaited_once()
            queue.assert_awaited_once()
            self.assertEqual(str(queue.await_args.args[1].id), source_id)
            self.assertFalse(manifest.exists())
