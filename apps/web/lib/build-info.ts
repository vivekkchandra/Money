import packageInfo from "../package.json";

/** Only public release identifiers may cross the build/runtime boundary. */
export function buildCommit(environment: Record<string, string | undefined>): string {
  return [environment.COMMIT_REF, environment.MONEY_GIT_SHA].find(value => value && /^[a-f0-9]{40}$/i.test(value))?.toLowerCase() ?? "unknown";
}

export function webRelease() {
  const baked = process.env.MONEY_WEB_BUILD_SHA;
  return {
    status: "ok",
    version: packageInfo.version,
    git_sha: baked === undefined ? buildCommit(process.env) : /^[a-f0-9]{40}$/i.test(baked) ? baked.toLowerCase() : "unknown",
  };
}
