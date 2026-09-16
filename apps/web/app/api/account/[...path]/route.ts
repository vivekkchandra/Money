import { commercialProxy } from "@/lib/commercial";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function handle(
  request: Request,
  context: {
    params: Promise<{
      path: string[];
    }>;
  }
) {
  return commercialProxy(request, "account", (await context.params).path.join("/"));
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
