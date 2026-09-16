import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import guard from "../netlify/plugins/money-ssr-guard/index.js";

const handler = "___netlify-server-handler";
const adapter = "@netlify/plugin-nextjs@5.15.11";
const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  vi.restoreAllMocks();
});

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), "money-ssr-guard-test-"));
  directories.push(directory);
  const publish = join(directory, ".next");
  const source = join(directory, ".netlify/functions-internal", handler);
  const functionsDist = join(directory, ".netlify/functions-dist");
  const bundle = join(functionsDist, "fixture-handler.zip");
  function write(file: string, value: string | object) {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, typeof value === "string" ? value : JSON.stringify(value));
  }
  write(join(source, `${handler}.mjs`), "// Synthetic adapter-output fixture, never deployed.\nexport const config = { path: '/*', preferStatic: true, }\n");
  write(join(source, `${handler}.json`), { version: 1, config: { generator: adapter, nodeBundler: "none" } });
  write(bundle, "synthetic bundled function bytes; not a real deployment archive");
  const manifest = { version: 1, functions: [{ name: handler, generator: adapter, path: bundle, routes: [{ pattern: "/*", expression: "^/.*$", preferStatic: true }] }] };
  write(join(functionsDist, "manifest.json"), manifest);
  write(join(publish, "_next/static/chunks/app.js"), "/* synthetic JS */");
  write(join(publish, "_next/static/css/app.css"), "/* synthetic CSS */");
  const failBuild = vi.fn((message: string): never => { throw new Error(message); });
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  return { directory, publish, source, functionsDist, bundle, manifest, write, failBuild, log,
    options: { constants: { PACKAGE_PATH: directory, PUBLISH_DIR: publish, FUNCTIONS_DIST: functionsDist }, utils: { build: { failBuild } } },
  };
}

describe("Netlify post-build SSR artifact guard", () => {
  it("accepts a complete version-bound artifact fixture without asserting runtime qualification", async () => {
    const sample = fixture();
    await guard.onPostBuild(sample.options);
    expect(sample.failBuild).not.toHaveBeenCalled();
    expect(sample.log).toHaveBeenCalledWith(expect.stringContaining('"bundled_ssr_handlers":1'));
    expect(sample.log.mock.calls.join(" ")).not.toContain(sample.directory);
  });

  it("rejects raw Next output even when next build has succeeded", async () => {
    const sample = fixture();
    rmSync(join(sample.directory, ".netlify"), { recursive: true });
    sample.write(join(sample.publish, "BUILD_ID"), "synthetic successful next build");
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_HANDLER_MANIFEST_MISSING");
    expect(sample.failBuild).toHaveBeenCalledOnce();
    expect(sample.log).not.toHaveBeenCalled();
  });

  it.each(["handler", "bundled-manifest", "bundle"])("rejects missing %s artifacts", async (missing) => {
    const sample = fixture();
    const file = missing === "handler" ? join(sample.source, `${handler}.mjs`) : missing === "bundled-manifest" ? join(sample.functionsDist, "manifest.json") : sample.bundle;
    rmSync(file);
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow(/MONEY_SSR_(HANDLER_ENTRY|BUNDLED_MANIFEST|BUNDLE)_MISSING/);
  });

  it("requires a real bundled SSR entry, not unrelated functions", async () => {
    const sample = fixture();
    sample.manifest.functions[0].name = "unrelated-function";
    sample.write(join(sample.functionsDist, "manifest.json"), sample.manifest);
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_BUNDLED_HANDLER_MISSING");
  });

  it.each(["source", "bundled"])("fails closed when the %s adapter version changes", async (changed) => {
    const sample = fixture();
    if (changed === "source") sample.write(join(sample.source, `${handler}.json`), { version: 1, config: { generator: "@netlify/plugin-nextjs@6.0.0", nodeBundler: "none" } });
    else {
      sample.manifest.functions[0].generator = "@netlify/plugin-nextjs@6.0.0";
      sample.write(join(sample.functionsDist, "manifest.json"), sample.manifest);
    }
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow(/MONEY_SSR_(ADAPTER_CONTRACT_MISMATCH|BUNDLED_HANDLER_MISSING)/);
  });

  it.each(["source", "bundled"])("requires catch-all %s routing instead of a handler file alone", async (changed) => {
    const sample = fixture();
    if (changed === "source") sample.write(join(sample.source, `${handler}.mjs`), "export const config = { path: '/different', preferStatic: true }");
    else {
      sample.manifest.functions[0].routes = [];
      sample.write(join(sample.functionsDist, "manifest.json"), sample.manifest);
    }
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow(/MONEY_SSR_(HANDLER|BUNDLED)_ROUTING_MISSING/);
  });

  it.each(["BUILD_ID", "server", "required-server-files.json"])("rejects unswapped publish output containing %s", async (name) => {
    const sample = fixture();
    sample.write(join(sample.publish, name), "raw server output");
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_RAW_NEXT_OUTPUT");
  });

  it("rejects empty assets and missing CSS", async () => {
    const sample = fixture();
    const css = join(sample.publish, "_next/static/css/app.css");
    sample.write(css, "");
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_STATIC_ASSET_EMPTY");
    rmSync(css);
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_STATIC_ASSETS_MISSING");
  });

  it("does not accept a bundle path outside the deployment function directory", async () => {
    const sample = fixture();
    const outside = join(sample.directory, "outside.zip");
    sample.write(outside, "not part of the function deployment");
    sample.manifest.functions[0].path = outside;
    sample.write(join(sample.functionsDist, "manifest.json"), sample.manifest);
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_BUNDLE_PATH_INVALID");
  });

  it("rejects symlinked static assets and oversized manifests", async () => {
    const sample = fixture();
    const css = join(sample.publish, "_next/static/css/app.css");
    rmSync(css);
    symlinkSync(sample.bundle, css);
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_STATIC_SYMLINK_UNSUPPORTED");
    sample.write(join(sample.source, `${handler}.json`), "x".repeat(65537));
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_HANDLER_MANIFEST_MISSING");
  });

  it("does not echo malformed artifact contents into deployment logs", async () => {
    const sample = fixture();
    sample.write(join(sample.functionsDist, "manifest.json"), "secret-fixture-content invalid json");
    await expect(guard.onPostBuild(sample.options)).rejects.toThrow("MONEY_SSR_BUNDLED_MANIFEST_MISSING");
    expect(sample.failBuild.mock.calls.join(" ")).not.toContain("secret-fixture-content");
  });

  it.each(["handler.tmpl.js", "handler-monorepo.tmpl.js"])("recognizes the installed pinned package's published %s routing contract", async (template) => {
    const sample = fixture();
    const publishedTemplate = readFileSync(new URL(`../node_modules/@netlify/plugin-nextjs/dist/build/templates/${template}`, import.meta.url), "utf8");
    sample.write(join(sample.source, `${handler}.mjs`), publishedTemplate);
    await guard.onPostBuild(sample.options);
    expect(sample.failBuild).not.toHaveBeenCalled();
  });
});
