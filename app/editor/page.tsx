import { Editor } from "./studio";

export default async function Page({ searchParams }: { searchParams: Promise<{ clips?: string; job?: string }> }) {
  const { clips, job } = await searchParams;
  const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
  const ids = typeof clips === "string" ? [...new Set(clips.split(",").filter((id) =>
    uuid.test(id),
  ))].slice(0, 10) : [];
  const initialJob = typeof job === "string" && uuid.test(job) ? job : null;
  return <Editor key={ids.join(",") + (initialJob ?? "")} ids={ids} initialJob={initialJob}/>;
}
