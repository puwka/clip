import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from fractions import Fraction
from uuid import uuid4

from .config import FFMPEG, MAX_BYTES, SOURCE_DIR, TWITCH_DIR, TWITCH_RESERVE_BYTES
from .media import CREATE_FLAGS, MediaError

logger = logging.getLogger(__name__)
DISK_CHECK_SECONDS = 1


def check_space():
    if shutil.disk_usage(SOURCE_DIR).free <= TWITCH_RESERVE_BYTES:
        raise MediaError("Достигнут резерв свободного места для записи Twitch.")


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


class Segment:
    def __init__(self, recording, streams, timestamp, decode_timestamp, transport_stream):
        import av
        self.id = str(uuid4())
        self.start = timestamp
        self.decode_start = decode_timestamp
        self.duration = 0.0
        self.extension = ".ts" if transport_stream else ".mkv"
        self.partial = SOURCE_DIR / f"{self.id}.twitch.part"
        self.final = SOURCE_DIR / f"{self.id}{self.extension}"
        self.manifest = TWITCH_DIR / f"{self.id}.json"
        minutes = recording.minutes
        self.target = minutes * 60
        number = recording.segment + 1
        atomic_json(self.manifest, {
            "source_id": self.id,
            "recording_id": recording.id,
            "name": f"Twitch · {recording.channel} · {number}{self.extension}",
            "extension": self.extension,
            "clip_seconds": recording.clip_seconds,
            "ad": recording.ad,
            "has_webcam": recording.has_webcam,
        })
        self.output = av.open(str(self.partial), mode="w",
                              format="mpegts" if transport_stream else "matroska",
                              options={"avoid_negative_ts": "make_zero"})
        try:
            self.streams = {stream.index: self.output.add_stream_from_template(stream, opaque=True)
                            for stream in streams}
        except BaseException:
            self.output.close()
            raise
        recording.segment = number
        recording.active_minutes = minutes

    def write(self, packet, timestamp):
        stream = self.streams[packet.stream.index]
        offset = round(self.decode_start / packet.time_base)
        end = timestamp + (packet.duration or 0) * packet.time_base
        packet.dts -= offset
        if packet.pts is not None:
            packet.pts -= offset
        packet.stream = stream
        self.output.mux(packet)
        self.duration = max(self.duration, float(end - self.start))

    def close(self):
        self.output.close()
        if self.partial.is_file() and self.partial.stat().st_size:
            self.partial.replace(self.final)
        else:
            self.manifest.unlink(missing_ok=True)


def capture_packets(source, recording):
    import av
    video = next((stream for stream in source.streams.video
                  if not stream.disposition & av.stream.Disposition.attached_pic), None)
    if video is None:
        raise MediaError("Twitch не передал видеодорожку.")
    streams = [video, *source.streams.audio[:1]]
    transport_stream = "mpegts" in source.format.name.split(",")
    segment = None
    next_disk_check = 0
    try:
        for packet in source.demux(streams):
            now = time.monotonic()
            if now >= next_disk_check:
                check_space()
                next_disk_check = now + DISK_CHECK_SECONDS
                if segment is not None and segment.partial.stat().st_size >= MAX_BYTES:
                    raise MediaError("Запись остановлена: сегмент достиг лимита размера файла.")
            if packet.dts is None or not packet.size or packet.is_corrupt:
                continue
            decode_timestamp = Fraction(packet.dts) * packet.time_base
            timestamp = Fraction(packet.pts) * packet.time_base if packet.pts is not None else decode_timestamp
            boundary = packet.stream.index == video.index and packet.is_keyframe
            if boundary and (segment is None or timestamp - segment.start >= segment.target):
                if segment is not None:
                    previous, segment = segment, None
                    previous.close()
                    recording.finished_seconds += previous.duration
                    recording.segment_seconds = 0
                check_space()
                segment = Segment(recording, streams, timestamp, decode_timestamp, transport_stream)
                if not recording.stop.is_set():
                    recording.status = "recording"
            if segment is not None:
                segment.write(packet, timestamp)
                recording.segment_seconds = segment.duration
    finally:
        if segment is not None:
            segment.close()
            recording.finished_seconds += segment.duration
        recording.segment_seconds = 0


class Recording:
    def __init__(self, channel, minutes, clip_seconds, ad, *, has_webcam=False):
        self.id = str(uuid4())
        self.channel = channel
        self.minutes = minutes
        self.clip_seconds = clip_seconds
        self.ad = ad
        self.has_webcam = has_webcam
        self.status = "connecting"
        self.active_minutes = None
        self.segment = 0
        self.segment_seconds = 0.0
        self.finished_seconds = 0.0
        self.error = None
        self.stop = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()

    def stop_capture(self):
        self.stop.set()
        with self.process_lock:
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    def snapshot(self):
        return {
            "id": self.id, "channel": self.channel, "status": self.status, "segment": self.segment,
            "active_minutes": self.active_minutes, "next_minutes": self.minutes,
            "segment_seconds": round(self.segment_seconds),
            "elapsed_seconds": round(self.finished_seconds + self.segment_seconds), "error": self.error,
        }

    def run(self):
        import av
        failures = 0
        try:
            while not self.stop.is_set():
                check_space()
                before = self.finished_seconds
                with self.process_lock:
                    if self.stop.is_set():
                        break
                    process = subprocess.Popen([
                        sys.executable, "-m", "streamlink", "--stdout", "--loglevel", "error",
                        "--stream-timeout", "20", "--stream-segment-timeout", "10",
                        "--ffmpeg-ffmpeg", FFMPEG, "--retry-open", "2", "--twitch-supported-codecs=h264,h265,av1",
                        f"https://www.twitch.tv/{self.channel}", "best",
                    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, creationflags=CREATE_FLAGS)
                    self.process = process
                try:
                    with av.open(process.stdout, mode="r", options={"fflags": "+discardcorrupt"}) as source:
                        capture_packets(source, self)
                except av.error.FFmpegError:
                    logger.info("Вход Twitch прервался: %s", self.channel, exc_info=True)
                finally:
                    process.stdout.close()
                    try:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    finally:
                        with self.process_lock:
                            self.process = None
                if self.stop.is_set():
                    break
                failures = 0 if self.finished_seconds > before else failures + 1
                if failures >= 6:
                    self.status = "finished" if self.segment else "failed"
                    if not self.segment:
                        self.error = "Трансляция недоступна после шести попыток подключения."
                    return
                self.status = "reconnecting"
                self.stop.wait(10)
            self.status = "finished"
        except MediaError as exc:
            self.status = "failed"
            self.error = str(exc)
        except Exception:
            logger.exception("Ошибка записи Twitch")
            self.status = "failed"
            self.error = "Не удалось записать Twitch. Проверьте диск и журнал API."
