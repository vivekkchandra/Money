import { proxyBackend } from "@/lib/backend";
import { json } from "@/lib/auth";
import { webRelease } from "@/lib/build-info";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const response = await proxyBackend(request, "/health/ready");
  // Public web liveness is distinct from authenticated backend readiness.
  return json({ ...await response.json(), web: webRelease() }, response.status);
}
