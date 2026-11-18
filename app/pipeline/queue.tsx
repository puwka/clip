"use client";

import Link from "next/link";
import { ArrowDownToLine, ArrowRight, Clapperboard, Film, Link2, ListTodo, RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { API, date, request, sourceLabels, useResource, type ImportJob, type Page, type QueueJob, type Source } from "../lib";
import { ImportButton } from "../import-link";
import { Empty, ErrorNotice, Loading, PageTitle } from "../ui";
import { UploadButton, useWorkspace } from "../workspace";
import { TwitchRecording } from "./twitch";

type EditJob = QueueJob & { options: { title: string }; segments: { title: string }[]; batch_id: string | null };

function JobRow({ job, title, detail, actions, children }: {
  job: QueueJob; title: string; detail: string; actions?: ReactNode; children?: ReactNode;
}) {
  return <li className="queue-row">
    <div className="queue-title"><strong title={title}>{title}</strong><small>{detail} · {date(job.created_at)}</small>
      {job.error && <p className="row-error" role="status">{job.error}</p>}</div>
    <div className="queue-state">
      <span className={"badge " + job.status}><span className="status-dot"/>{sourceLabels[job.status]}</span>
      {job.status === "processing" && <><div className="task-progress"><progress aria-label="Прогресс задачи" max={100} value={job.progress}/><span>{job.progress}%</span></div>
        {children}</>}
    </div>
    {actions && <div className="source-actions queue-actions">{actions}</div>}
  </li>;
}

function QueuePanel<T>({ title, path, pending, size, icon: Icon, render, empty }: {
  title: string; path: string; pending: boolean; size: number; icon: typeof ListTodo;
  render: (job: T) => ReactNode; empty: string;
}) {
  const { revision } = useWorkspace();
  const [offset, setOffset] = useState(0);
  const { data, error, reload } = useResource<Page<T>>(path + "?pending=" + pending + "&offset=" + offset, revision, 2800);
  return <section className="panel queue-panel">
    <div className="section-head"><h2><Icon size={17}/> {title}</h2>
      <button type="button" aria-label={"Обновить: " + title} className="icon-button" onClick={reload}><RefreshCw size={15}/></button></div>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {data?.items.length ? <ul className="queue-list">{data.items.map(render)}</ul> : data ?
      <Empty icon={Icon} title={empty} description={pending ? "Активных задач на этом этапе нет." : "Новые задачи будут отображаться здесь."}/> : !error && <Loading/>}
    {data && (offset > 0 || data.next !== null) && <div className="pagination">
      <button type="button" className="button secondary button-small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - size))}>Назад</button>
      <button type="button" className="button secondary button-small" disabled={data.next === null} onClick={() => data.next !== null && setOffset(data.next)}>Далее</button>
    </div>}
  </section>;
}

export function Pipeline() {
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const { refresh } = useWorkspace();

  async function retry(path: string) {
    setBusy(path);
    setError("");
    try {
      await request(path + "/retry", { method: "POST" });
      refresh();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(""); }
  }

  const retryButton = (path: string) => <button className="button secondary button-small" type="button" disabled={!!busy}
    onClick={() => retry(path)}><RefreshCw size={14} className={busy === path ? "spin" : ""}/> Повторить</button>;

  return <>
    <PageTitle title="Конвейер" description="От загрузки и ИИ-анализа до монтажа, рекламы и скачивания." action={
      <div className="queue-actions"><ImportButton/><UploadButton/></div>}/>
    <TwitchRecording/>
    <TwitchRecording archive/>
    <div className="queue-toolbar"><div className="segmented" aria-label="Фильтр очереди">
      <button type="button" aria-pressed={!pending} onClick={() => setPending(false)}>Все задачи</button>
      <button type="button" aria-pressed={pending} onClick={() => setPending(true)}>В работе</button>
    </div></div>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    <div className="queue-panels">
      <QueuePanel<Source> key={"sources-" + pending} title="ИИ-анализ" path="/sources" pending={pending} size={24} icon={Clapperboard} empty="Нет записей"
        render={(job) => <JobRow key={job.id} job={job} title={job.name} detail="Разбор записи" actions={job.status === "failed" ? retryButton("/sources/" + job.id) :
          job.status === "completed" ? <Link className="button primary button-small" href={"/clips?source=" + job.id}>Выбрать моменты <ArrowRight size={14}/></Link> : undefined}>
          <small className="muted">{job.progress < 59 ? "Речь и видео" : job.progress < 86 ? "Оценка Qwen" : "Нарезка MP4"}</small>
        </JobRow>}/>
      <QueuePanel<EditJob> key={"edits-" + pending} title="Монтаж и экспорт" path="/edits" pending={pending} size={16} icon={Film} empty="Нет экспортов"
        render={(job) => <JobRow key={job.id} job={job} title={job.options.title || job.segments[0]?.title || "Shorts"} detail={job.batch_id ? "Пакетный экспорт" : "Вертикальный ролик"}
          actions={<>
            <Link className="button secondary button-small" href={"/editor?job=" + job.id}>Открыть <ArrowRight size={14}/></Link>
            {job.status === "completed" && <a aria-label="Скачать MP4" className="icon-button" href={API + "/edits/" + job.id + "/download"}><ArrowDownToLine size={15}/></a>}
            {job.status === "failed" && retryButton("/edits/" + job.id)}
          </>}>
          <small className="muted">{job.progress < 84 ? "Монтаж" : "Сборка MP4"}</small>
        </JobRow>}/>
      <QueuePanel<ImportJob> key={"imports-" + pending} title="Скачивание по ссылке" path="/imports" pending={pending} size={24} icon={Link2} empty="Нет импортов"
        render={(job) => <JobRow key={job.id} job={job} title={job.name || job.host} detail={job.host} actions={job.status === "failed" ? retryButton("/imports/" + job.id) : undefined}>
          <small className="muted">{job.progress < 94 ? "Скачивание" : "Проверка файла"}</small>
        </JobRow>}/>
    </div>
  </>;
}
