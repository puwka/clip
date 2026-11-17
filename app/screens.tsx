"use client";

import Link from "next/link";
import {
  Activity, ArrowDownToLine, ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Clapperboard,
  Film, FolderOpen, HardDrive, Link2, Megaphone, Play, Plus, Radio, RefreshCw, Scissors,
  Settings2, Upload, Video,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  API, clipCategories, date, fileSize, formatCount, request, sourceLabels, timecode, useResource,
  type Campaign, type Clip, type Page, type Settings, type Source, type Summary,
} from "./lib";
import { Dialog, Empty, ErrorNotice, Loading, PageTitle } from "./ui";
import { CampaignField, UploadButton, useWorkspace } from "./workspace";
import { ImportButton } from "./import-link";
import { WebcamDialog } from "./webcam";
import { OllamaSettings } from "./ollama-settings";
import { availabilityLabels, campaignPeriod, CampaignDialog, forecast, money } from "./advertising";

const metrics = [
  { key: "sources", label: "Источники", icon: FolderOpen, color: "orange" },
  { key: "clips", label: "Готовые нарезки", icon: Clapperboard, color: "violet" },
  { key: "processing", label: "В очереди и обработке", icon: Activity, color: "blue" },
  { key: "campaigns", label: "Активные кампании", icon: Megaphone, color: "green" },
] as const;
const steps = [
  { icon: Upload, title: "Загрузка записи", text: "Сохранение видео на этом ПК" },
  { icon: Activity, title: "Анализ записи", text: "Речь, движение и резкие звуки" },
  { icon: Scissors, title: "Оценка Qwen и экспорт", text: "До 10 клипов в MP4" },
];
const waveform = Array.from({ length: 88 }, (_, n) => {
  const peak = Math.max(0, 1 - Math.abs(n - 53) / 20);
  const height = (Math.abs(Math.sin(n * 1.72) * Math.cos(n * .22)) * 20 + Math.abs(Math.sin(n * 2.87)) * 10) * (0.55 + peak * 1.8);
  return 5 + Math.round(height);
});

function EditGraphic() {
  return <div className="edit-art" aria-hidden="true">
    <div className="edit-grid"/>
    <div className="edit-orbit"/>
    <div className="edit-window">
      <div className="edit-top"><div className="edit-dots"><i/><i/><i/></div><span>00:00:18:24</span><Activity size={14}/></div>
      <div className="edit-tracks">
        <div className="clip-region"><span/><span/></div>
        <div className="edit-needle"/>
        <div className="waveform">{waveform.map((height, index) => <i key={index} style={{ height }} className={index >= 34 && index <= 67 ? "wave-active" : ""}/>)}</div>
        <div className="edit-segments"><i/><i/><i/><i/><i/><i/></div>
      </div>
      <div className="edit-ruler"><span>00:00</span><span>00:10</span><span>00:20</span><span>00:30</span></div>
    </div>
    <span className="edit-float"><Scissors size={23} strokeWidth={1.9}/></span>
  </div>;
}

