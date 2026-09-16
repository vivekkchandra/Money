import { proxyBackend } from "@/lib/backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path?: string[] }> };

async function handle(request: Request, context: Context) {
  const { path = [] } = await context.params;
  const suffix = path.length ? `/${path.join("/")}` : "";
  const resource = ["signals", "outcomes", "discovery"].includes(path[0]) ? `/research${suffix}` : `/research/jobs${suffix}`;
  return proxyBackend(request, resource);
}
export { handle as GET, handle as POST };
