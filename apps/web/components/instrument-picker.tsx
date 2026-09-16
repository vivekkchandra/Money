"use client";

import { useEffect, useId, useReducer, type KeyboardEvent } from "react";
import { api } from "@/lib/client";
import { INITIAL_SEARCH, instrumentLabel, instrumentStatus, parseInstrumentSearch, searchReducer, type Instrument } from "@/lib/instruments";
import { Icon } from "./primitives";

export function InstrumentPicker({ selected, onSelect, disabled = false, allowUnavailable = false }: {
  selected: Instrument | null; onSelect: (instrument: Instrument | null) => void; disabled?: boolean; allowUnavailable?: boolean;
}) {
  const [state, dispatch] = useReducer(searchReducer, INITIAL_SEARCH);
  const { query, revision, phase } = state;
  const id = useId();
  const inputId = `${id}-company`, listId = `${id}-companies`, helpId = `${id}-help`, statusId = `${id}-status`;

  useEffect(() => {
    const ticker = new URLSearchParams(window.location.search).get("ticker");
    if (ticker && /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/.test(ticker)) {
      // A watchlist link pre-fills a lookup; it never manufactures a selection.
      const timer = setTimeout(() => dispatch({ type: "query", query: ticker }), 0);
      return () => clearTimeout(timer);
    }
  }, []);

  useEffect(() => {
    if (phase !== "loading") return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const parameters = new URLSearchParams({ query: query.trim(), limit: "10", offset: "0" });
        const response = await api<unknown>(`/api/research/instruments?${parameters}`, { signal: controller.signal });
        if (!controller.signal.aborted) dispatch({ type: "loaded", revision, result: parseInstrumentSearch(response) });
      } catch (reason) {
        if (!controller.signal.aborted) dispatch({ type: "failed", revision, error: reason instanceof Error ? reason.message : "Company search is unavailable. Please retry." });
      }
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, revision, phase]);

  function select(instrument: Instrument) {
    if (disabled || (!allowUnavailable && !instrument.research_allowed) || !state.results.includes(instrument)) return;
    dispatch({ type: "select", instrument, allowUnavailable });
    onSelect(instrument);
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); dispatch({ type: "move", direction: event.key === "ArrowDown" ? 1 : -1, allowUnavailable });
    } else if (event.key === "Escape") {
      event.preventDefault(); dispatch({ type: "close" });
    } else if (event.key === "Enter" && !selected) {
      event.preventDefault();
      if (state.open && state.active >= 0) select(state.results[state.active]);
    }
  }

  return <div className="instrument-picker">
    <label htmlFor={inputId}>Company or stock ticker</label>
    <div className="instrument-search-input"><Icon name="search" size={19} /><input
      id={inputId} role="combobox" aria-autocomplete="list" aria-expanded={state.open && state.phase === "ready"}
      aria-controls={listId} aria-activedescendant={state.open && state.active >= 0 ? `${listId}-${state.active}` : undefined}
      aria-describedby={`${helpId} ${statusId}`} autoComplete="off" spellCheck={false} maxLength={80}
      placeholder="Search a company name or ticker" value={state.query} disabled={disabled}
      onChange={event => { onSelect(null); dispatch({ type: "query", query: event.target.value }); }}
      onKeyDown={keyDown} onFocus={() => dispatch({ type: "open" })}
      onBlur={event => { if (!event.currentTarget.parentElement?.parentElement?.contains(event.relatedTarget)) dispatch({ type: "close" }); }}
    /></div>
    <p id={helpId} className="search-help">Choose a listed match. A listing is not a research recommendation.</p>
    {state.mode === "live_rnd" && <p className="search-help"><strong>R&D / PERSONAL USE</strong> · Public-source search; not a verified ISA universe. Data may be delayed and is not commercially licensed.</p>}
    <div id={statusId} role="status" aria-live="polite" className="search-status">
      {state.phase === "loading" && "Searching the configured company source…"}
      {state.phase === "idle" && "Start typing a company name or ticker."}
      {state.phase === "ready" && (state.results.length ? `${state.total} match${state.total === 1 ? "" : "es"}. Use the arrow keys to choose ${allowUnavailable ? "a" : "an available"} result, then press Enter.` : "No matches from the configured source. Try another company name or ticker.")}
      {selected && `Selected ${instrumentLabel(selected)}. ${instrumentStatus(selected)}.`}
    </div>
    {state.phase === "error" && <div className="search-error" role="alert"><p>{state.error}</p><button type="button" className="text-button" onClick={() => dispatch({ type: "retry" })} disabled={disabled}>Retry company search</button></div>}
    {state.open && state.phase === "ready" && state.results.length > 0 && <ul id={listId} className="instrument-results" role="listbox" aria-label="Matching companies">
      {state.results.map((instrument, index) => <li key={instrument.instrument_id} id={`${listId}-${index}`} role="option"
        aria-selected={state.active === index} aria-disabled={!allowUnavailable && !instrument.research_allowed} className={state.active === index ? "active" : ""}
        onMouseDown={event => event.preventDefault()} onClick={() => select(instrument)}>
        <strong>{instrument.company}</strong><span className="instrument-symbol">{instrument.ticker} · {instrument.exchange ?? "Exchange not supplied"} · {instrument.currency}</span>
        <span className="instrument-eligibility">{instrumentStatus(instrument)}</span>
      </li>)}
    </ul>}
    {state.phase === "ready" && state.total > state.results.length && <p className="search-help">Showing the first {state.results.length} matches. Refine your search to narrow the list.</p>}
    {selected && <div className={`instrument-selected ${selected.synthetic ? "synthetic" : ""}`}><strong>{selected.ticker} · {selected.currency}</strong><span>{instrumentStatus(selected)}</span></div>}
  </div>;
}
