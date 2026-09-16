import type { Metadata } from "next";
import "./globals.css";
import { PersonalRndNotice } from "@/components/personal-rnd";

export const metadata: Metadata = {
  title: "Money · Independent investment research",
  description: "Independent research firms. Shared evidence. Decisions that stay yours.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-GB"><body>{process.env.MONEY_RESEARCH_MODE === "live_rnd" && <PersonalRndNotice className="deployment-notice" />}{children}</body></html>;
}
