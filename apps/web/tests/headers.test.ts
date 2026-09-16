import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";

afterEach(() => vi.unstubAllEnvs());

it("uses unpredictable per-request nonces without production inline or eval allowances", () => {
  vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("MONEY_ENV", "production");
  const request = new NextRequest("https://money.example.test/");
  const first = proxy(request);
  const second = proxy(request);
  const policy = first.headers.get("content-security-policy")!;
  expect(policy).toContain("'nonce-");
  expect(policy).toContain("object-src 'none'");
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).not.toContain("unsafe-inline");
  expect(policy).not.toContain("unsafe-eval");
  expect(second.headers.get("content-security-policy")).not.toBe(policy);
  expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(policy);
  expect(first.headers.get("strict-transport-security")).toBe("max-age=31536000");
  expect(first.headers.get("cache-control")).toContain("no-store");
});
