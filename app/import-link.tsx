"use client";

import { Link2, LoaderCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { request, type ImportJob } from "./lib";
import { Dialog, ErrorNotice } from "./ui";
import { CampaignField, useWorkspace } from "./workspace";

export function ImportButton({ campaign, goToQueue = false }: { campaign?: string; goToQueue?: boolean }) {
  const [open, setOpen] = useState(false);
  const { refresh } = useWorkspace();
  const router = useRouter();
  return <>
    <button className="button secondary" type="button" onClick={() => setOpen(true)}><Link2 size={17}/> Импорт по ссылке</button>
    {open && <ImportDialog campaign={campaign} close={() => setOpen(false)} done={() => {
      setOpen(false);
      refresh();
      if (goToQueue) router.push("/pipeline");
    }}/>}
  </>;
}

function ImportDialog({ campaign, close, done }: { campaign?: string; close: () => void; done: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [campaignId, setCampaignId] = useState(campaign ?? "");
  const [allowed, setAllowed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const attempted = useRef<{ body: string; id: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!allowed || busy) return;
    setBusy(true);
    setError("");
    const values = { url, name, campaign_id: campaignId || null, allowed };
    const key = JSON.stringify(values);
    if (attempted.current?.body !== key) attempted.current = { body: key, id: crypto.randomUUID() };
    try {
      await request<ImportJob>("/imports", { method: "POST", body: JSON.stringify({ ...values, id: attempted.current.id }) });
      done();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <Dialog title="Импорт по ссылке" close={close} lock={busy}>
    <form className="form" onSubmit={submit}>
      <fieldset className="form" disabled={busy}>
        <label className="field"><span>Прямая ссылка на видеофайл</span><input type="url" required maxLength={4096} placeholder="https://…/stream.mp4" value={url}
          onChange={(event) => setUrl(event.target.value)}/></label>
        <label className="field"><span>Название записи</span><input maxLength={225} placeholder="Автоматически из ссылки" value={name}
          onChange={(event) => setName(event.target.value)}/></label>
        <CampaignField value={campaignId} change={setCampaignId}/>
        <p className="muted">Источник должен разрешать скачивание файла. Используйте прямой адрес, в том числе временную ссылку от владельца записи.</p>
        <label className="edit-toggle"><input type="checkbox" checked={allowed} required onChange={(event) => setAllowed(event.target.checked)}/>
          У меня есть разрешение на скачивание и обработку записи</label>
      </fieldset>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <footer className="dialog-actions">
        <button type="button" className="button secondary" onClick={close} disabled={busy}>Отмена</button>
        <button className="button primary" disabled={!allowed || busy}>{busy ? <LoaderCircle size={16} className="spin"/> : <Link2 size={16}/>} Импортировать</button>
      </footer>
    </form>
  </Dialog>;
}
