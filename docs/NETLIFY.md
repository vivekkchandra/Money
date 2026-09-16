# Netlify web/control plane

Connect the existing GitHub repository; select production branch `main` and enable Deploy Previews. `netlify.toml` selects `apps/web`, `npm run build` and `.next`. Netlify's Next.js integration handles rendering and route handlers. No Python installation belongs in its build command.

Server-only variables: `RESEARCH_API_URL` (HTTPS origin of the remote API), `RESEARCH_API_TOKEN` (32+ characters), `MONEY_WEB_PASSWORD` (16+ characters), `SESSION_SECRET` (32+ independent random characters). Never prefix these with `NEXT_PUBLIC_`. Store them in Netlify environment configuration. Set separate values for Deploy Previews so previews never enqueue production research. Builds must succeed without runtime credentials; runtime requests fail closed if required settings are absent.

The initial deployment uses a single-user signed HttpOnly session with an eight-hour lifetime. Password rotation invalidates existing sessions. Configure durable Netlify/edge rate limiting for `/api/session` before public production use; the in-process limiter is supplemental and cannot coordinate across serverless instances. The backend enforces durable queue capacity and enqueue rate limits across API replicas.

The browser uses authenticated same-origin routes. POST research returns 202 and a durable ID; polling survives refresh. Backend outages produce a clear unavailable state without exposing upstream error bodies.

References: [Netlify monorepos](https://docs.netlify.com/build/configure-builds/monorepos/), [Next.js on Netlify](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/), [Next.js installation](https://nextjs.org/docs/app/getting-started/installation/).
