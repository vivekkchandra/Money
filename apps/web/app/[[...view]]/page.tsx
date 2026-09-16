import { Workspace } from "@/components/workspace";
import { connection } from "next/server";
import { MARKETING_PAGES, MarketingPage } from "@/components/marketing";
import { AccountForm, AccountWorkspace } from "@/components/account";
import type { Metadata } from "next";

export async function generateMetadata({ params }: { params: Promise<{ view?: string[] }> }): Promise<Metadata> {
  const { view = [] } = await params;
  const publicPage = view.length <= 1 && MARKETING_PAGES.has(view[0] ?? "");
  const indexable = publicPage && process.env.MONEY_ENV === "production" && process.env.MONEY_RESEARCH_MODE !== "live_rnd";
  return { robots: { index: indexable, follow: indexable } };
}

export default async function Page({ params }: { params: Promise<{ view?: string[] }> }) {
  await connection();
  const { view = [] } = await params;
  const page = view[0] ?? "";
  if (view.length <= 1 && MARKETING_PAGES.has(page)) return <MarketingPage page={page} />;
  const saas = process.env.MONEY_AUTH_MODE === "saas";
  if (view.length === 1 && ["login", "signup", "verify-email", "resend-verification", "forgot-password", "reset-password", "accept-invite"].includes(page)) return <AccountForm page={page} enabled={saas} />;
  if (view.length === 1 && ["onboarding", "account"].includes(page)) return <AccountWorkspace page={page as "onboarding" | "account"} />;
  return <Workspace view={view} commercial={saas} personalRnd={process.env.MONEY_RESEARCH_MODE === "live_rnd"} />;
}
