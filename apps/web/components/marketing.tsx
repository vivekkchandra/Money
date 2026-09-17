import Link from "next/link";

export const MARKETING_PAGES = new Set([
  "",
  "product",
  "how-it-works",
  "pricing",
  "security",
  "about",
  "terms",
  "privacy",
  "risk-disclosure",
  "acceptable-use",
  "cookies",
  "status"
]);

const pages: Record<string, {
  eyebrow: string;
  title: string;
  intro: string;
  sections: [string, string][];
}> = {
  product: {
    eyebrow: "YOUR RESEARCH, WITH PERSPECTIVE",
    title: "One question. Independent perspectives.",
    intro: "Money brings research, validation and challenge into one transparent workspace. Inspect the reasoning, follow the sources and make your own decision.",

    sections: [[
      "Research you can inspect",
      "Separate reports preserve each firm's perspective. Conclusions link to the evidence available at the time of research."
    ], [
      "A record that lasts",
      "Research runs independently of your browser. Return to the same investigation, its evidence and its final research state later."
    ], [
      "Clarity about uncertainty",
      "Missing evidence, expired research and rejected candidates remain visible. A completed investigation is not an endorsement."
    ]]
  },

  "how-it-works": {
    eyebrow: "THE RESEARCH PROCESS",
    title: "Shared facts. Independent thinking.",
    intro: "Agreement is not enough. Money is designed to challenge a thesis before presenting a research conclusion.",

    sections: [[
      "01 · Prepare the evidence",
      "Eligibility, business activities, prices and sources are checked and captured in a point-in-time snapshot."
    ], [
      "02 · Investigate independently",
      "TradingAgents and ai-hedge-fund receive the same objective evidence, without access to one another's first-pass conclusions. Optional Qlib adds independent quantitative research only when enabled and qualified."
    ], [
      "03 · Lock, validate and challenge",
      "The configured original reports are sealed. Mandatory LEAN validation tests historical behaviour. CrewAI audits evidence, examines contradictions and applies adversarial review."
    ], [
      "04 · Review the result",
      "Explore the independent reports, validation, bounded cross-examination and sources. The investment decision always remains yours."
    ]]
  },

  security: {
    eyebrow: "SECURITY & TRUST",
    title: "Clear boundaries. Visible evidence.",
    intro: "Your workspace is private. Research is auditable. Money does not hold brokerage credentials or execute trades.",

    sections: [[
      "Workspace access",
      "Server-side membership checks scope access to research records. Account sessions use HttpOnly cookies and can be revoked."
    ], [
      "Evidence integrity",
      "Sealed reports and decision packets preserve the original research. Later challenges do not silently rewrite earlier conclusions."
    ], [
      "Honest qualification",
      "Live research stays unavailable when required data, runtime, model or licensing checks have not passed. Synthetic demonstrations are labelled."
    ], [
      "Responsible reporting",
      "Never send credentials or private research in a public issue. A reviewed vulnerability-reporting contact and commercial security policy must be published before launch."
    ]]
  },

  about: {
    eyebrow: "INDEPENDENT BY DESIGN",
    title: "Better questions before stronger conviction.",
    intro: "Money is investment-research software for people who want to understand the evidence behind an idea, not just receive an opaque answer.",

    sections: [[
      "Research, not execution",
      "No orders, brokerage account control or automatic rebalancing. Money's role ends at research."
    ], [
      "A focused initial mandate",
      "Individual GBP/GBX stocks, an eligibility constraint based on the Trading 212 Stocks & Shares ISA universe, and deterministic exclusions for defence, weapons and oil activities."
    ], [
      "No promised performance",
      "Models and research can be wrong. Historical validation and independent challenge are tools for investigation, not guarantees of future returns."
    ]]
  },

  terms: {
    eyebrow: "LEGAL · REVIEW REQUIRED",
    title: "Terms of service",
    intro: "Pre-launch template. These terms require review by qualified counsel and publication of the contracting entity, jurisdiction, contact details and effective date before paid service launches.",

    sections: [[
      "Service scope",
      "Money provides informational investment research, not personalised financial advice, brokerage services or trade execution. Customers make their own investment decisions."
    ], [
      "Accounts and subscriptions",
      "Keep account access secure. Final pricing, billing periods, cancellation, refund terms and consumer rights must be disclosed before payment. Access remains subject to the purchased plan and applicable law."
    ], [
      "Data and acceptable use",
      "Use the service lawfully. Do not circumvent access limits, share restricted source material or attempt to access another customer's workspace. Third-party data rights remain with their owners."
    ], [
      "Service limitations",
      "Research may be delayed, incomplete, incorrect or unavailable. Liability, dispute-resolution and termination provisions require jurisdiction-specific legal approval before launch."
    ]]
  },

  privacy: {
    eyebrow: "LEGAL · REVIEW REQUIRED",
    title: "Privacy policy",
    intro: "Pre-launch template. The data controller, privacy contact, lawful bases, subprocessors, retention periods and international-transfer arrangements must be approved and published before commercial launch.",

    sections: [[
      "Information used",
      "Account identifiers, workspace membership, research requests, usage and necessary billing references support the service. Money does not request brokerage balances or positions."
    ], [
      "Purpose and security",
      "Data is used to authenticate customers, provide research, administer subscriptions, investigate failures and protect the service. Passwords and session tokens must not appear in logs or analytics."
    ], [
      "Your controls",
      "Account export and deletion-request workflows are available in account settings. Billing, fraud-prevention and audit retention may limit immediate deletion; the final retention schedule requires legal review."
    ], [
      "Minimal analytics",
      "Product events should record operational identifiers and event types, not full research contents. Non-essential tracking must not be enabled without the required disclosures and consent."
    ]]
  },

  "risk-disclosure": {
    eyebrow: "RESEARCH DISCLAIMER",
    title: "Evidence informs. It does not guarantee.",
    intro: "Money provides informational research. It is not personalised investment advice, a recommendation to transact or a promise of profit.",

    sections: [[
      "Investment risk",
      "Investments can fall in value. You may lose capital. Assess your own circumstances and seek qualified advice when appropriate."
    ], [
      "Model and data limitations",
      "Models may be inaccurate, sources may be incomplete and historical patterns may not persist. Point-in-time checks and validation cannot eliminate uncertainty."
    ], [
      "Simulation is not live performance",
      "DEMO.L and synthetic examples illustrate software behaviour, not investable opportunities or real firm performance. Historical or simulated performance does not guarantee future results."
    ], [
      "Your independent decision",
      "Potential entry ranges, targets and illustrative allocations are research assumptions only. Money never submits orders or acts on your behalf."
    ]]
  },

  "acceptable-use": {
    eyebrow: "LEGAL · REVIEW REQUIRED",
    title: "Acceptable use",
    intro: "This pre-launch policy requires legal approval before commercial launch.",

    sections: [[
      "Use the platform responsibly",
      "Do not attempt unauthorised access, introduce malicious content, evade quotas, probe private services or interfere with another customer's work."
    ], [
      "Respect source rights",
      "Do not redistribute restricted provider data or use research in ways that violate applicable licences. Access to a public source does not imply redistribution rights."
    ], [
      "Protect other people",
      "Do not upload unnecessary personal data, impersonate others or use the service to facilitate unlawful conduct."
    ]]
  },

  cookies: {
    eyebrow: "PRIVACY",
    title: "Essential cookies only by default.",
    intro: "Money uses secure session and workspace-selection cookies to keep you signed in and scope your application experience.",

    sections: [[
      "Session cookies",
      "HttpOnly cookies are not exposed to browser JavaScript. Session expiry and revocation are enforced by the account service."
    ], [
      "Workspace selection",
      "The selected workspace cookie is a convenience, not an access grant. The backend checks membership on each protected request."
    ], [
      "Optional analytics",
      "No advertising or third-party tracking is required to use the core product. Any future non-essential tracking requires an updated notice and appropriate consent."
    ]]
  },

  status: {
    eyebrow: "SERVICE STATUS",
    title: "Know what is available.",
    intro: "Availability and live-research qualification are different. Sign in to see the status relevant to your workspace.",

    sections: [[
      "Web application",
      "This page is served by the Money web application. It does not establish that research compute or third-party providers are available."
    ], [
      "Research availability",
      "The application reports unavailable research services explicitly and retains durable research records. Money never silently substitutes synthetic data for failed live research."
    ]]
  }
};

