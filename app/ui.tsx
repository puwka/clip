"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { AlertCircle, LoaderCircle, X, type LucideIcon } from "lucide-react";

export function Dialog({ title, children, close, wide, lock }: { title: string; children: ReactNode; close: () => void; wide?: boolean; lock?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog className={wide ? "dialog dialog-wide" : "dialog"} ref={ref} onCancel={(event) => { event.preventDefault(); if (!lock) close(); }} onClick={(event) => { if (!lock && event.target === event.currentTarget) close(); }} aria-labelledby="dialog-title">
    <div className="dialog-inner">
      <header><h2 id="dialog-title">{title}</h2><button className="icon-button" type="button" disabled={lock} aria-label="Закрыть" onClick={close}><X size={19} /></button></header>
      {children}
    </div>
  </dialog>;
}

export function ErrorNotice({ children }: { children: ReactNode }) {
  return <div className="notice" role="alert"><AlertCircle size={17} /><span>{children}</span></div>;
}

export function Loading() {
  return <div className="loading" role="status"><LoaderCircle size={18} className="spin" /> Загрузка…</div>;
}

export function Empty({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description: string; action?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Icon size={24} strokeWidth={1.6} /></span><strong>{title}</strong><p>{description}</p>{action}</div>;
}

export function PageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="page-heading"><div><h1>{title}</h1><p>{description}</p></div>{action}</div>;
}
