"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Clapperboard, FolderOpen, LayoutGrid, ListTodo, Megaphone, Settings2, Upload, Plus, Scissors, Video, HardDrive, LoaderCircle, WandSparkles } from "lucide-react";
import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { type Campaign, type Health, type Page, fileSize, request, uploadVideo, useResource } from "./lib";
import { WebcamToggle } from "./webcam";
import { Dialog, ErrorNotice } from "./ui";

const nav = [
  { path: "/", title: "Дашборд", icon: LayoutGrid },
  { path: "/pipeline", title: "Конвейер", icon: ListTodo },
  { path: "/sources", title: "Источники", icon: FolderOpen },
  { path: "/clips", title: "Нарезки", icon: Clapperboard },
  { path: "/editor", title: "Auto Editor", shortTitle: "Монтаж", icon: WandSparkles },
  { path: "/campaigns", title: "Рекламные кампании", shortTitle: "Кампании", icon: Megaphone },
  { path: "/settings", title: "Настройки", icon: Settings2 },
];

const WorkspaceContext = createContext({
  openUpload: (_campaign?: string) => {}, refresh: () => {}, revision: 0,
  health: null as Health | null, healthError: "",
});
export const useWorkspace = () => useContext(WorkspaceContext);

export function Workspace({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [revision, setRevision] = useState(0);
  const [upload, setUpload] = useState<{ campaign: string } | null>(null);
  const { data: health, error: healthError } = useResource<Health>("/health", revision, 6000);
  const ready = health?.database && health.ffmpeg && health.worker && health.ollama && health.whisper && !healthError;
  const status = ready ? "Готово к работе" : health || healthError ? "Требуется настройка" : "Проверка системы";
  const current = nav.find(({ path }) => path === pathname)?.title;
  const refresh = () => setRevision((n) => n + 1);
  const openUpload = (campaign = "") => setUpload({ campaign });
  return <WorkspaceContext.Provider value={{ openUpload, refresh, revision, health, healthError }}>
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="Clip Factory — дашборд">
          <span className="brand-mark"><Scissors size={21} strokeWidth={2.4} /></span>
          <div><strong>Clip Factory</strong><span>Локальная студия</span></div>
        </Link>
        <nav className="navigation" aria-label="Основная навигация">
          {nav.map(({ path, title, shortTitle, icon: Icon }) => <Link href={path} key={path} title={title} aria-label={title} aria-current={pathname === path ? "page" : undefined}>
            <Icon size={19} strokeWidth={1.75}/><span className={shortTitle ? "nav-long" : undefined}>{title}</span>
            {shortTitle && <span className="nav-short">{shortTitle}</span>}
          </Link>)}
        </nav>
        <div className="sidebar-footer"><HardDrive size={17} /><div>Локальное хранилище<small>Видео на этом ПК</small></div></div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb"><span>Студия</span><span aria-hidden="true">/</span><strong>{current}</strong></div>
          <Link className={"system-status " + (ready ? "is-ready" : "")} href="/settings" title={status}><span className="status-dot"/>{status}</Link>
        </header>
        <main className="workspace">{children}</main>
      </div>
    </div>
    {upload && <UploadDialog campaignId={upload.campaign} close={() => setUpload(null)} done={() => { setUpload(null); refresh(); }} />}
  </WorkspaceContext.Provider>;
}

export function UploadButton({ children = "Загрузить видео", campaign }: { children?: ReactNode; campaign?: string }) {
  const { openUpload } = useWorkspace();
  return <button className="button primary" type="button" onClick={() => openUpload(campaign)}><Plus size={18}/> {children}</button>;
}

export function CampaignField({ value, change, initial = [], forExport = false }: {
  value: string; change: (value: string) => void; initial?: Pick<Campaign, "id" | "name">[]; forExport?: boolean;
}) {
  const { revision } = useWorkspace();
  const base = forExport ? "/campaigns?for_export=true" : "/campaigns";
  const { data, error } = useResource<Page<Campaign>>(base, revision);
  const [extra, setExtra] = useState<Campaign[]>([]);
  const [offset, setOffset] = useState<number | null>();
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const items = new Map([...initial, ...(data?.items ?? []), ...extra].map((item) => [item.id, item.name]));
  const next = offset === undefined ? data?.next : offset;
  async function loadMore() {
    if (!next || loading) return;
    setLoading(true);
    setLoadError("");
    try {
      const page = await request<Page<Campaign>>(base + (forExport ? "&" : "?") + "offset=" + next);
      setExtra((current) => [...current, ...page.items]);
      setOffset(page.next);
    } catch (error) { setLoadError((error as Error).message); }
    finally { setLoading(false); }
  }
  return <div className="campaign-field">
    <label className="field"><span>Рекламная кампания</span>
      <select value={value} onChange={(event) => change(event.target.value)}>
        <option value="">{forExport ? "Без рекламы" : "Без кампании"}</option>
        {Array.from(items, ([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select>
    </label>
    {next && <button type="button" className="text-link" onClick={loadMore} disabled={loading}>Показать ещё кампании</button>}
    {(error || loadError) && <small className="muted">{error || loadError}</small>}
  </div>;
}

function UploadDialog({ campaignId, close, done }: { campaignId: string; close: () => void; done: () => void }) {
  const { health, healthError } = useWorkspace();
  const [file, setFile] = useState<File | null>(null);
  const [campaign, setCampaign] = useState(campaignId);
  const [hasWebcam, setHasWebcam] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const { data: initial } = useResource<Campaign>(campaignId ? "/campaigns/" + campaignId : null);
  const issue = healthError || health?.error || (health && !health.ffmpeg ? "Установите FFmpeg и FFprobe. Инструкция находится в README." : "")
    || (health && !health.whisper ? "Установите faster-whisper из backend/requirements.txt." : "")
    || (health && !health.ollama ? health.ollama_status.detail : "");
  const busy = progress !== null;

  function choose(value?: File) {
    if (!value) return;
    setError("");
    setFile(null);
    if (!/\.(mp4|mov|mkv|webm|avi|flv|m4v|ts)$/i.test(value.name)) {
      setError("Формат файла не поддерживается.");
      return;
    }
    if (health && value.size > health.max_upload_bytes) {
      setError("Файл превышает лимит " + fileSize(health.max_upload_bytes) + ".");
      return;
    }
    setFile(value);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || busy || issue) return;
    const abort = new AbortController();
    controller.current = abort;
    setError("");
    setProgress(0);
    try { await uploadVideo(file, campaign, hasWebcam, setProgress, abort.signal); done(); }
    catch (error) { setError((error as Error).message); setProgress(null); }
    finally { controller.current = null; }
  }

  return <Dialog title="Загрузить видео" close={close} lock={busy}>
    <form onSubmit={submit} className="form">
      <input ref={input} type="file" accept=".mp4,.mov,.mkv,.webm,.avi,.flv,.m4v,.ts" hidden
        onChange={(event) => { choose(event.target.files?.[0]); event.target.value = ""; }} />
      <button type="button" className={"dropzone " + (drag ? "drag" : "")} disabled={busy} onClick={() => input.current?.click()}
        onDragOver={(event) => { event.preventDefault(); if (!busy) setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => { event.preventDefault(); setDrag(false); if (!busy) choose(event.dataTransfer.files?.[0]); }}>
        <span className="empty-icon">{file ? <Video size={23}/> : <Upload size={23}/>}</span>
        <strong>{file ? file.name : "Выберите или перетащите файл"}</strong>
        <small>{file ? fileSize(file.size) : "MP4, MOV, MKV, WEBM, AVI, FLV, M4V, TS · от 15 с"}</small>
      </button>
      <fieldset disabled={busy} className="min-w-0">
        <CampaignField value={campaign} change={setCampaign} initial={initial ? [initial] : []}/>
        <WebcamToggle value={hasWebcam} change={setHasWebcam}/>
      </fieldset>
      {(error || issue) && <ErrorNotice>{error || issue}</ErrorNotice>}
      {busy && <div className="upload-progress" role="status"><span>{progress === 100 ? "Проверка видео…" : "Загрузка " + progress + "%"}</span><progress max={100} value={progress}/></div>}
      <footer className="dialog-actions">
        <button type="button" className="button secondary" onClick={busy ? () => controller.current?.abort() : close} disabled={progress === 100}>Отмена</button>
        <button type="submit" className="button primary" disabled={!file || busy || !!issue}>{busy ? <LoaderCircle className="spin" size={17}/> : <Activity size={17}/>} Загрузить и обработать</button>
      </footer>
    </form>
  </Dialog>;
}
