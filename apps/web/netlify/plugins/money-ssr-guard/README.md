# Money SSR deployment guard

Dependency-free local build plugin. Configure it **after** `@netlify/plugin-nextjs`
and keep it last among plugins that alter deployment output:

```toml
[[plugins]]
package = "@netlify/plugin-nextjs"

[[plugins]]
package = "./netlify/plugins/money-ssr-guard"
```

The local path above is relative to Money's `apps/web` build base. The parent
repository's deployment configuration owns this declaration.

The `onPostBuild` hook runs after function bundling but before deployment. A missing
artifact invokes `utils.build.failBuild`, not `failPlugin`; the latter would allow
deployment to continue. Same-stage plugins execute in configuration order.
See [Netlify Build plugin lifecycle and constants](https://docs.netlify.com/extend/develop-and-share/develop-build-plugins/)
and [plugin ordering](https://docs.netlify.com/extend/install-and-use/build-plugins/).

## Versioned artifact assumptions

Netlify's [Next.js documentation](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/)
establishes that SSR and API routes require a generated serverless function.
The [Frameworks API](https://docs.netlify.com/build/frameworks/frameworks-api/)
documents catch-all function routing, but does not promise a fixed Next.js adapter
internal layout. This guard therefore deliberately binds the **published
`@netlify/plugin-nextjs@5.15.11` package**, inspected read-only, rather than guessing
that every adapter version has identical output:

- `dist/build/plugin-context.js` defines `.netlify/functions-internal` relative to
  `constants.PACKAGE_PATH` or the build working directory.
- `dist/build/functions/server.js` emits
  `___netlify-server-handler/___netlify-server-handler.mjs` and the adjacent `.json`
  manifest with `version: 1`, generator `@netlify/plugin-nextjs@5.15.11` and
  `nodeBundler: none`.
- Both `dist/build/templates/handler*.tmpl.js` declare `path: '/*'` and
  `preferStatic: true`. Generated source is inspected as text, never executed.
- `dist/index.js` and `dist/build/content/static.js` swap generated static assets
  into `constants.PUBLISH_DIR` during the adapter's `onPostBuild`. Money requires
  nonempty JavaScript and CSS under `_next/static`, and refuses raw server/build
  metadata in that publish directory.

The documented `constants.FUNCTIONS_DIST` supplies the deployment bundle location;
the guard does not guess `.netlify/functions` or a ZIP filename. The inspected
published Netlify CLI 27.8.0 dependencies, `@netlify/build@37.0.0`
(`lib/plugins_core/functions/zisi.js`) and `@netlify/zip-it-and-ship-it@16.0.0`
(`dist/manifest.js`, `dist/utils/routes.js`), write its `manifest.json` version 1.
The guard requires exactly one matching generated handler, its `/*` route without
exclusions, and the manifest-declared nonempty regular bundle file contained within
`FUNCTIONS_DIST`. Source handlers without a built bundle do not pass.

An adapter/manifest upgrade requires explicit fixture review and a real Netlify
build; an unknown layout fails closed. The guard does not create or repair artifacts,
contact a service, read secrets, or establish production research readiness. A
successful guard is **artifact acceptance only**. Deployed HTTP route, asset, CSP,
session and authenticated backend checks remain separate acceptance requirements.
