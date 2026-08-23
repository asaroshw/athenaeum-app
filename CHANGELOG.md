# Athenaeum — Changelog

Covers three phases of work: (1) verified bug fixes from the original code
review, (2) WACC/ROIC and a point-in-time snapshot foundation, (3) the IPO
pipeline dedup/scraper/date-bucketing work. 176 tests pass (110 pre-existing
+ 66 new). Every item below was verified — either empirically against the
real scraped sites, by tracing the exact code path, or both — not just
patched and assumed correct.

## Reliability

- **Equity AI-report crash cascade fixed.** `generate_comprehensive_report`
  now has the same try/except + algorithmic-fallback the IPO narrative path
  already had. Previously, any Gemini failure (rate limit, timeout, a
  content-safety block) discarded the entire successfully-computed
  quantitative analysis, not just the AI narrative.
- **Gemini model centralized** into `config.GEMINI_MODEL = "gemini-3.5-flash-lite"`.
  Previously two independently hardcoded strings; one (`gemini-2.0-flash`,
  in the news-sentiment path) was for a model Google has since retired.

## Valuation / scoring correctness

- **Entry-range inversion fixed and made testable.** Extracted into
  `compute_entry_stop_range()` in `helpers.py`; a realistic case (price well
  above its support/volume zone) used to produce `entry_low > entry_high` —
  a visibly inverted "Recommended Entry" range.
- **`composite_verdict` consolidated.** It was called, its verdict/composite
  outputs were fully discarded, and an inline duplicate (with a different
  ARIMA nudge magnitude) silently ran instead. Now a single implementation,
  actually exercised by what it returns.
- **`safe_pct_change()` added** in `helpers.py`, replacing three separate
  `X or 1` divisor patterns that silently substituted a fake ₹1 when a
  prior-period value was genuinely zero (turning a zero base into a
  nonsensical percentage instead of correctly returning "undefined").
- **`analysis_radar_chart` None-vs-missing-key bug fixed** — `.get(key, 50)`
  never applied its neutral default for a real data-sparse company (key
  present, value `None`), silently breaking the chart.

## New: WACC / ROIC (Phase 2)

- `compute_wacc()` and `compute_roic()` added to `valuation.py` — the
  codebase previously had no WACC anywhere. Cost of debt is derived from
  actual interest expense when available (flagged `kd_is_estimated: False`),
  not assumed; effective tax rate is read from the company's own income
  statement when available, statutory rate only as a labeled fallback.
  Wired into `pipeline.py` using the *existing* `ke_pct` rather than adding
  a fourth redundant CAPM calculation. Displayed in the UI with an explicit
  caption on why the DCF still discounts at Ke, not WACC (the current FCF
  proxy is levered/equity-side, so that pairing is still correct — WACC is
  shown as an independent benchmark and for the ROIC−WACC spread).

## New: point-in-time snapshot store (Phase 3 foundation)

- `athenaeum/data/snapshot_store.py` — a real, working SQLite-backed log of
  this app's own verdicts over time. Explicitly scoped in its own docstring:
  it is **not** a point-in-time database of the underlying FMP/yfinance
  fundamentals, and **not** a backtester — those remain separate, larger
  undertakings. It's the honest first step: every analysis now gets logged
  with a timestamp, and a small "Verdict history" panel surfaces it.

## IPO pipeline: dedup, merge, and date bucketing

Built in the requested order — merge engine first, then the new source, then
date hardening — specifically so ipopremium.in enriches existing merged
records instead of tripling duplicates.

- **Root cause of the "Lumino Inds" vs "Lumino Industries" duplicate**: the
  existing normalizer stripped the full word "Industries" but not the
  abbreviation "Inds", so the same company produced two different dedup
  keys. Fixed by expanding the abbreviation list (Inds/Ind, Engrs/Engineers,
  Corpn, Intl, Mfg, etc.).
- **Real difflib fuzzy-match layer added** (`_fuzzy_key_match`) as a second
  pass for whatever the rule list doesn't cover — gated on matching leading
  word + ≥90% similarity, so it doesn't just catch near-misses on typos and
  new sources' naming conventions.