export function Dashboard() {
  const { revision, health, healthError } = useWorkspace();
  const { data, error } = useResource<Summary>("/summary", revision, 4000);
  const connectionIssue = healthError || health?.error || (health && !health.ffmpeg ? "Для обработки требуется FFmpeg." : "")
    || (health && !health.ollama ? health.ollama_status.detail : "")
    || (health && !health.whisper ? "Проверьте faster-whisper." : "");
  return <>
    <PageTitle title="Дашборд" description="Загрузка записей, очередь обработки и готовые клипы."
      action={<Link className="button secondary" href="/pipeline">Открыть конвейер <ArrowRight size={15}/></Link>}/>
    <section className="hero">
      <div className="hero-content">
        <span className="hero-tag"><Radio size={15}/> Автоматические нарезки</span>
        <h2>Из стрима<br/>в <em>нарезку.</em></h2>
        <p>Загрузите запись игрового стрима. Clip Factory найдёт активные моменты и соберёт клипы.</p>
        <UploadButton/>
      </div>
      <EditGraphic/>
    </section>
    <section className="metrics-grid" aria-label="Сводка">
      {metrics.map(({ key, label, icon: Icon, color }) => <div className="metric panel" key={key}>
        <span className={"metric-icon " + color}><Icon size={19} strokeWidth={1.8}/></span>
        <div><strong>{data ? formatCount(data.counts[key]) : "—"}</strong><span>{label}</span></div>
      </div>)}
    </section>
    <div className="dashboard-grid">
      <section className="panel recent">
        <div className="section-head"><h2>Последние источники</h2><Link className="text-link" href="/sources">Все источники <ArrowRight size={16}/></Link></div>
        {data?.sources.length ? <SourceTable items={data.sources} compact/> :
          <Empty icon={FolderOpen} title={error ? "Очередь недоступна" : data ? "Пока нет источников" : "Загрузка очереди"} description={error ? "Статусы появятся после подключения к Supabase." : data ? "Загрузите запись, чтобы запустить обработку." : "Запрашиваем последние записи."}/>}
      </section>
      <section className="panel pipeline">
        <div className="section-head"><h2>Этапы обработки</h2></div>
        <ol>{steps.map(({ icon: Icon, title, text }) => <li key={title}><span className="step-icon"><Icon size={17}/></span><div><strong>{title}</strong><p>{text}</p></div></li>)}</ol>
      </section>
    </div>
    {connectionIssue && <div className="connection-note"><span><Settings2 size={16}/>{connectionIssue}</span><Link href="/settings" className="text-link">Настройки <ArrowRight size={15}/></Link></div>}
    {!!data?.counts.failed && <ErrorNotice>Задач с ошибками: {data.counts.failed}. Повторный запуск доступен на странице «Источники».</ErrorNotice>}
    {!!data?.clips.length && <section className="latest-clips"><div className="section-head"><h2>Последние нарезки</h2><Link className="text-link" href="/clips">Все нарезки <ArrowRight size={16}/></Link></div><ClipGrid items={data.clips}/></section>}
  </>;
}

function SourceTable({ items, compact }: { items: Source[]; compact?: boolean }) {
  const { refresh } = useWorkspace();
  const [assignment, setAssignment] = useState<Source | null>(null);
  const [webcam, setWebcam] = useState<Source | null>(null);
  const [retrying, setRetrying] = useState("");
  const [error, setError] = useState("");
  async function runAgain(id: string, action: "retry" | "reanalyze") {
    setRetrying(id);
    setError("");
    try { await request("/sources/" + id + "/" + action, { method: "POST" }); refresh(); }
    catch (error) { setError((error as Error).message); }
    finally { setRetrying(""); }
  }
  return <>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    <div className="table-scroll">
      <table>
        <thead><tr><th>Источник</th><th>Статус</th>{!compact && <><th>Кампания</th><th>Добавлен</th><th><span className="sr-only">Действия</span></th></>}</tr></thead>
        <tbody>{items.map((item) => <tr key={item.id}>
          <td><div className="file-title"><span className="file-icon"><Film size={17}/></span><div><strong title={item.name}>{item.name}</strong><small>{timecode(item.duration)} · {fileSize(item.byte_size)}</small></div></div></td>
          <td><span className={"badge " + item.status}><span className="status-dot"/>{sourceLabels[item.status]}</span>
            {item.status === "processing" && <><div className="task-progress"><progress max={100} value={item.progress} aria-label="Обработка"/><span>{item.progress}%</span></div>
              {!compact && <small className="muted">{item.progress < 59 ? "Анализ речи и видео" : item.progress < 86 ? "Оценка Qwen" : "Экспорт клипов"}</small>}</>}
            {item.error && !compact && <p className="row-error">{item.error}</p>}
          </td>
          {!compact && <>
            <td><button type="button" className="text-link campaign-link" onClick={() => setAssignment(item)}><Link2 size={15}/>{item.cf_campaigns?.name ?? "Назначить"}</button></td>
            <td className="muted whitespace-nowrap">{date(item.created_at)}</td>
            <td><div className="source-actions">
              <button className="button secondary button-small" type="button" onClick={() => setWebcam(item)}>
                <Video size={14}/> Веб-камера{item.has_webcam ? " · Да" : ""}
              </button>
              {item.status === "completed" && <Link className="button secondary button-small" href={"/clips?source=" + item.id}>Нарезки <ArrowRight size={14}/></Link>}
              {item.status === "failed" && <button className="button secondary button-small" type="button" onClick={() => runAgain(item.id, "retry")} disabled={!!retrying}><RefreshCw size={14} className={retrying === item.id ? "spin" : ""}/> Повторить</button>}
              {item.status === "completed" && item.duration >= 15 && <button className="icon-button" aria-label="Анализировать заново" title="Анализировать заново" type="button" onClick={() => runAgain(item.id, "reanalyze")} disabled={!!retrying}><RefreshCw size={14} className={retrying === item.id ? "spin" : ""}/></button>}
            </div></td>
          </>}
        </tr>)}</tbody>
      </table>
    </div>
    {assignment && <AssignmentDialog source={assignment} close={() => setAssignment(null)} done={() => { setAssignment(null); refresh(); }}/>}
    {webcam && <WebcamDialog source={webcam} close={() => setWebcam(null)} done={() => { setWebcam(null); refresh(); }}/>}
  </>;
}

