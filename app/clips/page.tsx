import { Clips } from "../screens";
export default async function Page({ searchParams }: { searchParams: Promise<{ campaign?: string; source?: string; selected?: string }> }) {
  const { campaign, source, selected } = await searchParams;
  return <Clips campaign={typeof campaign === "string" ? campaign : undefined} source={typeof source === "string" ? source : undefined}
    initialSelection={typeof selected === "string" ? selected.split(",").filter((id) => /^[a-f0-9-]{36}$/i.test(id)).slice(0, 10) : []}/>;
}
