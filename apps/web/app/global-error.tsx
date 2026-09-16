"use client";
import Link from "next/link";
export default function GlobalError(
  {
    reset
  }: {
    reset: () => void;
  }
) {
  // Standalone semantic document: no inline styles/scripts that would weaken CSP.
  return <html lang="en-GB"><body><main role="alert"><h1>Money is temporarily unavailable.</h1><p>We could not open this page. Your saved research has not been removed.</p><button onClick={reset}>Try again</button><p><Link href="/">Return to Money</Link></p></main></body></html>;
}
