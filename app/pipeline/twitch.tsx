"use client";

import { Radio } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { request, timecode, useResource } from "../lib";
import { ErrorNotice } from "../ui";
import { CampaignField, useWorkspace } from "../workspace";
import { WebcamToggle } from "../webcam";

const statuses = {
  connecting: "Подключение",
  recording: "Идёт запись",
  reconnecting: "Переподключение",
  stopping: "Сохранение сегмента",
  finished: "Запись завершена",
  failed: "Запись остановлена",
};

type Recording = {
  id: string;
  channel: string;
  status: keyof typeof statuses;
  segment: number;
  active_minutes: number | null;
  next_minutes: number;
  elapsed_seconds: number;
  segment_seconds: number;
  error: string | null;
  directory?: string | null;
};

export function TwitchRecording({ archive = false }: { archive?: boolean }) {
  const { revision } = useWorkspace();
  const { data, error: statusError, reload } = useResource<{
    recording: Recording | null; archive: Recording | null; handoff_error: string | null;
  }>("/twitch", revision, 2000);
  const [url, setUrl] = useState("");
  const [campaign, setCampaign] = useState("");
  const [hasWebcam, setHasWebcam] = useState(false);
  const [directory, setDirectory] = useState("");
  const { data: archiveSettings } = useResource<{directory: string}>(archive ? "/twitch/archive-settings" : null);
  useEffect(() => { if (archiveSettings) setDirectory(archiveSettings.directory); }, [archiveSettings]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recording = archive ? data?.archive : data?.recording;
  const active = recording && recording.status !== "finished" && recording.status !== "failed";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (active) {
        await request("/twitch/" + recording.id + "/stop", { method: "POST" });
      } else {
        await request("/twitch", {
          method: "POST",
          body: JSON.stringify(archive ? { url, archive_directory: directory } :
            { url, campaign_id: campaign || null, has_webcam: hasWebcam }),
        });
      }
      reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel queue-panel">
    <div className="section-head"><h2><Radio size={17}/> {archive ? "Архив стрима · каждые 2 часа" : "Запись Twitch для нарезки"}</h2></div>
    {archive && <p className="muted">Исходный поток сохраняется на выбранный диск. Запись работает параллельно с нарезкой.</p>}
    <form className="form" onSubmit={submit}>
      {!active && <>
        <label className="field"><span>Ссылка на канал</span>
          <input type="url" value={url} onChange={(event) => setUrl(event.target.value)}
            placeholder="https://www.twitch.tv/channel" required/>
        </label>
        {archive ? <label className="field"><span>Папка на большом диске</span>
          <input required value={directory} placeholder="E:\\Streams" onChange={event => setDirectory(event.target.value)}/>
        </label> : <>
          <CampaignField value={campaign} change={setCampaign} forExport/>
          <WebcamToggle value={hasWebcam} change={setHasWebcam} disabled={busy}/>
        </>}
      </>}
      {recording && <div role="status">
        <strong>{recording.channel} · {statuses[recording.status]}</strong>
        <p className="muted">Записано {timecode(recording.elapsed_seconds)}
          {recording.segment > 0 && <span> · Сегмент {recording.segment}</span>}
          {active && recording.active_minutes && <span> · {timecode(recording.segment_seconds)} / {recording.active_minutes} мин</span>}
          {active && recording.active_minutes !== recording.next_minutes && <span> · Следующий сегмент {recording.next_minutes} мин</span>}
        </p>
        {recording.directory && <p className="muted">{recording.directory}</p>}
      </div>}
      {(error || statusError || recording?.error) && <ErrorNotice>{error || statusError || recording?.error}</ErrorNotice>}
      {!archive && data?.handoff_error && <ErrorNotice>{data.handoff_error}</ErrorNotice>}
      <div><button type="submit" className={"button " + (active ? "secondary" : "primary")}
        disabled={busy || !data || recording?.status === "stopping"}>
        {active ? "Остановить запись" : "Начать запись"}
      </button></div>
    </form>
  </section>;
}
