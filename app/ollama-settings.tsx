"use client";

import { useState, type FormEvent } from "react";
import { request, useResource, type Health, type OllamaConnection, type OllamaMode, type OllamaStatus } from "./lib";
import { ErrorNotice, Loading } from "./ui";

const path = "/settings/ollama";

export function OllamaSettings({ health, refresh }: { health: Health | null | undefined; refresh: () => void }) {
  const { data, error } = useResource<OllamaConnection>(path);
  return <section className="panel settings-panel">
    <h2>Сервер Ollama</h2>
    {health && <p className="muted">
      {health.ollama_mode === "local" ? "Локальный режим" : "Сетевой режим"} · Активных запросов: {health.ollama_queue.active},
      в очереди: {health.ollama_queue.waiting}
    </p>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {data ? <ConnectionForm initial={data} refresh={refresh}/> : !error && <Loading/>}
  </section>;
}

function ConnectionForm({ initial, refresh }: { initial: OllamaConnection; refresh: () => void }) {
  const [url, setUrl] = useState(initial.url);
  const [model, setModel] = useState(initial.model);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OllamaStatus | null>(null);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const test = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "check";
    setBusy(true);
    setResult(null);
    setError("");
    const body = JSON.stringify({ url, model });
    try {
      if (test) {
        const response = await request<OllamaStatus & { mode: OllamaMode }>(path + "/check", { method: "POST", body });
        setResult(response);
      } else {
        const saved = await request<OllamaConnection>(path, { method: "PATCH", body });
        setUrl(saved.url);
        setModel(saved.model);
        setResult({ state: "ready", detail: "Настройки сохранены на этом ПК. Новые задачи используют этот адрес." });
        refresh();
      }
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <form className="form settings-form" onSubmit={submit} onChange={() => { setResult(null); setError(""); }}>
    <fieldset className="form" disabled={busy}>
      <label className="field"><span>Адрес Ollama API</span>
        <input type="url" required maxLength={512} value={url} onChange={event => setUrl(event.target.value)}/>
      </label>
      <label className="field"><span>Модель</span>
        <input required maxLength={180} value={model} onChange={event => setModel(event.target.value)}/>
      </label>
      <p className="muted">Для локального режима укажите http://127.0.0.1:11434, для сетевого — адрес компьютера с Ollama.</p>
    </fieldset>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {result && <p className={result.state === "ready" ? "success" : "muted"} role="status">{result.detail}</p>}
    <div className="flex flex-wrap gap-3">
      <button className="button primary" disabled={busy}>Сохранить</button>
      <button className="button secondary" value="check" disabled={busy}>Проверить подключение</button>
    </div>
  </form>;
}
