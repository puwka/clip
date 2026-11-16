"use client";

import Link from "next/link";
import { ImagePlus, LoaderCircle, Megaphone, Upload, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { API, campaignLabels, formatCount, request, useResource, type AdPlacement, type Campaign, type EditAd, type Page } from "./lib";
import { Dialog, ErrorNotice } from "./ui";

const defaultPlacement: AdPlacement = {
  platform: "both", width: 62, x: 50, y: 15, opacity: 92, start: 1, end: null, show_code: true,
};

export function money(amount: number, currency: Campaign["currency"]) {
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency, maximumFractionDigits: 2 }).format(amount);
}

export function forecast(cpm: number, views: number, currency: Campaign["currency"]) {
  return money(cpm * (views / 1000), currency);
}

export const availabilityLabels: Record<Campaign["availability"], string> = {
  ...campaignLabels, scheduled: "По расписанию", expired: "Период завершён", no_banner: "Нужен баннер", available: "Готова к экспорту",
};

export function campaignPeriod(start: string | null, end: string | null) {
  const format = (value: string) => new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(value + "T00:00:00Z"));
  if (start && end) return format(start) + " — " + format(end);
  if (start) return "С " + format(start);
  if (end) return "До " + format(end) + " включительно";
  return "Без ограничения по датам";
}

function PlacementPreview({ banner, promo, placement }: { banner: string; promo: string; placement: AdPlacement }) {
  const [image, setImage] = useState("");
  const [error, setError] = useState("");
  // Layout preview is independent of the duration entered for the final video.
  const body = JSON.stringify({ banner_id: banner, promo_code: promo, placement: { ...placement, start: 0, end: null } });
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(API + "/campaign-banners/preview", {
          method: "POST", signal: controller.signal, body,
          headers: { "Content-Type": "application/json", "X-Clip-Factory": "local" },
        });
        if (!response.ok) throw new Error();
        const image = await response.blob();
        if (!controller.signal.aborted) {
          setImage(URL.createObjectURL(image));
          setError("");
        }
      } catch {
        if (!controller.signal.aborted) setError("Предпросмотр недоступен");
      }
    }, 180);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [body]);
  useEffect(() => () => { if (image) URL.revokeObjectURL(image); }, [image]);

  return <figure className="ad-preview">
    <div>{image ? <img src={image} alt="Баннер в пределах безопасной зоны вертикального видео"/> : <Megaphone size={28}/>}</div>
    <figcaption>{error || "Безопасная зона 9:16"}</figcaption>
  </figure>;
}

export function placementError(placement: AdPlacement, duration?: number) {
  if (placement.end !== null && placement.end <= placement.start) return "Окончание показа должно быть позже начала.";
  if (duration !== undefined && placement.start >= duration) return "Начало показа должно находиться внутри ролика.";
  return "";
}

function PlacementFields({ banner, promo, value, change, duration }: {
  banner: string; promo: string; value: AdPlacement; change: (value: AdPlacement) => void; duration?: number;
}) {
  const sliders = [
    { key: "width", label: "Ширина в зоне", min: 20 },
    { key: "x", label: "По горизонтали", min: 0 },
    { key: "y", label: "По вертикали", min: 0 },
    { key: "opacity", label: "Непрозрачность", min: 1 },
  ] as const;
  const error = placementError(value, duration);
  return <div className="ad-placement">
    <div className="form">
      <label className="field"><span>Безопасная зона</span><select aria-label="Безопасная зона" value={value.platform} onChange={(event) => change({ ...value, platform: event.target.value as AdPlacement["platform"] })}>
        <option value="both">Shorts и TikTok</option><option value="shorts">Shorts</option><option value="tiktok">TikTok</option>
      </select></label>
      <div className="form-columns">{sliders.map(({ key, label, min }) =>
        <label className="edit-range" key={key}><span>{label}<output>{value[key]}%</output></span>
          <input type="range" min={min} max={100} value={value[key]} aria-label={label} onChange={(event) => change({ ...value, [key]: event.target.valueAsNumber })}/>
        </label>,
      )}</div>
      <div className="form-columns">
        <label className="field"><span>Начало показа, сек.</span><input type="number" min={0} max={179.99} step="0.01" required value={value.start} onChange={(event) => change({ ...value, start: event.target.valueAsNumber || 0 })}/></label>
        <label className="field"><span>Конец показа, сек.</span><input type="number" min={0.01} max={180} step="0.01" placeholder="До конца ролика" value={value.end ?? ""} onChange={(event) => change({ ...value, end: Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : null })}/></label>
      </div>
      {promo && <label className="edit-toggle"><input type="checkbox" checked={value.show_code} onChange={(event) => change({ ...value, show_code: event.target.checked })}/> Показывать промокод под баннером</label>}
      {error ? <ErrorNotice>{error}</ErrorNotice> : duration !== undefined && <p className="muted">Показ с {value.start.toLocaleString("ru-RU")} по {Math.min(value.end ?? duration, duration).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} с</p>}
    </div>
    <PlacementPreview banner={banner} promo={promo} placement={value}/>
  </div>;
}

