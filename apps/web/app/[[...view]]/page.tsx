import { Workspace } from "@/components/workspace";
import { connection } from "next/server";

export default async function Page({ params }: { params: Promise<{ view?: string[] }> }) {
  await connection();
  const { view = [] } = await params;
  return <Workspace view={view} />;
}
