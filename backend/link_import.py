import ipaddress
import math
import re
import socket
import time
from pathlib import PurePosixPath
from urllib.parse import unquote

import httpx

from .config import EXTENSIONS, IMPORT_DIR, MAX_BYTES, SOURCE_DIR
from .highlights import MIN_CLIP_SECONDS
from .media import MediaError, probe

MIME_EXTENSIONS = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-matroska": ".mkv", "video/x-msvideo": ".avi", "video/x-flv": ".flv",
    "video/x-m4v": ".m4v", "video/mp2t": ".ts",
}
REDIRECTS = {301, 302, 303, 307, 308}


def import_link_path(job_id):
    return IMPORT_DIR / f"{job_id}.url"


def public_ip(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and (ip.is_site_local or ip.sixtofour or ip.teredo):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def validate_link(value):
    if not value or len(value) > 4096 or any(ord(ch) < 32 or ch == "\\" for ch in value):
        raise MediaError("Некорректная ссылка на видео.")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise MediaError("Некорректная ссылка на видео.") from None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.port is not None:
        raise MediaError("Нужна прямая HTTP(S)-ссылка без логина и нестандартного порта.")
    host = url.host.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost", ".internal")):
            raise MediaError("Импорт доступен только с публичных адресов.")
    else:
        if not public_ip(str(literal)):
            raise MediaError("Импорт доступен только с публичных адресов.")
    if PurePosixPath(url.path).suffix.lower() in {".m3u8", ".mpd", ".html", ".htm"}:
        raise MediaError("Вставьте ссылку на файл видео.")
    return url.copy_with(fragment=None)


def pinned_address(url):
    host = url.raw_host.decode("ascii").rstrip(".")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, 443 if url.scheme == "https" else 80,
                                                              type=socket.SOCK_STREAM)}
        if not addresses or not all(public_ip(ip) for ip in addresses):
            raise MediaError("Источник перенаправляет на закрытый или локальный адрес.")
    except (socket.gaierror, ValueError):
        raise MediaError("Не удалось найти публичный адрес источника.") from None
    return host, min(addresses, key=lambda ip: (":" in ip, ip))


def download(url, path, progress, check):
    deadline = time.monotonic() + 3 * 3600
    for hop in range(6):
        check()
        host, ip = pinned_address(url)
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False) as client:
                with client.stream("GET", url.copy_with(host=ip),
                                   headers={"Host": url.netloc.decode("ascii"), "Accept-Encoding": "identity",
                                            "User-Agent": "ClipFactory/1.0"},
                                   extensions={"sni_hostname": host}) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise MediaError("Источник вернул пустое перенаправление.")
                        destination = validate_link(str(url.join(location)))
                        if url.scheme == "https" and destination.scheme != "https":
                            raise MediaError("Защищённая ссылка ведёт на незашифрованный адрес.")
                        url = destination
                        continue
                    if response.status_code in {401, 403, 451}:
                        raise MediaError("Источник запретил скачивание. Проверьте разрешение или получите новую ссылку.")
                    if response.status_code != 200:
                        raise MediaError(f"Источник вернул HTTP {response.status_code}.")
                    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if mime and not mime.startswith("video/") and mime not in {"application/octet-stream", "binary/octet-stream"}:
                        raise MediaError("Ссылка ведёт на страницу или поток. Нужна прямая ссылка на видеофайл.")
                    encoding = response.headers.get("content-encoding", "").strip().lower()
                    if encoding not in ("", "identity"):
                        raise MediaError("Источник прислал сжатый ответ. Используйте прямую ссылку на видеофайл.")
                    raw_length = response.headers.get("content-length", "")
                    total = int(raw_length) if raw_length and raw_length.isascii() and raw_length.isdigit() and len(raw_length) <= 20 else 0
                    if total > MAX_BYTES:
                        raise MediaError("Видео превышает допустимый размер загрузки.")
                    size = 0
                    with path.open("xb") as file:
                        for chunk in response.iter_raw(chunk_size=1024 * 1024):
                            check()
                            if time.monotonic() > deadline:
                                raise MediaError("Загрузка не завершилась за три часа.")
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise MediaError("Видео превышает допустимый размер загрузки.")
                            file.write(chunk)
                            percent = size / total if total else size / (size + 32 * 1024**2)
                            progress(2 + min(89, int(89 * percent)))
                    if not size or total and size != total:
                        raise MediaError("Источник прислал пустой или неполный видеофайл.")
                    name = re.sub(r"[\x00-\x1f\x7f<>:\\|?*/]", "_", unquote(PurePosixPath(url.path).name))
                    extension = MIME_EXTENSIONS.get(mime) or PurePosixPath(name).suffix.lower()
                    return size, name, extension if extension in EXTENSIONS else ".mp4"
        except (httpx.RequestError, httpx.InvalidURL):
            raise MediaError("Загрузка прервалась или источник недоступен. Проверьте ссылку и повторите.") from None
        except OSError:
            raise MediaError("Не удалось сохранить видео. Проверьте локальный диск.") from None
    raise MediaError("Источник вернул слишком много перенаправлений.")


def process_import(record, token, progress, check, outputs):
    try:
        link = import_link_path(record["id"]).read_text(encoding="utf-8")
    except OSError:
        raise MediaError("Файл ссылки отсутствует на этом ПК. Создайте новый импорт.") from None
    url = validate_link(link)
    staging = SOURCE_DIR / f"{token}.import"
    outputs.append(staging)
    try:
        size, name, extension = download(url, staging, progress, check)
        progress(94)
        duration, has_audio = probe(staging)
        if not math.isfinite(duration) or duration < MIN_CLIP_SECONDS:
            raise MediaError("Для анализа нужна запись длительностью не менее 15 секунд.")
        check()
        stem = PurePosixPath(record.get("name") or name).stem[:225].strip(" .") or "Импорт записи"
        target = SOURCE_DIR / f"{token}{extension}"
        outputs.append(target)
        staging.replace(target)
        return {"id": token, "name": stem + extension, "byte_size": size, "duration": duration,
                "has_audio": has_audio, "extension": extension, **record["options"]}
    finally:
        staging.unlink(missing_ok=True)