export function MarketingShell(
  {
    children
  }: {
    children: React.ReactNode;
  }
) {
  return (
    <div className="marketing-shell"><a className="skip-link" href="#main">Skip to content</a><header className="marketing-header"><Link className="brand" href="/" aria-label="Money home">money<span className="brand-period">.</span></Link><nav aria-label="Product navigation"><Link href="/product">Product</Link><Link href="/how-it-works">How it works</Link><Link href="/pricing">Plans</Link><Link href="/security">Security</Link></nav><Link className="button secondary" href="/login">Sign in</Link></header>{children}<footer className="marketing-footer"><div><Link className="brand" href="/">money.</Link><p>Independent investment research.<br />Your decisions. Always.</p></div><nav aria-label="Footer navigation">{[
            ["about", "About"],
            ["terms", "Terms"],
            ["privacy", "Privacy"],
            ["cookies", "Cookies"],
            ["risk-disclosure", "Risk disclosure"],
            ["acceptable-use", "Acceptable use"],
            ["status", "Status"]
          ].map(([path, label]) => <Link key={path} href={`/${path}`}>{label}</Link>)}</nav><p className="small-print">Research is informational, not personalised financial advice. Returns are not guaranteed. Live research and paid access require qualified providers and an enabled commercial service.</p></footer></div>
  );
}

