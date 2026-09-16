import type { NextConfig } from "next";
import { buildCommit } from "./lib/build-info";

const config: NextConfig = {
  poweredByHeader: false,
  // Public metadata only. Bake Netlify's build SHA so runtime environment changes
  // cannot make an older bundle masquerade as the latest deployment.
  env: { MONEY_WEB_BUILD_SHA: buildCommit(process.env) },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "same-origin" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ] }];
  },
};

export default config;