- **A false-positive risk was found and fixed while calibrating the fix**:
  the original ≥4-char guard let "Apex Industries" and "Apex Pharma" both
  collapse to the same generic "apex" key — different companies would have
  incorrectly merged. Raised to ≥5 and verified against every existing test
  plus this new case before shipping it.
- `_merge_ipo_records` and `_deduplicate_list` now share one merge
  implementation (`_merge_ipo_field_values`) instead of two that could (and
  had) drifted apart, and both track a `sources` provenance list.
- **`_parse_date_flex` hardened**: strips a leading day-of-week
  ("Tue, Aug 11, 2026" — Chittorgarh's dominant format on its detail pages),
  handles a combined date range in one string ("11 to 13 Aug, 2026"),
  handles `\xa0` non-breaking spaces, and centralizes ordinal-suffix
  stripping that previously only existed as a local workaround inside the
  Screener.in scraper. All verified against the real, live-fetched HTML
  from Chittorgarh and ipomarket.in, not assumed.
- **Investigated the actual "Milky Mist stuck in Upcoming" mechanism**
  end-to-end against real data (Chittorgarh's detail page, ipomarket.in's
  `/ipo/listed` table) rather than guessing: `_bucket_ipo`'s own
  classification logic was already correct for a past listing date — the
  real mechanism was a stale, unmerged record from one source sitting
  alongside a correctly-closed record from another, which the merge-engine
  fix above resolves. Reproduced as an explicit regression test.
- **ipopremium.in scraper added** (`_scrape_ipopremium_list`), scoped to a
  single cached homepage fetch, matching "fast scraper... to cross-reference
  GMPs and dates." Built against the real live page structure: skips an
  empty JavaScript-populated table by header signature, strips a
  parenthetical exchange/board suffix ipopremium glues onto company names
  ("Lumino Industries Ltd (MAINBOARD)") both at the source and as
  defense-in-depth in the normalizer, and treats a "0–0" placeholder price
  band as not-yet-set rather than a real ₹0 band.
- **`robots.txt` is now checked programmatically at runtime**
  (`_robots_allow`, stdlib `urllib.robotparser`) for every scraped request
  across all four sources, not just the new one. This was added because
  ipopremium.in's robots.txt could not be manually verified during this
  session (the fetch tooling available couldn't retrieve it directly) —
  rather than ship on an unverified assumption, the check is now automatic
  and permanent. Fails open (allows the fetch) only if robots.txt itself is
  unreachable; an explicit `Disallow` is always honored.

## Cleanup

- Removed dead code: `_classify_bucket` (imported, never called, contained
  a real bug), unused `fastapi`/`uvicorn` in `requirements.txt`.
- Fixed one unescaped field (`industry`) in an `unsafe_allow_html` block;
  audited all ~30 such call sites — the rest were already properly escaped.

## UI

- Refined color tokens in `config.py` (page/card background previously had
  almost no contrast — both near-pure-black); pill-shaped verdict/status
  badges (`verdict_pill`, `status_pill`); tabular figures for financial
  numbers; hidden Streamlit chrome; restyled native buttons/tabs/inputs.
  Consolidated ~20 scattered hardcoded near-white hex values into two named
  tokens (`TEXT`, `TEXT_BODY`).

## What's explicitly still open (not started this session)

- The `equity.py`/`ipo.py` "god module" split — deliberately deferred; a
  blind refactor of a 750+2,200 line surface is how you introduce the next
  round of bugs. Worth a dedicated pass with agreed module boundaries.
- Full unlevered FCFF DCF (needs CapEx/D&A/ΔNWC extraction, not currently
  pulled as clean fields).
- Consensus estimates engine, segment/SOTP valuation, a real backtesting
  harness (the snapshot store is the foundation for this, not the harness
  itself), bank/insurance-specific ratios (GNPA/NNPA/CET1/VNB — genuinely
  not derivable from the generic data sources currently wired in).
