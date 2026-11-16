"use client";

import Link from "next/link";
import { ArrowDown, ArrowDownToLine, ArrowUp, Clapperboard, Crop, Film, LoaderCircle, Play, RefreshCw, Scissors, Type, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { API, date, request, sourceLabels, timecode, useResource, type AdSnapshot, type Clip, type EditAd, type Page } from "../lib";
import { Empty, ErrorNotice, Loading, PageTitle } from "../ui";
import { EditorAdvertising, forecast, placementError } from "../advertising";

type Options = {
  crop: "auto" | "fixed" | "fit"; center_x: number; center_y: number;
  captions: boolean; caption_language: "auto" | "ru" | "en"; caption_size: number; caption_color: string; highlight_color: string;
  caption_y: number; words_per_line: number;
  title: string; title_size: number; title_color: string; title_y: number; title_seconds: number;
  transition: "cut" | "fade" | "fadeblack"; transition_seconds: number;
  quality: "compact" | "standard" | "high";
};

type Edit = {
  id: string; segments: { clip_id: string; title: string }[]; options: Options; progress: number;
  status: keyof typeof sourceLabels; error: string | null; duration: number | null; created_at: string;
  ad: AdSnapshot | null;
  batch_id: string | null;
  batch_position: number | null;
};
type Batch = { id: string; items: Edit[] };

const defaults: Options = {
  crop: "auto", center_x: 50, center_y: 50,
  captions: true, caption_language: "auto", caption_size: 62, caption_color: "#FFFFFF", highlight_color: "#FFD633",
  caption_y: 76, words_per_line: 4,
  title: "", title_size: 76, title_color: "#FFFFFF", title_y: 8, title_seconds: 3,
  transition: "fade", transition_seconds: 0.3, quality: "high",
};

function Range({ label, value, change, min, max, step = 1, unit = "" }: {
  label: string; value: number; change: (value: number) => void; min: number; max: number; step?: number; unit?: string;
}) {
  return <label className="edit-range"><span>{label}<output>{value.toLocaleString("ru-RU")}{unit}</output></span>
    <input type="range" value={value} min={min} max={max} step={step} onChange={(event) => change(event.target.valueAsNumber)}/>
  </label>;
}

function Color({ label, value, onChange }: { label: string; value: string; onChange: (color: string) => void }) {
  return <label className="edit-color"><span>{label}</span><input type="color" value={value} onChange={(event) => onChange(event.target.value)}/></label>;
}

function EditForm({ options, update, count, duration, busy, ad, setAd, batch, setBatch, adDuration, submit }: {
  options: Options; update: <K extends keyof Options>(key: K, value: Options[K]) => void; count: number; busy: boolean;
  duration: number; ad: EditAd | null; setAd: (value: EditAd | null) => void;
  batch: boolean; setBatch: (value: boolean) => void; adDuration: number;
  submit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return <form className="editor-form" onSubmit={submit}>
    <fieldset className="panel editor-panel" disabled={busy}>
      <h2><Crop size={17}/> Кадрирование</h2>
      <label className="field"><span>Режим</span><select aria-label="Режим кадрирования" value={options.crop} onChange={(event) => update("crop", event.target.value as Options["crop"])}>
        <option value="auto">На весь экран по центру</option>
        <option value="fixed">На весь экран с ручным центром</option>
      </select></label>
      {options.crop === "fixed" && <div className="form-columns">
        <Range label="Центр по горизонтали" min={0} max={100} unit="%" value={options.center_x} change={(n) => update("center_x", n)}/>
        <Range label="Центр по вертикали" min={0} max={100} unit="%" value={options.center_y} change={(n) => update("center_y", n)}/>
      </div>}
    </fieldset>
    <fieldset className="panel editor-panel" disabled={busy}>
      <div className="edit-field-head"><h2><Type size={17}/> Субтитры</h2><label className="edit-toggle"><input type="checkbox" checked={options.captions} onChange={(event) => update("captions", event.target.checked)}/> Показывать</label></div>
      {options.captions && <>
        <label className="field"><span>Язык речи</span><select aria-label="Язык речи" value={options.caption_language} onChange={(event) => update("caption_language", event.target.value as Options["caption_language"])}>
          <option value="auto">Определять автоматически</option><option value="ru">Русский</option><option value="en">Английский</option>
        </select></label>
        <div className="form-columns">
          <Range label="Размер" min={42} max={100} value={options.caption_size} unit=" px" change={(n) => update("caption_size", n)}/>
          <Range label="Длина группы" min={2} max={6} value={options.words_per_line} change={(n) => update("words_per_line", n)}/>
          <Color label="Цвет текста" value={options.caption_color} onChange={(c) => update("caption_color", c)}/>
          <Color label="Цвет акцентной строки" value={options.highlight_color} onChange={(c) => update("highlight_color", c)}/>
        </div>
        <p className="muted">До трёх коротких строк. Акцентная строка остаётся выделенной на всю реплику.</p>
      </>}
    </fieldset>
    <fieldset className="panel editor-panel" disabled={busy}>
      <h2><Type size={17}/> Заголовок</h2>
      <label className="field"><span>Текст в начале ролика</span><input placeholder="Пустое поле скрывает заголовок" maxLength={72} value={options.title} onChange={(event) => update("title", event.target.value)}/></label>
      {options.title.trim() && <div className="form-columns">
        <Range label="Размер заголовка" min={44} max={76} unit=" px" value={options.title_size} change={(n) => update("title_size", n)}/>
        <Range label="Длительность заголовка" min={0.5} max={3} step={0.5} unit=" с" value={options.title_seconds} change={(n) => update("title_seconds", n)}/>
        <Color label="Цвет заголовка" value={options.title_color} onChange={(c) => update("title_color", c)}/>
      </div>}
    </fieldset>
    <fieldset className="panel editor-panel" disabled={busy}>
      <h2><Film size={17}/> Переходы и экспорт</h2>
      {count > 1 && <label className="field"><span>Режим экспорта</span>
        <select aria-label="Режим экспорта" value={batch ? "batch" : "single"} onChange={(event) => setBatch(event.target.value === "batch")}>
          <option value="single">Один ролик из выбранных фрагментов</option>
          <option value="batch">Пакет: отдельный MP4 для каждого фрагмента</option>
        </select>
      </label>}
      {count > 1 && !batch && <>
        <label className="field"><span>Между фрагментами</span>
          <select aria-label="Переход между фрагментами" value={options.transition} onChange={(event) => update("transition", event.target.value as Options["transition"])}>
            <option value="fade">Плавное смешивание</option><option value="fadeblack">Через чёрный</option><option value="cut">Прямая склейка</option>
          </select>
        </label>
        {options.transition !== "cut" && <Range label="Длительность перехода" min={0.1} max={1} step={0.1} unit=" с" value={options.transition_seconds} change={(n) => update("transition_seconds", n)}/>}
      </>}
      <label className="field"><span>Качество MP4</span><select aria-label="Качество MP4" value={options.quality} onChange={(event) => update("quality", event.target.value as Options["quality"])}>
        <option value="compact">Компактное</option><option value="standard">Стандартное</option><option value="high">Высокое</option>
      </select></label>
      <p className="muted">1080 × 1920 · 30 кадр/с · H.264 / AAC · исходный звук</p>
      {batch ? <p className="muted">Файлов в пакете: {count}. Настройки монтажа и рекламы применяются к каждому.</p> :
        duration > 180 ? <ErrorNotice>Ролик длится {timecode(duration, 1)}. Уберите фрагменты, чтобы уложиться в 3 минуты.</ErrorNotice> :
          <p className="muted">Длительность ролика {timecode(duration, 1)}</p>}
    </fieldset>
    <EditorAdvertising ad={ad} change={setAd} duration={adDuration} disabled={busy}/>
    <button className="button primary editor-submit" disabled={!count || busy || (!batch && duration > 180) || !!(ad && placementError(ad.placement, adDuration))}>{busy ? <LoaderCircle size={17} className="spin"/> : <Scissors size={17}/>} {batch ? "Поставить пакет в очередь" : "Собрать Shorts"}</button>
  </form>;
}

export function Editor({ ids, initialJob }: { ids: string[]; initialJob: string | null }) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(!!ids.length);
  const [selectionError, setSelectionError] = useState("");
  const [options, setOptions] = useState(defaults);
  const [ad, setAd] = useState<EditAd | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [videoError, setVideoError] = useState("");
  const [job, setJob] = useState<string | null>(initialJob);
  const [batchRequested, setBatch] = useState(false);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const submitted = useRef<{ key: string; id: string } | null>(null);
  const { data: history, error: historyError } = useResource<Page<Edit>>("/edits?offset=" + offset, revision, 4500);
  const { data: current, error: statusError } = useResource<Edit>(job ? "/edits/" + job : null, revision, 2500);
  const batch = batchRequested && clips.length > 1;
  const { data: currentBatch, error: batchError } = useResource<Batch>(current?.batch_id ? "/edits/batches/" + current.batch_id : null, revision, 2500);

  useEffect(() => {
    if (!ids.length) return;
    const controller = new AbortController();
    void Promise.allSettled(ids.map((id) => request<Clip>("/clips/" + id, { signal: controller.signal }))).then((results) => {
      if (controller.signal.aborted) return;
      const items = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      setClips(items);
      if (items[0]) setOptions((previous) => ({ ...previous, title: items[0].title.slice(0, 72) }));
      if (items.length !== ids.length) setSelectionError("Часть фрагментов недоступна. Обновите выбор в «Нарезках».");
      setLoading(false);
    });
    return () => controller.abort();
  }, [ids]);

  function update<K extends keyof Options>(key: K, value: Options[K]) {
    setOptions((previous) => ({ ...previous, [key]: value }));
  }

  function move(index: number, step: number) {
    setClips((previous) => {
      const items = [...previous];
      [items[index], items[index + step]] = [items[index + step], items[index]];
      return items;
    });
  }

  function openJob(id: string) {
    setVideoError("");
    setJob(id);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!clips.length || busy) return;
    setBusy(true);
    setError("");
    const clip_ids = clips.map((clip) => clip.id);
    const payload = { clip_ids, options, ad: ad ? { campaign_id: ad.campaign.id, placement: ad.placement, expected_views: ad.expected_views } : null };
    const key = JSON.stringify({ ...payload, batch });
    if (submitted.current?.key !== key) submitted.current = { key, id: crypto.randomUUID() };
    try {
      const body = JSON.stringify({ id: submitted.current.id, ...payload });
      const result = batch ? (await request<Batch>("/edits/batches", { method: "POST", body })).items[0] :
        await request<Edit>("/edits", { method: "POST", body });
      submitted.current = null;
      openJob(result.id);
      setOffset(0);
      setRevision((n) => n + 1);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  async function retry(id: string) {
    setBusy(true);
    setError("");
    try {
      await request("/edits/" + id + "/retry", { method: "POST" });
      openJob(id);
      setRevision((n) => n + 1);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const selection = clips.length ? clips.map((clip) => clip.id) : loading ? ids : [];
  const frames = clips.map((clip) => Math.floor((clip.end_seconds - clip.start_seconds) * 30 + 1e-6));
  const overlap = clips.length > 1 && options.transition !== "cut" ? Math.min(Math.round(options.transition_seconds * 30), Math.floor(Math.min(...frames) / 4)) : 0;
  const duration = (frames.reduce((total, next) => total + next, 0) - overlap * Math.max(0, clips.length - 1)) / 30;
  const adDuration = batch ? Math.min(...frames) / 30 : duration;
  const ready = currentBatch?.items.filter((item) => item.status === "completed").length ?? 0;
  return <>
    <PageTitle title="Auto Editor" description="Соберите вертикальный Shorts из готовых нарезок." action={<Link className="button secondary" href={"/clips" + (selection.length ? "?selected=" + selection.join(",") : "")}><Clapperboard size={17}/> {selection.length ? "Изменить выбор" : "Выбрать фрагменты"}</Link>}/>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    <div className="editor-grid">
      <div className="editor-form">
        <section className="panel editor-panel">
          <h2><Clapperboard size={17}/> Порядок фрагментов {clips.length > 0 && <span className="muted">{clips.length}</span>}</h2>
          {selectionError && <ErrorNotice>{selectionError}</ErrorNotice>}
          {loading ? <Loading/> : clips.length ? <ol className="edit-timeline">
            {clips.map((clip, index) => <li key={clip.id}>
              <span className="edit-number">{index + 1}</span>
              <img src={API + "/clips/" + clip.id + "/poster"} loading="lazy" alt=""/>
              <div className="edit-clip-name"><strong title={clip.title}>{clip.title}</strong><small>{timecode(clip.start_seconds, 1)}–{timecode(clip.end_seconds, 1)}</small></div>
              <div className="edit-order">
                <button className="icon-button" type="button" disabled={!index || busy} onClick={() => move(index, -1)} aria-label="Поднять фрагмент"><ArrowUp size={14}/></button>
                <button className="icon-button" type="button" disabled={index === clips.length - 1 || busy} onClick={() => move(index, 1)} aria-label="Опустить фрагмент"><ArrowDown size={14}/></button>
                <button className="icon-button" type="button" disabled={busy} onClick={() => setClips((prev) => prev.filter((item) => item.id !== clip.id))} aria-label="Убрать из монтажа"><X size={14}/></button>
              </div>
            </li>)}
          </ol> : <Empty icon={Clapperboard} title="Выберите от 1 до 10 фрагментов" description="В разделе «Нарезки» нажмите «В монтаж» на нужных клипах."/>}
        </section>
        {clips.length > 0 && <EditForm options={options} update={update} count={clips.length} duration={duration} busy={busy} ad={ad} setAd={setAd} batch={batch} setBatch={setBatch} adDuration={adDuration} submit={submit}/>}
      </div>
      <div className="editor-form editor-review">
        {batchError && <ErrorNotice>{batchError}</ErrorNotice>}
        {currentBatch && <section className="panel editor-panel">
          <div className="edit-field-head"><h2><Film size={17}/> Пакет · {ready}/{currentBatch.items.length}</h2>
            {ready > 0 && <a className="button secondary button-small" href={API + "/edits/batches/" + currentBatch.id + "/download"}><ArrowDownToLine size={15}/> Скачать ZIP</a>}
          </div>
          <ul className="edit-history">{currentBatch.items.map((item) => <li key={item.id}>
            <button type="button" aria-pressed={item.id === job} onClick={() => openJob(item.id)}>
              <div><strong>{item.batch_position}. {item.segments[0]?.title ?? "Shorts"}</strong></div>
              <span className={"badge " + item.status}>{sourceLabels[item.status]}{item.status === "processing" ? " · " + item.progress + "%" : ""}</span>
            </button>
          </li>)}</ul>
        </section>}
        <section className="panel editor-panel editor-preview">
          <div className="edit-field-head"><h2><Play size={17}/> Предпросмотр</h2><span className="badge">9:16</span></div>
          {statusError && <ErrorNotice>{statusError}</ErrorNotice>}
          {current?.status === "completed" ? <>
            <video key={current.id} controls playsInline preload="metadata" className="short-player"
              src={API + "/edits/" + current.id + "/play"} onError={() => setVideoError("Не удалось открыть MP4. Проверьте, что файл доступен на диске.")}/>
            {videoError && <ErrorNotice>{videoError}</ErrorNotice>}
            {current.ad ? <div className="ad-forecast"><span>{current.ad.brand} · {current.ad.name}</span><strong title="Прогноз по ожидаемым просмотрам">{forecast(current.ad.cpm, current.ad.expected_views, current.ad.currency)}</strong></div> :
              <p className="muted">Без рекламы</p>}
            <div className="edit-field-head"><span className="muted">{current.duration !== null ? timecode(current.duration, 1) : ""}</span>
              <a className="button primary" href={API + "/edits/" + current.id + "/download"}><ArrowDownToLine size={17}/> Скачать MP4</a>
            </div>
          </> : <div className="short-placeholder">
            {current?.status === "processing" || current?.status === "queued" ? <div className="short-wait" role="status">
              <LoaderCircle size={25} className="spin"/><strong>{current.status === "queued" ? "В очереди" : current.progress < 84 ? "Монтаж фрагментов" : "Сборка MP4"}</strong>
              <progress max={100} value={current.progress} aria-label="Прогресс монтажа"/><small>{current.progress}%</small>
            </div> : current?.status === "failed" ? <div className="short-wait">
              <ErrorNotice>{current.error ?? "Экспорт завершился ошибкой."}</ErrorNotice>
              <button type="button" className="button secondary button-small" onClick={() => retry(current.id)} disabled={busy}><RefreshCw size={15}/> Повторить экспорт</button>
            </div> : job && !statusError ? <Loading/> : <div className="short-wait"><Film size={28}/><span>Готовый Shorts появится здесь</span></div>}
          </div>}
        </section>
        <section className="panel editor-panel">
          <h2><Film size={17}/> История экспорта</h2>
          {historyError && <ErrorNotice>{historyError}</ErrorNotice>}
          {history?.items.length ? <ul className="edit-history">{history.items.map((item) => <li key={item.id}>
            <button type="button" aria-pressed={item.id === job} onClick={() => openJob(item.id)}>
              <div><strong>{item.options.title || item.segments[0]?.title || "Shorts"}</strong><small>{date(item.created_at)} · {item.ad?.brand ?? "Без рекламы"}</small></div>
              <span className={"badge " + item.status}>{sourceLabels[item.status]}</span>
            </button>
          </li>)}</ul> : history ? <p className="muted">Экспортов пока нет.</p> : !historyError && <Loading/>}
          {!!history && (!!offset || history.next !== null) && <div className="edit-field-head">
            <button className="button secondary button-small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 16))}>Назад</button>
            <button className="button secondary button-small" disabled={history.next === null} onClick={() => history.next !== null && setOffset(history.next)}>Далее</button>
          </div>}
        </section>
      </div>
    </div>
  </>;
}