export function CampaignDialog({ campaign, close, done }: { campaign?: Campaign; close: () => void; done: () => void }) {
  const [form, setForm] = useState<Omit<Campaign, "id" | "availability">>({
    name: campaign?.name ?? "", brand: campaign?.brand ?? "",
    description: campaign?.description ?? "", status: campaign?.status ?? "draft",
    promo_code: campaign?.promo_code ?? "", cpm: campaign?.cpm ?? 0,
    currency: campaign?.currency ?? "RUB", starts_on: campaign?.starts_on ?? null, ends_on: campaign?.ends_on ?? null,
    expected_views: campaign?.expected_views ?? 0, banner_id: campaign?.banner_id ?? null,
    placement: campaign?.placement ?? defaultPlacement,
  });
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const busy = saving || uploading;
  const datesInvalid = !!(form.starts_on && form.ends_on && form.starts_on > form.ends_on);
  const timingInvalid = !!placementError(form.placement);

  async function upload(file?: File) {
    if (!file || busy) return;
    setError("");
    if (file.size > 10 * 1024 ** 2) { setError("Баннер должен весить не более 10 МБ."); return; }
    setUploading(true);
    try {
      const asset = await request<{ id: string }>("/campaign-banners", {
        method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" },
      });
      setForm((previous) => ({ ...previous, banner_id: asset.id }));
    } catch (error) { setError((error as Error).message); }
    finally { setUploading(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || datesInvalid || timingInvalid) return;
    setSaving(true);
    setError("");
    try {
      await request("/campaigns" + (campaign ? "/" + campaign.id : ""), {
        method: campaign ? "PATCH" : "POST", body: JSON.stringify(form),
      });
      done();
    } catch (error) { setError((error as Error).message); }
    finally { setSaving(false); }
  }

  return <Dialog title={campaign ? "Изменить кампанию" : "Новая кампания"} close={close} lock={busy} wide>
    <form onSubmit={save} className="form">
      <fieldset disabled={busy} className="form">
        <div className="form-columns">
          <label className="field"><span>Название кампании</span><input required maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })}/></label>
          <label className="field"><span>Рекламодатель</span><input required maxLength={120} value={form.brand} onChange={(event) => setForm({ ...form, brand: event.target.value })}/></label>
          <label className="field"><span>Промокод</span><input maxLength={32} value={form.promo_code} onChange={(event) => setForm({ ...form, promo_code: event.target.value })}/></label>
          <label className="field"><span>Статус</span><select aria-label="Статус" value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as Campaign["status"] })}>
            {Object.entries(campaignLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label className="field"><span>Начало кампании</span><input type="date" value={form.starts_on ?? ""} onChange={(event) => setForm({ ...form, starts_on: event.target.value || null })}/></label>
          <label className="field"><span>Конец кампании</span><input type="date" value={form.ends_on ?? ""} onChange={(event) => setForm({ ...form, ends_on: event.target.value || null })}/></label>
        </div>
        {datesInvalid && <ErrorNotice>Дата окончания должна быть не раньше начала.</ErrorNotice>}
        <div className="ad-forecast-fields">
          <label className="field"><span>CPM</span><input type="number" step="0.0001" min={0} max={1000000} required value={form.cpm} onChange={(event) => setForm({ ...form, cpm: event.target.valueAsNumber || 0 })}/></label>
          <label className="field"><span>Валюта</span><select aria-label="Валюта" value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value as Campaign["currency"] })}>
            <option value="RUB">RUB</option><option value="USD">USD</option><option value="EUR">EUR</option>
          </select></label>
          <label className="field"><span>План просмотров</span><input type="number" step="1" min={0} max={1000000000} required value={form.expected_views} onChange={(event) => setForm({ ...form, expected_views: event.target.valueAsNumber || 0 })}/></label>
        </div>
        <div className="ad-forecast"><span>Прогноз по кампании</span><strong>{forecast(form.cpm, form.expected_views, form.currency)}</strong></div>
        <label className="field"><span>Описание</span><textarea maxLength={2000} rows={2} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })}/></label>
        <div className="ad-upload">
          {form.banner_id && <img src={API + "/campaign-banners/" + form.banner_id} alt="Загруженный баннер"/>}
          <label className="button secondary">
            {uploading ? <LoaderCircle size={16} className="spin"/> : <Upload size={16}/>} {form.banner_id ? "Заменить баннер" : "Загрузить PNG или WebP"}
            <input type="file" className="sr-only" aria-label="Загрузить баннер PNG или WebP" accept="image/png,image/webp,.png,.webp"
              onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ""; }}/>
          </label>
          {form.banner_id ? <button type="button" className="icon-button" aria-label="Убрать баннер" onClick={() => setForm({ ...form, banner_id: null })}><X size={16}/></button> : <small className="muted">До 10 МБ</small>}
        </div>
        {form.banner_id && <PlacementFields banner={form.banner_id} promo={form.promo_code.trim()} value={form.placement} change={(placement) => setForm({ ...form, placement })}/>}
        {!form.banner_id && timingInvalid && <ErrorNotice>{placementError(form.placement)}</ErrorNotice>}
      </fieldset>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <footer className="dialog-actions"><button className="button secondary" type="button" onClick={close} disabled={busy}>Отмена</button><button className="button primary" disabled={busy || datesInvalid || timingInvalid}>Сохранить</button></footer>
    </form>
  </Dialog>;
}

