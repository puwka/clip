"use client";

import { useCallback, useEffect, useState } from "react";

export const API = (process.env.NEXT_PUBLIC_SAME_ORIGIN_API === "1"
  ? "/api" : process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/$/, "");
export const sourceLabels = { queued: "В очереди", processing: "В обработке", completed: "Готово", failed: "Ошибка" };
export const campaignLabels = { draft: "Черновик", active: "Активна", paused: "На паузе", finished: "Завершена" };
export const clipCategories = { funny: "Смешной момент", shout: "Крик", crash: "Авария", surprise: "Неожиданность", other: "Другой момент" };

export type WebcamRegion = { x: number; y: number; width: number; height: number };
export type Source = {
  has_webcam: boolean; webcam_region: WebcamRegion | null;
  id: string; name: string; status: keyof typeof sourceLabels; progress: number;
  byte_size: number; duration: number; created_at: string; error: string | null;
  campaign_id: string | null; cf_campaigns: { name: string } | null;
  aspect: "landscape" | "portrait";
};
export type Clip = {
  lossless?: boolean;
  id: string; title: string; start_seconds: number; end_seconds: number; score_percent: number; created_at: string;
  category?: keyof typeof clipCategories | null; reason?: string | null;
  source_id: string; cf_sources: { name: string; aspect: Source["aspect"]; cf_campaigns: { name: string } | null };
};
export type Campaign = {
  id: string; name: string; brand: string; description: string; status: keyof typeof campaignLabels;
  promo_code: string; cpm: number; currency: "RUB" | "USD" | "EUR";
  starts_on: string | null; ends_on: string | null; expected_views: number; banner_id: string | null;
  placement: AdPlacement; availability: keyof typeof campaignLabels | "scheduled" | "expired" | "no_banner" | "available";
};
export type AdPlacement = {
  platform: "both" | "shorts" | "tiktok"; width: number; x: number; y: number;
  opacity: number; start: number; end: number | null; show_code: boolean;
};
export type EditAd = { campaign: Campaign; placement: AdPlacement; expected_views: number };
export type AdSnapshot = Pick<Campaign, "name" | "brand" | "promo_code" | "cpm" | "currency" | "expected_views" | "placement">;
export type Settings = { clip_seconds: number; aspect: Source["aspect"]; twitch_segment_minutes?: 15 | 60 };
export type OllamaMode = "local" | "network";
export type OllamaConnection = { url: string; model: string; mode: OllamaMode };
export type OllamaStatus = { state: string; detail: string };
export type Health = {
  mode?: "catalog" | "local";
  database: boolean; ffmpeg: boolean; worker: boolean; max_upload_bytes: number; error: string | null;
  ollama: boolean; ollama_model: string; whisper: boolean; whisper_model: string;
  ollama_mode: OllamaMode; ollama_status: OllamaStatus; ollama_queue: { waiting: number; active: number };
};
export type Page<T> = { items: T[]; next: number | null };
export type QueueJob = { id: string; status: keyof typeof sourceLabels; progress: number; error: string | null; created_at: string };
export type ImportJob = QueueJob & { name: string; host: string; source_id: string | null };
export type Summary = {
  counts: { sources: number; processing: number; failed: number; clips: number; campaigns: number };
  sources: Source[]; clips: Clip[];
};

function reason(payload: { detail?: unknown } | null) {
  return typeof payload?.detail === "string" ? payload.detail : "Проверьте введённые данные и повторите.";
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API + path, {
      cache: "no-store", ...init,
      headers: { ...(init.method && init.method !== "GET" ? { "Content-Type": "application/json", "X-Clip-Factory": "local" } : {}), ...init.headers },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error(API === "/api" ? "Нет связи с API на Vercel." : "Нет связи с локальным сервером FastAPI.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(reason(data));
  return data as T;
}

export function uploadVideo(file: File, campaign: string, hasWebcam: boolean, onProgress: (n: number) => void, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const query = new URLSearchParams({ filename: file.name, has_webcam: String(hasWebcam) });
    if (campaign) query.set("campaign", campaign);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API + "/sources?" + query);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Clip-Factory", "local");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => xhr.status === 201 ? resolve() : reject(new Error(reason(xhr.response)));
    xhr.onerror = () => reject(new Error("Соединение с локальным сервером прервано."));
    xhr.onabort = () => reject(new Error("Загрузка отменена."));
    const abort = () => xhr.abort();
    signal.addEventListener("abort", abort, { once: true });
    xhr.onloadend = () => signal.removeEventListener("abort", abort);
    xhr.send(file);
  });
}

export function useResource<T>(path: string | null, revision = 0, interval = 0) {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string }>({ path, data: null, error: "" });
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!path) return;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        const data = await request<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted) setState({ path, data, error: "" });
      } catch (error) {
        if (!controller.signal.aborted) {
          setState((previous) => ({ path, data: previous.path === path ? previous.data : null, error: (error as Error).message }));
        }
      } finally {
        if (interval && !controller.signal.aborted) timer = setTimeout(load, interval);
      }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, revision, refresh, interval]);
  const reload = useCallback(() => setRefresh((current) => current + 1), []);
  return { ...(state.path === path ? state : { data: null, error: "" }), reload };
}

export const formatCount = (number: number) => new Intl.NumberFormat("ru-RU").format(number);

export function timecode(seconds: number, precision = 0) {
  const divisor = 10 ** precision;
  const rounded = (precision ? Math.round(seconds * divisor) : Math.floor(seconds)) / divisor;
  const total = Math.floor(rounded);
  const two = (value: number) => String(value).padStart(2, "0");
  const hours = Math.floor(total / 3600);
  const fraction = precision ? "." + String(Math.round((rounded - total) * divisor)).padStart(precision, "0") : "";
  return (hours > 0 ? hours + ":" : "") + two(Math.floor(total / 60) % 60) + ":" + two(total % 60) + fraction;
}

export function fileSize(bytes: number) {
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const index = Math.min(3, Math.max(0, Math.floor(Math.log(bytes || 1) / Math.log(1024))));
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(bytes / 1024 ** index) + " " + units[index];
}

export function date(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}