function AssignmentDialog({ source, close, done }: { source: Source; close: () => void; done: () => void }) {
  const [campaign, setCampaign] = useState(source.campaign_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const initial = source.cf_campaigns && source.campaign_id ? [{ id: source.campaign_id, name: source.cf_campaigns.name }] : [];
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/sources/" + source.id + "/campaign", { method: "PATCH", body: JSON.stringify({ campaign_id: campaign || null }) });
      done();
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <Dialog title="Кампания источника" close={close} lock={busy}>
    <form onSubmit={save} className="form">
      <p className="muted break-all">{source.name}</p>
      <fieldset disabled={busy}><CampaignField value={campaign} change={setCampaign} initial={initial}/></fieldset>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <footer className="dialog-actions"><button className="button primary" disabled={busy}>Сохранить</button></footer>
    </form>
  </Dialog>;
}

function Pagination({ offset, next, change }: { offset: number; next: number | null; change: (value: number) => void }) {
  if (!offset && next === null) return null;
  return <div className="pagination">
    <button type="button" className="button secondary button-small" disabled={!offset} onClick={() => change(Math.max(0, offset - 24))}><ChevronLeft size={16}/> Назад</button>
    <span>Страница {Math.floor(offset / 24) + 1}</span>
    <button type="button" className="button secondary button-small" disabled={next === null} onClick={() => next !== null && change(next)}>Далее <ChevronRight size={16}/></button>
  </div>;
}

function useListing<T>(table: string, campaign?: string, interval?: number, source?: string) {
  const { revision } = useWorkspace();
  const [page, setPage] = useState({ campaign, source, offset: 0 });
  const offset = page.campaign === campaign && page.source === source ? page.offset : 0;
  const params = new URLSearchParams({ offset: String(offset) });
  if (campaign) params.set("campaign", campaign);
  if (source) params.set("source", source);
  const result = useResource<Page<T>>("/" + table + "?" + params, revision, interval);
  const change = (next: number) => setPage({ campaign, source, offset: next });
  return { ...result, offset, change };
}

function CampaignFilter({ id, path }: { id: string; path: string }) {
  const { data } = useResource<Campaign>("/campaigns/" + encodeURIComponent(id));
  return <div className="filter-bar"><Link className="text-link" href={path}><ArrowLeft size={16}/> Сбросить фильтр</Link><span>Кампания: {data?.name ?? "выбранная кампания"}</span></div>;
}

function Unavailable({ error }: { error: string }) {
  return <Empty icon={Radio} title="Данные недоступны" description={error}
    action={<Link className="button secondary" href="/settings"><Settings2 size={16}/> Проверить подключения</Link>}/>;
}

export function Sources({ campaign }: { campaign?: string }) {
  const { data, error, offset, change } = useListing<Source>("sources", campaign, 3000);
  return <>
    <PageTitle title="Источники" description="Записи стримов и ход обработки." action={
      <div className="queue-actions"><ImportButton campaign={campaign} goToQueue/><UploadButton campaign={campaign}/></div>}/>
    {campaign && <CampaignFilter id={campaign} path="/sources"/>}
    {data && error && <ErrorNotice>{error}</ErrorNotice>}
    <section className="panel list-panel" aria-label="Записи стримов">
      {data ? data.items.length ? <SourceTable items={data.items}/> :
        <Empty icon={FolderOpen} title="Нет записей" description="Загрузите запись стрима, чтобы поставить её в очередь."/> : error ? <Unavailable error={error}/> : <Loading/>}
    </section>
    {data && <Pagination offset={offset} next={data.next} change={change}/>}
  </>;
}

function ClipGrid({ items, ranked, selected, toggle }: { items: Clip[]; ranked?: boolean; selected?: string[]; toggle?: (id: string) => void }) {
  const [playing, setPlaying] = useState<Clip | null>(null);
  const [videoError, setVideoError] = useState(false);
  return <>
    <div className="clips-grid">{items.map((clip, index) => {
      const path = API + "/clips/" + clip.id;
      return <article className="panel clip-card" key={clip.id}>
        <button className="clip-cover" type="button" onClick={() => { setVideoError(false); setPlaying(clip); }} aria-label={"Смотреть: " + clip.title}>
          {/* The local API serves a generated poster for every completed clip. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={path + "/poster"} alt="" loading="lazy" />
          <span className="play-icon"><Play size={20} fill="currentColor"/></span>
          {ranked && <span className="clip-rank">#{index + 1}</span>}
          <span className="duration">{timecode(clip.end_seconds - clip.start_seconds)}</span>
        </button>
        <div className="clip-content"><h3 title={clip.title}>{clip.title}</h3>
          <div className="clip-meta"><span title="Таймкод в исходной записи">{timecode(clip.start_seconds, 1)}–{timecode(clip.end_seconds, 1)}</span><span>{clip.lossless || clip.cf_sources.aspect === "landscape" ? "16:9" : "9:16"}</span>
            {clip.category && <span className="clip-score" title="Оценка Qwen">{clip.score_percent}/100</span>}</div>
          {clip.category && <span className="badge clip-category">{clipCategories[clip.category]}</span>}
          {clip.reason && <p className="clip-reason">{clip.reason}</p>}
          {clip.cf_sources.cf_campaigns && <small className="muted">{clip.cf_sources.cf_campaigns.name}</small>}
          <div className="clip-actions"><a className="button secondary button-small" href={path + "/download"}><ArrowDownToLine size={16}/> {clip.lossless ? "Скачать без потерь (MKV)" : "Скачать MP4"}</a>
            {toggle && <button className="button secondary button-small select-clip" type="button" aria-pressed={selected?.includes(clip.id) ?? false}
              disabled={selected?.length === 10 && !selected.includes(clip.id)} onClick={() => toggle(clip.id)}>
              {selected?.includes(clip.id) ? <Check size={15}/> : <Plus size={15}/>} {selected?.includes(clip.id) ? "Выбран" : "В монтаж"}
            </button>}
          </div>
        </div>
      </article>;
    })}</div>
    {playing && <Dialog title={playing.title} close={() => setPlaying(null)} wide>
      <video className="video-player" controls autoPlay playsInline src={API + "/clips/" + playing.id + "/play"} onError={() => setVideoError(true)}/>
      {playing.category && <div className="clip-details"><span>{timecode(playing.start_seconds, 1)}–{timecode(playing.end_seconds, 1)}</span><span>{clipCategories[playing.category]}</span><strong>{playing.score_percent}/100</strong></div>}
      {playing.reason && <p className="clip-reason">{playing.reason}</p>}
      {videoError && <ErrorNotice>Не удалось открыть видео. Проверьте, что файл клипа доступен на диске.</ErrorNotice>}
    </Dialog>}
  </>;
}

export function Clips({ campaign, source, initialSelection }: { campaign?: string; source?: string; initialSelection: string[] }) {
  const { data, error, offset, change } = useListing<Clip>("clips", campaign, 5000, source);
  const [selected, setSelected] = useState<string[]>([...new Set(initialSelection)]);
  function toggle(id: string) {
    setSelected((items) => items.includes(id) ? items.filter((item) => item !== id) : items.length < 10 ? [...items, id] : items);
  }
  return <>
    <PageTitle title={source ? "Лучшие моменты записи" : "Нарезки"} description={data?.items[0] && source ? data.items[0].cf_sources.name : "Оценки, предпросмотр и скачивание готовых клипов."}
      action={selected.length > 0 ? <Link className="button primary" href={"/editor?clips=" + selected.join(",")}><Scissors size={17}/> В Auto Editor ({selected.length})</Link> : undefined}/>
    {source && <div className="filter-bar"><Link className="text-link" href="/sources"><ArrowLeft size={16}/> К источникам</Link></div>}
    {campaign && <CampaignFilter id={campaign} path="/clips"/>}
    {data && error && <ErrorNotice>{error}</ErrorNotice>}
    {data?.items.length ? <ClipGrid items={data.items} ranked={!!source} selected={selected} toggle={toggle}/> : <section className="panel list-panel">
      {data ? <Empty icon={Clapperboard} title="Нет готовых клипов" description="Клипы появятся здесь после обработки видео." action={<Link href="/sources" className="button secondary">К источникам <ArrowRight size={16}/></Link>}/> :
        error ? <Unavailable error={error}/> : <Loading/>}
    </section>}
    {data && <Pagination offset={offset} next={data.next} change={change}/>}
  </>;
}

export function Campaigns() {
  const { data, error, offset, change } = useListing<Campaign>("campaigns");
  const { refresh } = useWorkspace();
  const [editing, setEditing] = useState<{ value?: Campaign } | null>(null);
  const create = <button type="button" className="button primary" onClick={() => setEditing({})}><Plus size={18}/> Создать кампанию</button>;
  return <>
    <PageTitle title="Рекламные кампании" description="Управляйте баннерами, сроками и прогнозом дохода по рекламодателям." action={create}/>
    {data && error && <ErrorNotice>{error}</ErrorNotice>}
    {data?.items.length ? <div className="campaign-grid">{data.items.map((item) => <article className="panel campaign-card" key={item.id}>
      <div className="campaign-top"><span className="metric-icon green"><Megaphone size={19}/></span><span className={"badge " + (item.availability === "available" ? "active" : item.availability === "expired" ? "finished" : item.availability)}><span className="status-dot"/>{availabilityLabels[item.availability]}</span></div>
      <h2>{item.name}</h2><p>{item.brand}</p>
      {item.banner_id && <div className="campaign-banner"><img src={API + "/campaign-banners/" + item.banner_id} alt="Баннер кампании" loading="lazy"/></div>}
      <p>{campaignPeriod(item.starts_on, item.ends_on)}</p>
      {item.promo_code && <p>Промокод <code className="campaign-code">{item.promo_code}</code></p>}
      {item.description && <p className="campaign-description">{item.description}</p>}
      <div className="campaign-revenue">
        <span>CPM {money(item.cpm, item.currency)} · {formatCount(item.expected_views)} просмотров</span>
        <div className="ad-forecast"><span>Прогноз</span><strong>{forecast(item.cpm, item.expected_views, item.currency)}</strong></div>
      </div>
      <div className="campaign-actions">
        <Link className="button secondary button-small" href={"/sources?campaign=" + item.id}>Источники</Link>
        <Link className="button secondary button-small" href={"/clips?campaign=" + item.id}>Нарезки</Link>
        <button type="button" className="text-link" onClick={() => setEditing({ value: item })}>Изменить</button>
      </div>
    </article>)}</div> : <section className="panel list-panel">
      {data ? <Empty icon={Megaphone} title="Пока нет кампаний" description="Добавьте рекламодателя и баннер, чтобы выбирать кампанию в Auto Editor."/> :
        error ? <Unavailable error={error}/> : <Loading/>}
    </section>}
    {data && <Pagination offset={offset} next={data.next} change={change}/>}
    {editing && <CampaignDialog campaign={editing.value} close={() => setEditing(null)} done={() => { setEditing(null); refresh(); }}/>}
  </>;
}

export function SettingsPage() {
  const { health, healthError, revision, refresh } = useWorkspace();
  const { data, error } = useResource<Settings>("/settings", revision);
  return <>
    <PageTitle title="Настройки" description="Параметры нарезки и состояние локальной системы."/>
    <div className="settings-grid">
      <section className="panel settings-panel">
        <h2>Новые нарезки</h2><p className="muted">До 10 лучших фрагментов на запись. Параметры применяются при загрузке.</p>
        {error && <ErrorNotice>{error}</ErrorNotice>}
        {data ? <SettingsForm initial={data}/> : !error && <Loading/>}
      </section>
      <section className="panel settings-panel">
        <div className="flex items-center justify-between gap-3"><h2>Подключения</h2><button className="icon-button" type="button" onClick={refresh} title="Проверить подключения" aria-label="Проверить подключения"><RefreshCw size={16}/></button></div>
        <ul className="checks">
          {[
            { name: "FastAPI", ok: !!health && !healthError, icon: Radio },
            { name: "Supabase", ok: health?.database && !healthError, icon: HardDrive },
            { name: "FFmpeg и FFprobe", ok: health?.ffmpeg && !healthError, icon: Video },
            { name: "faster-whisper" + (health ? " · " + health.whisper_model : ""), ok: health?.whisper && !healthError, icon: Activity },
            { name: "Ollama" + (health ? " · " + health.ollama_model : ""), ok: health?.ollama && !healthError, icon: Activity },
            { name: "Обработчик очереди", ok: health?.worker && health.database && !healthError, icon: Activity },
          ].map(({ name, ok, icon: Icon }) => <li key={name}><span><Icon size={17}/>{name}</span><span className={"status-dot " + (ok ? "good" : "")} title={ok ? "Подключено" : "Недоступно"} aria-label={ok ? "Подключено" : "Недоступно"}/></li>)}
        </ul>
        {(healthError || health?.error) && <ErrorNotice>{healthError || health?.error}</ErrorNotice>}
        {health && !health.ffmpeg && <ErrorNotice>Установите FFmpeg и FFprobe, затем перезапустите FastAPI.</ErrorNotice>}
        {health && !health.ollama && <ErrorNotice>{health.ollama_status.detail}</ErrorNotice>}
        {health && !health.whisper && <ErrorNotice>Установите faster-whisper из backend/requirements.txt.</ErrorNotice>}
        {!!health && <div className="connection-limit"><span>Лимит загрузки</span><strong>{fileSize(health.max_upload_bytes)}</strong></div>}
      </section>
      <OllamaSettings health={health} refresh={refresh}/>
    </div>
  </>;
}

function SettingsForm({ initial }: { initial: Settings }) {
  const [seconds, setSeconds] = useState(String(initial.clip_seconds));
  const [twitchMinutes, setTwitchMinutes] = useState(String(initial.twitch_segment_minutes ?? 60));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await request("/settings", { method: "PATCH", body: JSON.stringify({
        clip_seconds: Number(seconds), aspect: "portrait",
        twitch_segment_minutes: initial.twitch_segment_minutes === undefined ? undefined : Number(twitchMinutes),
      }) });
      setSaved(true);
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <form className="form settings-form" onSubmit={save} onChange={() => setSaved(false)}>
    <fieldset className="form" disabled={busy}>
      <label className="field"><span>Длительность клипа, сек.</span><input type="number" min={15} max={45} required value={seconds} onChange={(event) => setSeconds(event.target.value)}/></label>
      {initial.twitch_segment_minutes !== undefined && <label className="field">
        <span>Длительность сегмента Twitch</span>
        <select value={twitchMinutes} onChange={(event) => setTwitchMinutes(event.target.value)}>
          <option value="15">Быстрый · 15 минут</option>
          <option value="60">Стандартный · 60 минут</option>
        </select>
        <small className="muted">При записи изменение вступит в силу со следующего сегмента.</small>
      </label>}
      <p className="muted">Формат экспорта · 9:16, 1080 × 1920</p>
    </fieldset>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    <div className="flex items-center gap-3"><button className="button primary" disabled={busy}>Сохранить</button>{saved && <span className="success" role="status"><Check size={17}/> Сохранено</span>}</div>
  </form>;
}
