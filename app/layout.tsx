import type { Metadata } from "next";
import "@fontsource-variable/manrope";
import "@fontsource-variable/geist-mono";
import "./globals.css";
import { Workspace } from "./workspace";

export const metadata: Metadata = {
  title: "Clip Factory",
  description: "Локальная студия автоматических нарезок игровых стримов.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body><Workspace>{children}</Workspace></body></html>;
}