export function MarketingPage(
  {
    page
  }: {
    page: string;
  }
) {
  if (page === "pricing") return (
    <MarketingShell><main id="main" className="marketing-main" data-money-page="pricing"><p className="eyebrow">PLANS THAT FIT YOUR RESEARCH</p><h1>Start with understanding.<br />Grow with your questions.</h1><p className="marketing-intro">Plan limits and billing availability are provided by your workspace. Prices are shown in secure checkout only when subscriptions are enabled. No payment is taken on this page.</p><div className="marketing-cards plans">{[[
            "FREE",
            "Explore the process",
            "A bounded allowance for exploring the research workflow. Demonstrations remain clearly synthetic."
          ], [
            "PRO",
            "Go deeper",
            "More research capacity, evidence exploration and a durable personal research history."
          ], ["TEAM", "Work together", "Shared workspaces, role-based access and organisation-level allowances."], [
            "ENTERPRISE",
            "Specific requirements",
            "Dedicated limits and audit requirements subject to a reviewed commercial agreement."
          ]].map(
            ([name, title, detail]) => <section className="panel" key={name}><p className="eyebrow">{name}</p><h2>{title}</h2><p>{detail}</p><Link className="button secondary" href="/signup">Create an account</Link><small>Availability and limits shown in your account</small></section>
          )}</div><p className="notice">Paid plans are offered only after billing, data rights and live-service qualification are complete. A plan never guarantees an investable result.</p></main></MarketingShell>
  );

  if (page) {
    const content = pages[page];

    if (!content)
      return null;

    return <MarketingShell><main id="main" className="marketing-main editorial" data-money-page={page}><p className="eyebrow">{content.eyebrow}</p><h1>{content.title}</h1><p className="marketing-intro">{content.intro}</p><div className="editorial-sections">{content.sections.map(([title, text]) => <section key={title}><h2>{title}</h2><p>{text}</p></section>)}</div><Link className="button" href="/signup">Explore Money</Link></main></MarketingShell>;
  }

  return (
    <MarketingShell><main id="main" className="marketing-main" data-money-page="marketing"><section className="marketing-hero"><div><p className="eyebrow">INDEPENDENT INVESTMENT RESEARCH</p><h1>See the evidence.<br />Question the consensus.</h1><p className="marketing-intro">Independent research firms. Historical validation. Adversarial challenge. A clear record behind every conclusion — and room for your own judgement.</p><div className="marketing-actions"><Link className="button" href="/signup">Create your workspace <span aria-hidden="true">↗</span></Link><Link className="text-button" href="/how-it-works">See how Money thinks →</Link></div><p className="small-print">Research software. No trade execution. No promised returns.</p></div><aside className="research-preview" aria-label="Illustration of the research process"><p className="eyebrow">THE MONEY METHOD · ILLUSTRATION</p><h2>One evidence snapshot.<br />Independent perspectives.</h2><div className="preview-firms"><span>TradingAgents<small>RESEARCH FIRM A</small></span><span>ai-hedge-fund<small>RESEARCH FIRM B</small></span><span>Qlib<small>OPTIONAL QUANT</small></span></div><div className="preview-stage"><span>01</span>Seal independent reports</div><div className="preview-stage"><span>02</span>Mandatory LEAN validation</div><div className="preview-stage"><span>03</span>Challenge with the CIO & Red Team</div><p>Shared facts. Not shared opinions.</p></aside></section><section className="marketing-section"><p className="eyebrow">CONVICTION BEGINS WITH BETTER QUESTIONS</p><h2>More transparent research.<br />Less black-box certainty.</h2><div className="marketing-cards">{[[
              "Independent by design",
              "Firms investigate the same evidence without seeing each other's first-pass conclusions."
            ], [
              "Open to challenge",
              "Validation and an adversarial review can reject a thesis, even when the firms agree."
            ], [
              "Yours to investigate",
              "Inspect reports, timestamps, source provenance and uncertainty in one persistent record."
            ]].map(
              ([title, text], index) => <section className="panel" key={title}><span className="feature-index">0{index + 1}</span><h3>{title}</h3><p>{text}</p></section>
            )}</div></section><section className="marketing-callout"><div><p className="eyebrow">YOUR DECISIONS. ALWAYS.</p><h2>Understand the case.<br />And the case against it.</h2><p>Money helps you research UK equities within clear eligibility and ethical boundaries. It never places orders or manages your portfolio.</p></div><Link className="button light" href="/product">Explore the product →</Link></section></main></MarketingShell>
  );
}
