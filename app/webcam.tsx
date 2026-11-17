"use client";

import { useRef, useState, type FormEvent, type PointerEvent } from "react";
import { API, request, type Source, type WebcamRegion } from "./lib";
import { Dialog, ErrorNotice } from "./ui";

type Point = { x: number; y: number };

export function WebcamToggle({ value, change, disabled }: { value: boolean; change: (value: boolean) => void; disabled?: boolean }) {
  return <label className="field"><span>Есть веб-камера</span>
    <select value={String(value)} disabled={disabled} onChange={(event) => change(event.target.value === "true")}>
      <option value="false">Нет</option><option value="true">Да</option>
    </select>
  </label>;
}

export function WebcamDialog({ source, close, done }: { source: Source; close: () => void; done: () => void }) {
  const [enabled, setEnabled] = useState(source.has_webcam);
  const [region, setRegion] = useState<WebcamRegion | null>(source.webcam_region);
  const [second, setSecond] = useState(Math.min(5, Math.max(0, Math.floor(source.duration - 1))));
  const [frame, setFrame] = useState(second);
  const [frameError, setFrameError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const start = useRef<Point | null>(null);

  function point(event: PointerEvent<HTMLDivElement>): Point {
    const box = event.currentTarget.getBoundingClientRect();
    const percent = (value: number, size: number) => Math.round(Math.max(0, Math.min(100, value / size * 100)) * 100) / 100;
    return { x: percent(event.clientX - box.left, box.width), y: percent(event.clientY - box.top, box.height) };
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    const a = start.current, b = point(event);
    setRegion({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
      width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (enabled && region && (region.width < 2 || region.height < 2)) {
      setError("Выделите область шириной и высотой не менее 2% кадра.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await request("/sources/" + source.id + "/webcam", { method: "PATCH",
        body: JSON.stringify({ has_webcam: enabled, webcam_region: enabled ? region : null }) });
      done();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <Dialog title="Веб-камера источника" close={close} lock={busy}>
    <form className="form" onSubmit={save}>
      <WebcamToggle value={enabled} change={setEnabled} disabled={busy}/>
      {enabled && <>
        <p className="muted">Выделите прямоугольник на исходном кадре. При автоматическом режиме редактор сам найдёт лицо стримера.</p>
        <div className="source-actions">
          <label className="field"><span>Секунда записи</span><input type="number" min={0}
            max={Math.max(0, Math.floor(source.duration - 1))} value={second}
            onChange={(event) => setSecond(Number(event.target.value))}/></label>
          <button type="button" className="button secondary" onClick={() => {
            setFrameError(false); setFrame(Math.max(0, Math.min(second, source.duration - 1)));
          }}>Показать кадр</button>
        </div>
        <div style={{ position: "relative", touchAction: "none", cursor: "crosshair" }}
          onPointerDown={(event) => {
            if (frameError || busy || event.button !== 0) return;
            start.current = point(event); event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={move}
          onPointerUp={(event) => { move(event); start.current = null; }}
          onPointerCancel={() => { start.current = null; }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={API + "/sources/" + source.id + "/webcam/frame?at=" + frame} alt="Исходный кадр"
            draggable={false} onError={() => setFrameError(true)} onLoad={() => setFrameError(false)}
            style={{ display: "block", width: "100%", userSelect: "none" }}/>
          {region && !frameError && <div style={{ position: "absolute", pointerEvents: "none",
            left: region.x + "%", top: region.y + "%", width: region.width + "%", height: region.height + "%",
            border: "2px solid #ffd633", background: "#ffd63330" }}/>}
        </div>
        {frameError && <ErrorNotice>Кадр недоступен. Выберите другую секунду или проверьте исходный файл.</ErrorNotice>}
        <div className="source-actions">
          <span className="muted">{region ? "Ручная область" : "Автоматическое определение"}</span>
          {region && <button type="button" className="button secondary" onClick={() => setRegion(null)}>Сбросить область</button>}
        </div>
      </>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <footer className="dialog-actions"><button className="button primary" disabled={busy}>Сохранить</button></footer>
    </form>
  </Dialog>;
}
