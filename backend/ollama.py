import json
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DATA, OLLAMA_MODEL, OLLAMA_URL
from .media import MediaError

SETTINGS_FILE = DATA / "ollama.json"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RETRYABLE = {408, 429, 500, 502, 503, 504}
REQUEST_TIMEOUT = httpx.Timeout(240, connect=5, write=15, pool=5)
condition = threading.Condition()
waiting = deque()
active = False


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=180, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:\-]*$")

    @model_validator(mode="after")
    def normalize(self):
        parsed = urlsplit(self.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or re.search(r"[\s\\]", self.url)):
            raise ValueError("Укажите адрес Ollama в формате http://адрес:11434 или HTTPS.")
        if parsed.port == 0:
            raise ValueError("Порт Ollama должен быть больше нуля.")
        self.url = self.url.rstrip("/")
        if self.url.endswith("/api"):
            self.url = self.url[:-4]
        if ":" not in self.model.rsplit("/", 1)[-1]:
            self.model += ":latest"
        return self

    @property
    def mode(self):
        return "local" if urlsplit(self.url).hostname in LOOPBACK else "network"


def settings():
    try:
        return Connection.model_validate_json(SETTINGS_FILE.read_bytes())
    except FileNotFoundError:
        return Connection(url=OLLAMA_URL, model=OLLAMA_MODEL)


def save(connection):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=SETTINGS_FILE.parent, prefix=".ollama-", delete=False) as temp:
        path = Path(temp.name)
    try:
        path.write_text(connection.model_dump_json(indent=2) + "\n", encoding="utf-8")
        path.replace(SETTINGS_FILE)
    finally:
        path.unlink(missing_ok=True)


def queue_state():
    with condition:
        return {"waiting": len(waiting), "active": int(active)}


@contextmanager
def generation_slot(check):
    global active
    ticket = object()
    deadline = time.monotonic() + 120
    with condition:
        if len(waiting) >= 16:
            raise MediaError("Локальная очередь запросов Ollama заполнена. Повторите задачу позже.")
        waiting.append(ticket)
        try:
            while active or waiting[0] is not ticket:
                check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MediaError("Истекло время ожидания в локальной очереди Ollama.")
                condition.wait(min(0.5, remaining))
            active = True
        finally:
            waiting.remove(ticket)
            condition.notify_all()
    try:
        check()
        yield
    finally:
        with condition:
            active = False
            condition.notify_all()


def client_for(connection):
    return httpx.Client(base_url=connection.url, timeout=REQUEST_TIMEOUT, trust_env=False)


def retry_post(client, route, payload, check, *, timeout=REQUEST_TIMEOUT):
    for attempt in range(3):
        check()
        try:
            response = client.post(route, json=payload, timeout=timeout)
            response.raise_for_status()
            check()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRYABLE or attempt == 2:
                raise
        except httpx.TransportError:
            if attempt == 2:
                raise
        time.sleep(0.75 * 2 ** attempt)


def status(connection):
    try:
        with client_for(connection) as client:
            response = client.post("api/show", json={"model": connection.model}, timeout=httpx.Timeout(4, connect=2))
            if response.status_code == 404:
                return {"state": "missing_model", "detail": f"На сервере Ollama отсутствует модель {connection.model}."}
            response.raise_for_status()
            if "vision" in response.json().get("capabilities", []):
                return {"state": "ready", "detail": "Сервер и модель с поддержкой изображений доступны."}
            return {"state": "no_vision", "detail": "Модель должна поддерживать изображения для анализа видео."}
    except httpx.TimeoutException:
        return {"state": "timeout", "detail": "Сервер Ollama не ответил вовремя."}
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return {"state": "busy" if code in {429, 503} else "error", "detail": f"Сервер Ollama вернул HTTP {code}."}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return {"state": "offline", "detail": "Не удалось подключиться к Ollama по указанному адресу."}


def require_model(client, connection, check):
    try:
        result = retry_post(client, "api/show", {"model": connection.model}, check, timeout=httpx.Timeout(8, connect=3))
        if "vision" not in result.get("capabilities", []):
            raise MediaError(f"Модель {connection.model} должна поддерживать изображения.")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise MediaError(f"На сервере Ollama отсутствует модель {connection.model}.") from exc
        raise MediaError(f"Сервер Ollama вернул HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise MediaError("Не удалось проверить модель. Проверьте адрес и состояние Ollama.") from exc


def chat(client, payload, check):
    with generation_slot(check):
        try:
            return retry_post(client, "api/chat", payload, check)
        except httpx.TimeoutException as exc:
            raise MediaError("Сервер Ollama не завершил генерацию вовремя. Повторите задачу.") from exc
        except httpx.HTTPStatusError as exc:
            raise MediaError(f"Ошибка генерации Ollama: HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise MediaError("Связь с Ollama прервалась. Проверьте сервер и повторите задачу.") from exc
