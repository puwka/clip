import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const requestHeaders = ["accept", "content-type", "range", "if-none-match", "if-modified-since", "x-clip-factory"];
const responseHeaders = ["content-type", "content-disposition", "content-range", "accept-ranges", "etag", "last-modified"];

async function forward(request: NextRequest, context: RouteContext<"/api/[...path]">) {
  const base = process.env.CLIP_BACKEND_URL || (process.env.CLIP_SHARED_MODE === "1" ? "http://127.0.0.1:8000" : undefined);
  if (!base) return Response.json({ detail: "Внутреннее подключение FastAPI не настроено." }, { status: 503 });

  try {
    if (request.method === "POST" || request.method === "PATCH") {
      const origin = request.headers.get("origin");
      if (origin && new URL(origin).host !== request.headers.get("host")) {
        return Response.json({ detail: "Запрос с другого сайта отклонён." }, { status: 403 });
      }
    }
    const { path } = await context.params;
    if (path.some(segment => segment === "." || segment === ".." || /[/\\]/.test(segment))) {
      return Response.json({ detail: "Недопустимый путь API." }, { status: 400 });
    }
    const url = new URL(path.map(encodeURIComponent).join("/"), base.replace(/\/+$/, "") + "/");
    url.search = new URL(request.url).search;
    const headers = new Headers();
    for (const name of requestHeaders) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    const options: RequestInit & { duplex?: "half" } = {
      method: request.method, headers, cache: "no-store", redirect: "manual", signal: request.signal,
    };
    if (request.body) {
      options.body = request.body;
      options.duplex = "half";
    }
    const upstream = await fetch(url, options);
    if (upstream.status >= 500 && !upstream.headers.get("content-type")?.includes("application/json")) {
      console.error("FastAPI service returned a platform error", upstream.status, upstream.headers.get("x-vercel-error"));
      await upstream.body?.cancel();
      return Response.json({ detail: "Внутренний сервис FastAPI вернул ошибку запуска." }, { status: 502 });
    }
    const outgoing = new Headers({ "Cache-Control": "private, no-store" });
    for (const name of responseHeaders) {
      const value = upstream.headers.get(name);
      if (value !== null) outgoing.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: outgoing });
  } catch (error) {
    console.error("FastAPI binding request failed", error instanceof Error ? error.name : "UnknownError");
    return Response.json({ detail: "Нет связи с внутренним сервисом FastAPI." }, { status: 502 });
  }
}

export { forward as GET, forward as HEAD, forward as POST, forward as PATCH };
