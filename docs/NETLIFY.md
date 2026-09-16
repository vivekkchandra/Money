# Netlify web/control plane

Keep the existing GitHub connection and Netlify project; do not run initialization
or create another site. Production branch remains `main`, with pull-request Deploy
Previews. `netlify.toml` selects `apps/web`, `npm run build` and `.next`. No Python
or heavy research belongs in its build or request runtime.

Server-only variables: `RESEARCH_API_URL` (HTTPS origin of the remote API), `RESEARCH_API_TOKEN` (32+ characters), `MONEY_WEB_PASSWORD` (16+ characters), `SESSION_SECRET` (32+ independent random characters). Never prefix these with `NEXT_PUBLIC_`. Store them in Netlify environment configuration. Set separate values for Deploy Previews so previews never enqueue production research. Builds must succeed without runtime credentials; runtime requests fail closed if required settings are absent.

The initial deployment is private/single-user with signed HttpOnly, production
Secure, SameSite cookies and an eight-hour lifetime. Random session hashes,
revocation and login rate limits are PostgreSQL-backed through the authenticated
control API. Password/secret rotation invalidates sessions; logout revokes them.
Authentication-service outages fail closed. Origin checks and CSRF protect writes.
Server-only tokens never reach JavaScript bundles.

Explicit `MONEY_ENV` distinguishes production and preview; isolate preview
API/database/secrets. Nonce CSP and other security headers protect the product.
Only development permits the documented evaluation allowance.

The browser uses authenticated same-origin routes. POST research returns 202 and a durable ID; polling survives refresh. Backend outages produce a clear unavailable state without exposing upstream error bodies.

References: [Netlify monorepos](https://docs.netlify.com/build/configure-builds/monorepos/), [Next.js on Netlify](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/), [Next.js installation](https://nextjs.org/docs/app/getting-started/installation/).
