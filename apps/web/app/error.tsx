"use client";
import Link from "next/link";
import { useEffect } from "react";

export default function ErrorPage(
  {
    error,
    reset
  }: {
    error: Error & {
      digest?: string;
    };
    reset: () => void;
  }
) {
  useEffect(() => {
    // Deliberately omit error messages, stacks, URL/query strings and research content.
    void fetch("/api/product/events", {
      method: "POST",

      headers: {
        "Content-Type": "application/json"
      },

      body: JSON.stringify({
        event: "frontend_error"
      }),

      signal: AbortSignal.timeout(5000),
      credentials: "same-origin"
    }).catch(() => undefined);
  }, [error]);

  const reference = error.digest && /^[a-zA-Z0-9_-]{1,80}$/.test(error.digest) ? error.digest : null;
  return <main className="marketing-main editorial" role="alert"><p className="eyebrow">MONEY · SOMETHING WENT WRONG</p><h1>Let’s get you back to your research.</h1><p className="marketing-intro">This page could not be displayed. Your saved research remains in your workspace. Try again, or return to your dashboard.</p>{reference && <p>Reference: <code>{reference}</code></p>}<div className="marketing-actions"><button className="button" onClick={reset}>Try again</button><Link className="button secondary" href="/dashboard">Open dashboard</Link></div></main>;
}
