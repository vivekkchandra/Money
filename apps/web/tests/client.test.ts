import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/client";

afterEach(() => vi.unstubAllGlobals());

it("turns a non-JSON gateway failure into a bounded unavailable message", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>private provider traceback</html>", { status: 502 })));
  await expect(api("/api/research")).rejects.toThrow("Research service unavailable");
});

it("notifies the workspace when a server-validated session expires", async () => {
  const dispatchEvent = vi.fn();
  vi.stubGlobal("window", { dispatchEvent });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: "Please sign in" }, { status: 401 })));
  await expect(api("/api/research")).rejects.toBeInstanceOf(ApiError);
  expect(dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "money:session-expired" }));
});
