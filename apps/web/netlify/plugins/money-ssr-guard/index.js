// Dependency-free CommonJS Netlify Build plugin. Keep after @netlify/plugin-nextjs.
// Contract: adapter 5.15.11; Netlify Build FUNCTIONS_DIST manifest v1.
// See README.md for the official lifecycle and inspected published artifact contracts.
const ADAPTER = "@netlify/plugin-nextjs@5.15.11";
const HANDLER = "___netlify-server-handler";

async function verifyArtifacts(constants) {
  const fs = await import("node:fs/promises");
  const path = await import("node:path");
  function requireCheck(condition, code) {
    if (!condition) throw new Error(code);
  }
  async function regularFile(file, code, maximumBytes = Infinity) {
    let metadata;
    try { metadata = await fs.lstat(file); } catch { throw new Error(code); }
    requireCheck(metadata.isFile() && metadata.size > 0 && metadata.size <= maximumBytes, code);
    return metadata;
  }
  async function jsonFile(file, code, maximumBytes = 65536) {
    await regularFile(file, code, maximumBytes);
    try { return JSON.parse(await fs.readFile(file, "utf8")); } catch { throw new Error(code); }
  }
  async function absent(file, code) {
    try { await fs.lstat(file); } catch (error) {
      if (error.code === "ENOENT") return;
      throw new Error(code);
    }
    throw new Error(code);
  }
  requireCheck(typeof constants?.PUBLISH_DIR === "string" && constants.PUBLISH_DIR.length > 0, "MONEY_SSR_PUBLISH_DIR_MISSING");
  requireCheck(typeof constants?.FUNCTIONS_DIST === "string" && constants.FUNCTIONS_DIST.length > 0, "MONEY_SSR_FUNCTIONS_DIST_MISSING");
  requireCheck(constants.PACKAGE_PATH === undefined || typeof constants.PACKAGE_PATH === "string", "MONEY_SSR_PACKAGE_PATH_INVALID");
  const publish = path.resolve(constants.PUBLISH_DIR);
  const functionsDist = path.resolve(constants.FUNCTIONS_DIST);
  // Resolve exactly as the pinned adapter's PluginContext.resolveFromPackagePath.
  const handler = path.resolve(constants.PACKAGE_PATH || "", ".netlify/functions-internal", HANDLER);
  const sourceManifest = await jsonFile(path.join(handler, `${HANDLER}.json`), "MONEY_SSR_HANDLER_MANIFEST_MISSING");
  requireCheck(sourceManifest.version === 1 && sourceManifest.config?.generator === ADAPTER && sourceManifest.config?.nodeBundler === "none", "MONEY_SSR_ADAPTER_CONTRACT_MISMATCH");
  const entry = path.join(handler, `${HANDLER}.mjs`);
  await regularFile(entry, "MONEY_SSR_HANDLER_ENTRY_MISSING", 65536);
  const entryCode = await fs.readFile(entry, "utf8");
  // Both published v5.15.11 templates end with this static, bundler-readable config.
  // Never import/evaluate generated server code during a deployment assertion.
  requireCheck(/export const config\s*=\s*\{\s*path:\s*['"]\/\*['"],\s*preferStatic:\s*true,?\s*\}\s*;?\s*$/.test(entryCode), "MONEY_SSR_HANDLER_ROUTING_MISSING");

  const bundled = await jsonFile(path.join(functionsDist, "manifest.json"), "MONEY_SSR_BUNDLED_MANIFEST_MISSING", 1048576);
  requireCheck(bundled.version === 1 && Array.isArray(bundled.functions) && bundled.functions.length <= 1000, "MONEY_SSR_BUNDLED_MANIFEST_INVALID");
  const handlers = bundled.functions.filter((item) => item?.name === HANDLER);
  requireCheck(handlers.length === 1 && handlers[0].generator === ADAPTER, "MONEY_SSR_BUNDLED_HANDLER_MISSING");
  const deployedHandler = handlers[0];
  requireCheck(Array.isArray(deployedHandler.routes) && deployedHandler.routes.some((route) => route?.pattern === "/*") && (!deployedHandler.excludedRoutes || deployedHandler.excludedRoutes.length === 0), "MONEY_SSR_BUNDLED_ROUTING_MISSING");
  requireCheck(typeof deployedHandler.path === "string" && path.isAbsolute(deployedHandler.path), "MONEY_SSR_BUNDLE_PATH_INVALID");
  await regularFile(deployedHandler.path, "MONEY_SSR_BUNDLE_MISSING");
  const relativeBundle = path.relative(await fs.realpath(functionsDist), await fs.realpath(deployedHandler.path));
  requireCheck(relativeBundle.length > 0 && relativeBundle !== ".." && !relativeBundle.startsWith(`..${path.sep}`) && !path.isAbsolute(relativeBundle), "MONEY_SSR_BUNDLE_PATH_INVALID");

  // The adapter's onPostBuild swaps staticDir into PUBLISH_DIR before deployment.
  // Raw Next server output must never become publicly served static content.
  for (const name of ["BUILD_ID", "server", "required-server-files.json"]) {
    await absent(path.join(publish, name), "MONEY_SSR_RAW_NEXT_OUTPUT");
  }
  const directories = [{ directory: path.join(publish, "_next/static"), depth: 0 }];
  let inspected = 0;
  let javascript = 0;
  let stylesheets = 0;
  while (directories.length) {
    const { directory, depth } = directories.pop();
    requireCheck(depth <= 16, "MONEY_SSR_ASSET_BOUNDS_EXCEEDED");
    const directoryMetadata = await fs.lstat(directory);
    requireCheck(directoryMetadata.isDirectory(), "MONEY_SSR_STATIC_ASSETS_MISSING");
    const entries = await fs.opendir(directory);
    for await (const item of entries) {
      requireCheck(++inspected <= 10000, "MONEY_SSR_ASSET_BOUNDS_EXCEEDED");
      const file = path.join(directory, item.name);
      requireCheck(!item.isSymbolicLink(), "MONEY_SSR_STATIC_SYMLINK_UNSUPPORTED");
      if (item.isDirectory()) directories.push({ directory: file, depth: depth + 1 });
      else if (item.isFile() && /\.(?:js|css)$/.test(item.name)) {
        await regularFile(file, "MONEY_SSR_STATIC_ASSET_EMPTY");
        if (item.name.endsWith(".js")) javascript++;
        else stylesheets++;
      }
    }
  }
  requireCheck(javascript > 0 && stylesheets > 0, "MONEY_SSR_STATIC_ASSETS_MISSING");
  return { adapter: ADAPTER, bundled_ssr_handlers: handlers.length, catch_all_route: true, javascript, stylesheets };
}

module.exports = {
  async onPostBuild({ constants, utils }) {
    try {
      const verified = await verifyArtifacts(constants);
      console.log(`Money SSR deployment artifacts verified: ${JSON.stringify(verified)}`);
    } catch (error) {
      const code = /^MONEY_SSR_[A-Z_]+$/.test(error.message) ? error.message : "MONEY_SSR_ARTIFACT_CHECK_FAILED";
      return utils.build.failBuild(`Money SSR deployment guard failed (${code}). Refusing raw or incomplete Next.js deployment. Verify the pinned Next.js adapter ran before money-ssr-guard.`);
    }
  },
};