export function EditorAdvertising({ ad, change, duration, disabled }: {
  ad: EditAd | null; change: (value: EditAd | null) => void; duration: number; disabled: boolean;
}) {
  const path = "/campaigns?for_export=true";
  const { data, error } = useResource<Page<Campaign>>(path);
  const [extra, setExtra] = useState<Campaign[]>([]);
  const [next, setNext] = useState<number | null | undefined>();
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const items = Array.from(new Map([...(ad ? [ad.campaign] : []), ...(data?.items ?? []), ...extra]
    .filter((campaign) => campaign.availability === "available")
    .map((campaign) => [campaign.id, campaign])).values());
  const offset = next === undefined ? data?.next : next;

  async function more() {
    if (!offset || loading) return;
    setLoading(true);
    setListError("");
    try {
      const page = await request<Page<Campaign>>(path + "&offset=" + offset);
      setExtra((previous) => [...previous, ...page.items]);
      setNext(page.next);
    } catch (error) { setListError((error as Error).message); }
    finally { setLoading(false); }
  }

  return <fieldset className="panel editor-panel" disabled={disabled}>
    <h2><Megaphone size={17}/> Реклама в ролике</h2>
    <label className="field"><span>Кампания для этого экспорта</span><select aria-label="Кампания для этого экспорта" value={ad?.campaign.id ?? ""} onChange={(event) => {
      const campaign = items.find((item) => item.id === event.target.value);
      change(campaign ? { campaign, placement: campaign.placement, expected_views: campaign.expected_views } : null);
    }}>
      <option value="">Без рекламы</option>
      {items.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.brand} · {campaign.name}</option>)}
    </select></label>
    {offset && <button type="button" className="text-link" onClick={more} disabled={loading}>Показать ещё кампании</button>}
    {(error || listError) && <ErrorNotice>{error || listError}</ErrorNotice>}
    {data && !items.length && !offset && <Link className="text-link" href="/campaigns"><ImagePlus size={15}/> Добавить активную кампанию с баннером</Link>}
    {ad?.campaign.banner_id && <>
      <PlacementFields banner={ad.campaign.banner_id} promo={ad.campaign.promo_code} value={ad.placement} duration={duration}
        change={(placement) => change({ ...ad, placement })}/>
      <label className="field"><span>Ожидаемые просмотры ролика</span><input type="number" min={0} max={1000000000} step={1} required
        value={ad.expected_views} onChange={(event) => change({ ...ad, expected_views: event.target.valueAsNumber || 0 })}/></label>
      <div className="ad-forecast"><span>Прогноз при CPM {money(ad.campaign.cpm, ad.campaign.currency)}</span><strong>{forecast(ad.campaign.cpm, ad.expected_views, ad.campaign.currency)}</strong></div>
    </>}
  </fieldset>;
}
