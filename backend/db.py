import asyncio
import logging

import httpx
from fastapi import HTTPException

from .config import SUPABASE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.configured = bool(SUPABASE_URL and SUPABASE_KEY)
        self.client = httpx.AsyncClient(timeout=12)
        self.headers = {"apikey": SUPABASE_KEY, "Prefer": "return=representation"}
        if SUPABASE_KEY.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {SUPABASE_KEY}"

    async def request(self, method, path, *, params=None, body=None, retry=False):
        if not self.configured:
            raise HTTPException(503, "Укажите SUPABASE_URL и SUPABASE_SECRET_KEY в файле .env.")

        for attempt in range(2 if retry else 1):
            try:
                response = await self.client.request(
                    method, f"{SUPABASE_URL}/rest/v1/{path}",
                    params=params, json=body, headers=self.headers,
                )
                response.raise_for_status()
                return response.json() if response.content else None
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                logger.warning("Supabase %s %s: HTTP %s", method, path, code)
                if code in (401, 403, 404) or (code == 400 and b'"42703"' in exc.response.content):
                    raise HTTPException(503, "Проверьте ключ, Data API и выполнение supabase/schema.sql.") from exc
                if exc.response.status_code == 409:
                    raise HTTPException(409, "Связанная запись недоступна или операция уже выполнена.") from exc
            except (httpx.RequestError, ValueError):
                logger.warning("Не удалось получить ответ Supabase: %s %s", method, path)
            if retry and attempt == 0:
                await asyncio.sleep(0.4)
        raise HTTPException(503, "Supabase временно недоступен. Проверьте соединение.")

    async def get(self, table, **params):
        return await self.request("GET", table, params=params, retry=True)

    async def rpc(self, name, body=None):
        return await self.request("POST", f"rpc/{name}", body=body or {})
