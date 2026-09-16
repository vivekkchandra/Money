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

## Existing project and incident acceptance

- Project: `neon-griffin-08e616`; ID: `c66f8305-899f-4deb-92c8-b2ee55f5acab`.
- Production: https://neon-griffin-08e616.netlify.app ; Git branch: `main`.
- The local `.netlify/state.json` matches the ID. Remote account/project name,
  published commit and remote account identity still require direct verification.
- The user reported the public generic Netlify 404. Local Next output contains the
  optional catch-all SSR root and all API handlers. The root configuration already
  has base `apps/web`, command `npm run build`, publish `.next` (relative to base).
  This does **not** establish which assets or commit Netlify actually published.
  The later supplied deployment log below establishes a missing adapter, not a
  base/publish-path error. No SPA rewrite or static export was introduced.

### Supplied production log: missing SSR adapter

The user supplied the full build log for deploy `6aaaa3c8a1d24e0009cc631b`,
2026-09-16, displayed 15:12–15:13. Its actual context is `production`, with Git
reference `refs/heads/main`; it does not disclose the built commit SHA.

| Observed log evidence | Conclusion |
| --- | --- |
| CWD `/opt/build/repo/apps/web`; config `/opt/build/repo/netlify.toml`; command `npm run build`; publish `apps/web/.next` | Base-relative paths are correct |
| Next 16.3.5 builds dynamic `/[[...view]]`, API route handlers and Proxy | This requires a hybrid runtime, not a static upload |
| Both Next and Vite detected; no Next adapter lifecycle/banner in supplied log | Next detection did not result in adapter execution; why automatic installation was absent is not established |
| Functions bundling targets nonexistent `netlify/functions`; deploy uploads 40 files and zero new functions | No generated SSR serving handler is evidenced; raw `.next` publication explains the reported generic root 404 |
| Post-processing reports site live | Upload success is not application serving success |

The user's subsequent Netlify API inspection of this exact project additionally
reported `plugins: []` and `available_functions: []`. This confirms the absent
installed runtime/function state, rather than merely inferring it from a missing
log banner. Raw API credentials were not requested or copied. No successful
post-repair deploy or live response has yet been supplied or directly observed.

The repository now explicitly declares `@netlify/plugin-nextjs` in `netlify.toml`
and locks adapter `5.15.11` as a web development dependency. This exact version
was available in the local npm cache with declared Node >=18 compatibility; it
was not guessed or labelled the latest version. Dependency installation succeeded
offline; a current online advisory scan and actual adapter build are still required.
No main-branch application dependencies were otherwise upgraded.

The dependency-free local `money-ssr-guard` runs after the adapter's post-build
hook and before deployment. It requires the versioned handler source, actual
bundled handler manifest/file and `/*` route, plus published JavaScript/CSS, and
rejects raw server/build metadata in the publish directory. A missing artifact
fails the build rather than merely disabling the plugin. The guard is pinned to
the inspected adapter 5.15.11 / bundle-manifest-v1 contracts; upgrades require
review and a real build. Fixture tests are not a successful Netlify artifact run.

Until this local fix can be published, the existing authorized project's UI can
enable the Next.js Runtime under **Project configuration → Build & deploy → Build
plugins**, then retry the deployment without cache. This is the documented
installation route for an existing project, not project initialization. Require
Next adapter execution and generated SSR functions in the new log, then run the
public HTTP checker. Do not change `.next` to an unrelated directory. If the
adapter is still skipped, inspect the project's runtime/skip configuration rather
than inventing an `index.html`.

Use the CLI through npx, never a global installation or initialization:

```sh
npx --yes netlify-cli@latest status
npx --yes netlify-cli@latest logs --source deploy --since 24h
npx --yes netlify-cli@latest build
npm run check:deployment --prefix apps/web -- https://neon-griffin-08e616.netlify.app --production
```

Stop before remote mutations if the linked ID/name differs. Inspect resolved base,
command/publish, Git SHA, OpenNext adapter/server artifacts and publication status
in the actual deploy log. A `.next` directory without Netlify's server adapter
artifacts is not proof of a hybrid deployment. Do not upload it as a plain static
site or add `/* /index.html 200`.

The read-only HTTP checker tests `/`, `/dashboard`, `/research`, `/system`, session,
authenticated control routes, linked JavaScript/CSS, nonce CSP and production
headers. It requires recognizable Money content and rejects generic host 404s,
HTML returned for assets and unavailable route handlers. Missing backend/auth
configuration remains explicit; this check does not certify login or research.
`live-web.yml` runs it against only this existing public URL without secrets or
deployment writes. Its observations must pass on the actual release, not just
against injected unit-test responses.

References: [Netlify monorepos](https://docs.netlify.com/build/configure-builds/monorepos/), [Next.js on Netlify](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/), [Build plugin installation](https://docs.netlify.com/extend/install-and-use/build-plugins/), [Next.js installation](https://nextjs.org/docs/app/getting-started/installation/).
