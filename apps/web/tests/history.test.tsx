import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResearchHistory } from "@/components/customer-tools";
import { historyFiltersReducer, historyQuery, INITIAL_HISTORY_FILTERS } from "@/lib/history";

describe("customer history filters", () => {
  it("renders labelled UTC date bounds and chronological sorting", () => {
    const html = renderToStaticMarkup(<ResearchHistory />);
    expect(html).toContain('for="history-from"');
    expect(html).toContain('for="history-to"');
    expect(html).toContain('for="history-sort"');
    expect(html).toContain('aria-describedby="history-date-help"');
    expect(html).toContain("Newest first");
    expect(html).toContain("Oldest first");
    expect(html).toContain("entire selected UTC calendar days");
  });

  it.each([
    { query: "ABC.L" }, { state: "COMPLETE" }, { created_from: "2026-09-01" },
    { created_to: "2026-09-16" }, { sort: "oldest" as const },
  ])("resets pagination on filter change %j", changes => {
    const result = historyFiltersReducer({ ...INITIAL_HISTORY_FILTERS, offset: 75 }, { type: "filter", changes });
    expect(result.offset).toBe(0);
    expect(result).toMatchObject(changes);
  });

  it("retains filters when paging and never selects a negative offset", () => {
    const initial = { ...INITIAL_HISTORY_FILTERS, created_from: "2026-09-01", sort: "oldest" as const };
    expect(historyFiltersReducer(initial, { type: "page", offset: 25 })).toEqual({ ...initial, offset: 25 });
    expect(historyFiltersReducer(initial, { type: "page", offset: -25 }).offset).toBe(0);
  });

  it("omits unset dates and passes exact ISO dates without timezone conversion", () => {
    expect(historyQuery(INITIAL_HISTORY_FILTERS).has("created_from")).toBe(false);
    expect(historyQuery(INITIAL_HISTORY_FILTERS).has("created_to")).toBe(false);
    const query = historyQuery({ ...INITIAL_HISTORY_FILTERS, created_from: "2026-09-01", created_to: "2026-09-16", sort: "oldest", offset: 25 });
    expect(Object.fromEntries(query)).toEqual({ query: "", state: "", created_from: "2026-09-01", created_to: "2026-09-16", sort: "oldest", offset: "25", limit: "25" });
  });
});
