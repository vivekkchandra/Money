export type HistoryFilters = {
  query: string;
  state: string;
  created_from: string;
  created_to: string;
  sort: "newest" | "oldest";
  offset: number;
};

export const INITIAL_HISTORY_FILTERS: HistoryFilters = {
  query: "", state: "", created_from: "", created_to: "", sort: "newest", offset: 0,
};

export function historyFiltersReducer(
  previous: HistoryFilters,
  action: { type: "filter"; changes: Partial<Omit<HistoryFilters, "offset">> } | { type: "page"; offset: number },
): HistoryFilters {
  return action.type === "filter"
    ? { ...previous, ...action.changes, offset: 0 }
    : { ...previous, offset: Math.max(0, action.offset) };
}

export function historyQuery(filters: HistoryFilters): URLSearchParams {
  return new URLSearchParams({
    query: filters.query,
    state: filters.state,
    sort: filters.sort,
    offset: String(filters.offset),
    limit: "25",
    ...(filters.created_from ? { created_from: filters.created_from } : {}),
    ...(filters.created_to ? { created_to: filters.created_to } : {}),
  });
}
