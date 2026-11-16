import { Sources } from "../screens";
export default async function Page({ searchParams }: { searchParams: Promise<{ campaign?: string }> }) {
  const { campaign } = await searchParams;
  return <Sources campaign={typeof campaign === "string" ? campaign : undefined}/>;
}
