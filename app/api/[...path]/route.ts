import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const requestHeaders = ["accept", "content-type", "range", "if-none-match", "if-modified-since", "x-clip-factory"];
const responseHeaders = ["content-type", "content-disposition", "content-range", "accept-ranges", "etag", "last-modified"];

async function forward(request: NextRequest, context: RouteContext<"/api/[...path]">) {
  const base = process.env.CLIP_BACKEND_URL;
  if (!base) return Response.json({ detail: "Внутреннее подключение FastAPI не настроено." }, { status: 503 });

  const { path } = await context.params;
  if (path.some(segment => segment === "." || segment === ".." || /[/\\]/.test(segment))) {
    return Response.json({ detail: "Недопустимый путь API." }, { status: 400 });
  }
  const url = new URL(path.map(encodeURIComponent).join("/"), base.replace(/\/+$/, "") + "/");
  url.search = request.nextUrl.search;
  const headers = new Headers();
  for (const name of requestHeaders) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  try {
    const options: RequestInit & { duplex?: "half" } = {
      method: request.method, headers, cache: "no-store", redirect: "manual", signal: request.signal,
    };
    if (request.body) {
      options.body = request.body;
      options.duplex = "half";
    }
    const upstream = await fetch(url, options);
    const outgoing = new Headers({ "Cache-Control": "private, no-store" });
    for (const name of responseHeaders) {
      const value = upstream.headers.get(name);
      if (value !== null) outgoing.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: outgoing });
  } catch {
    return Response.json({ detail: "Нет связи с внутренним сервисом FastAPI." }, { status: 502 });
  }
}

export { forward as GET, forward as HEAD, forward as POST, forward as PATCH };
