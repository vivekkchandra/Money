/** Product aliases stay within the existing dynamic Next.js application. */
export function canonicalView(view: readonly string[]): string[] {
  if (view.length === 1) {
    if (view[0] === "dashboard") return [];
    if (view[0] === "research") return ["jobs"];
    if (view[0] === "system") return ["health"];
    if (view[0] === "plans") return ["billing"];
  }
  return [...view];
}
