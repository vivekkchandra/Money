/** NextRequest canonicalizes loopback IP URLs to localhost; keep Origin identical.
 * Listener bindings remain explicitly 127.0.0.1 in both isolated smoke harnesses.
 * This is test configuration, never an exception to the application's CSRF policy.
 */
export function smokeWebOrigin(port) {
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error("Invalid smoke web port");
  return `http://localhost:${port}`;
}
