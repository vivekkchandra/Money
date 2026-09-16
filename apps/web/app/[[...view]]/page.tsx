import { Workspace } from "@/components/workspace";

export default async function Page({ params }: { params: Promise<{ view?: string[] }> }) {
  const { view = [] } = await params;
  return <Workspace view={view} />;
}
