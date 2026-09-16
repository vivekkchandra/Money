import Link from "next/link";

export default function NotFound() {
  return <main className="standalone"><p className="eyebrow">MONEY / 404</p><h1>This page is outside the research universe.</h1><Link className="button" href="/">Back to dashboard</Link></main>;
}
