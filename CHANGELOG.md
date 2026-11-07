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

---

# Sprint 2 — Live bugs, remaining technical debt, and UI-adjacent fixes

Second sprint, covering: two live bugs reported after Sprint 1 shipped, the
three remaining Part 2 technical-debt items, three follow-up bug reports,
a GTT/swing-trading verdict decoupling, and an IPO risk-list data bug.
240 tests pass (223 pre-existing + 17 new this sprint on top of Sprint 1's
own additions). Every item verified — either empirically, by tracing the
exact code path, or against real fetched pages — not just patched.

## Live bugs (Part 1 of this sprint)

- **IPO dedup: mid-word truncated names** ("Augmont Enterpri" vs "Augmont
  Enterprises"). Investigated exhaustively against real fetched pages from
  all three sources — none showed a genuine truncation at that character
  count, so the exact origin couldn't be pinned down. Fixed regardless with
  general prefix/stem matching (`_is_truncation_prefix_match`), deliberately
  built on RAW (pre-suffix-stripped) names rather than normalized keys — a
  real false positive was found and fixed during calibration ("Reliance
  Industries" vs "Reliance Retail" coincidentally shared a stripped-key
  prefix even though the raw names never did).
- **Sector Alternative visibility**: previously required a literal "STRONG
  BUY" peer and showed nothing if none qualified. Now suggests the
  highest-scoring peer above a 60-point bar (any tier, not just STRONG
  BUY), or an explicit "Scanned N sector peers — none currently meet our
  BUY threshold" message. Selection logic extracted into a pure,
  independently-tested function (`select_best_sector_alternative`).

## Remaining Part 2 technical debt

- **Data-completeness/confidence scoring** was dead code — `n_possible`
  always equaled `n_available` because check functions silently omitted a
  check rather than recording "checked, but data unavailable." All four
  check functions in `fundamentals.py` now explicitly return `None` (not
  silently omitted, not a false `False`) for structurally-applicable but
  missing data, so completeness genuinely varies now (verified: a 3-check
  set with 2 missing now correctly scores 80, not 100).
- **The exact confirmed root cause of this**: `dividend_checks`'s
  `dy is not None and dy > 1.5` coerced missing data to `False` via
  short-circuit — indistinguishable from a real "doesn't pay" fail. Fixed
  as part of the same pass. (Traced Taparia Tools' real dividend profile
  during investigation — it actually pays 300–600%+ on an extremely
  illiquid, decoupled ₹12 share price — but `dividend_checks` doesn't
  numerically drive `fundamental_score` at all, so this fix is correct and
  real but likely isn't what caused that specific stock's OBSERVE verdict.)
- **`render_checks` didn't handle the new `None` status** — found while
  verifying the above: a genuinely-missing-data check rendered as an
  identical hard red FAIL to a real negative, silently undoing the point of
  returning `None` instead of `False`. Now uses the three states correctly
  (pass/fail/N-A, reusing the already-defined-but-unused `swf-check-na`
  style).
- **JPB/DDM redundant, uncached Ke + inconsistent growth-clamping**:
  `equity.py` used to precompute Justified P/B and DDM with a separately
  -fetched RFR and an UNCLAMPED growth rate, while every other model in the
  same blend used the pipeline's own clamped growth. Removed entirely —
  `compute_multi_model_values` already self-computes JPB/DDM correctly when
  not handed a precomputed override (proven by the fact the Bear/Bull
  scenario calls already worked this way); the base case now matches. The
  ratio is exposed in the return dict and read back after the pipeline
  runs, for display, instead of being computed a second time beforehand.
- **WACC wired into an actual DCF.** WACC was computed and displayed
  (Sprint 1) but fed nothing. Rather than swap WACC into the existing
  "Simplified FCF DCF" (which would have paired an unlevered rate with that
  model's levered FCF proxy — a mismatch, not a fix), built a genuinely
  separate `unlevered_fcff_dcf_fair_value()`: FCFF = EBIT×(1−tax)+D&A−Capex
  −ΔNWC, discounted at WACC over an explicit 5-year fade to a Gordon-growth
  terminal value, net debt subtracted for equity value. Added D&A/CapEx/
  ΔNWC extraction from the cash-flow statement. Wired into
  `compute_multi_model_values` as a new model — equal-weighted with the
  existing FCF DCF for capex-intensive/materials/cyclical sectors (where
  leverage and capital intensity matter most), lighter-weighted for
  standard, excluded for financials (same rationale as the existing
  EV/EBITDA exclusion). Verified end-to-end: the new model measurably
  changes the blended target price for a capex-intensive company when WACC
  data is available, and is a no-op (old behavior exactly preserved) when
  it isn't.
- **Inconsistent metrics-dict typing** — explicitly scoped down rather than
  attempted as a full sweep (the honest call made in Sprint 1 still holds;
  a full pass touches dozens of fields across the whole app). Fixed the
  clearest, most-argued-about instance as a concrete, fully-verified
  example: `dividend_yield` was a pre-formatted string (`"2.34%"` or the
  literal `"N/A"`) instead of a float — traced its only two real consumers
  first, confirmed no blast radius surprises, converted to a raw float
  (or `None`), and updated `dividend_checks` accordingly while preserving
  the existing "explicit non-payer" test case exactly. A full sweep across
  the rest of the metrics dict remains open.

## Three follow-up bug reports

- **AI Markdown not rendering** (raw `* **bold**` asterisks visible in AI
  notes). Root cause: Streamlit's markdown parser doesn't re-parse markdown
  syntax found inside HTML you've written yourself once
  `unsafe_allow_html=True` is active — every `card()` call wrapping AI text
  in a hand-written `<p>`/`<div>` hit this. Added `render_ai_markdown()`
  (bold, italic, bullet markers → real HTML, escaped first, verdict
  -highlighting applied after) and wired it into all 9 call sites across
  the equity and IPO narrative sections. Found and fixed a real ordering
  bug during testing: a bullet line that also contains genuine italic text
  on the same line garbled without bullet-conversion running before italic
  conversion.
- **"DON'T BUY" rendering "BUY" in green.** Root cause: `html_escape` turns
  the apostrophe into the 6-character entity `&#x27;`, and the whole-phrase
  regex's `.?` (0-or-1 characters) couldn't span it — the whole-phrase match
  silently failed and the standalone `\bBUY\b` alternative matched instead.
  Fixed by explicitly enumerating the realistic apostrophe forms in the
  regex instead of a single wildcard character.
- **IPO risk list showing generic boilerplate** instead of IPO-specific
  risks (a UI tooltip, a disclaimer, and a section heading were all showing
  up as "risks" with a ⚠️ prefix). Root cause: the extraction scanned the
  ENTIRE scraped page's text for a dozen generic keywords ("risk",
  "regulatory", etc.) with no restriction to an actual Risk Factors section
  — a disclaimer mentioning "subject to market risks" matched on "risk" the
  same as a genuine risk bullet would. Fixed by scoping extraction to the
  actual Strengths/Risk(s) section first (the same marker-based technique
  already used for the "about" section), plus an independent boilerplate
  -phrase filter as a second layer. Verified against the exact garbage
  lines from the report.

## GTT / swing-trading verdict decoupling

`apply_tiered_sanity_veto` used to force a downgrade whenever the live
price exceeded the modeled target — more than 15% above hard-forced
OBSERVE/DON'T BUY regardless of fundamental strength, and any amount above
target downgraded BUY/STRONG BUY straight to OBSERVE. That conflated "is
this a fundamentally good company" with "is right now a good moment to
enter" — the latter question the Entry/Target/Stop-Loss levels already
answer. Removed the price-vs-target demotion entirely; kept the
implausible-upside data-anomaly ceiling (a check on the model's output
quality, unrelated to price direction). Verified: a STRONG BUY now survives
price 60% above its modeled target with fundamentals untouched, while an
implausible 300%-upside-on-low-growth case is still correctly flagged.


## Part 3 — "Simply Wall St" UI transformation

Full UI rework based on the reference video: deep charcoal background
(instead of near-black), gold as the single accent color, serif section
headings over sans-serif body/data, a left-hand sidebar for company
header + navigation, and the Snowflake/checklist/fair-value/ownership
visuals that report style is built around — not just the color palette,
per the explicit instruction to also bring over "the graphs, tables and
other pieces of information."

- **Palette** (`config.py`): `BG`/`CARD_BG`/`BORDER` warmed from a cool
  near-black to a warm charcoal; `ACCENT` moved from orange-red (`#FF6B35`)
  to gold (`#D9A521`); added `PURPLE_SOFT`. Every existing call site picks
  this up automatically since the token *names* didn't change.
- **New Future Growth checklist** (`future_growth_checks` in
  `fundamentals.py`) — previously the only one of the 5 narrative sections
  with no accompanying pass/fail checklist card. Built entirely from data
  already computed elsewhere (analyst/trailing growth, revenue CAGR, the
  valuation model's own growth assumption, and the ROIC-WACC economic
  -profit spread) rather than fetching anything new, and follows the same
  tri-state True/False/None convention (`None` = data genuinely
  unavailable, not a silent fail) as the other four checklists.
- **Snowflake chart** (`snowflake_chart` in `ui/components.py`) — the
  5-axis Value/Future/Past/Health/Dividend radar that's the report's
  signature visual, colored on a red→gold→green gradient by its own
  average. A large version sits in the main body next to the existing
  Composite Score Radar (kept — it measures something different:
  fundamental/intrinsic/technical model sub-scores, not the checklist
  -based Snowflake axes); a small, label-free version sits in the new
  sidebar.
- **Narrative-style checklists** (`score_bar_header` + `narrative_checklist`)
  replace the old bare-table `render_checks` rendering for all five
  checklist cards (Valuation, Future Growth, Past Performance, Financial
  Health, Dividend) with a "Score X/Y" header and "✅ Bold label: detail"
  prose rows, matching the reference report instead of a plain pass/fail
  table. `render_checks` itself is untouched (still used by the IPO
  pages), just no longer called from the equity report.
- **Fair-value comparison bar** (`fair_value_bar_chart`) replaces the old
  plain two-bar chart with the reference report's horizontal
  current-price-vs-fair-value bars plus an inline "X% Over/Undervalued"
  verdict, colored green/gold/red by the gap.
- **Ownership stacked bar** (`ownership_stacked_bar`) adds a horizontal
  stacked-bar ownership breakdown alongside the existing donut (kept, not
  replaced — it was already correct and tested). Built generically over
  whatever buckets the shareholding dict actually has, rather than
  assuming a fixed Promoters/FII/DII/Public/Others shape this app's actual
  data source (yfinance insider/institutional holding percentages) doesn't
  provide — it only ever has Promoters/Institutions/Public.
- **Sidebar navigation**: company name, price, verdict pill, market cap,
  the small Snowflake, and a numbered jump-list (`<a href="#sec-N">`) to
  anchor `<div id="sec-N">` markers placed above each of the 8 report
  sections plus the overview header.
- **Serif/sans-serif pairing**: Lora (serif) for section titles and the
  Snowflake card heading, Inter (sans-serif, unchanged) for everything
  else including all numbers — deliberately not applied globally so
  tabular figures keep their tabular-nums alignment.

**Explicitly out of scope** (flagged rather than silently dropped, per
the "give me a real accounting" instruction from earlier in this sprint):
a Sankey revenue/expense flow diagram (needs segment-level revenue data
this app doesn't extract), CEO/management compensation analysis, and an
insider buy/sell transaction timeline — none of the underlying data is
available from this app's data sources (yfinance + the scraped IPO/news
sources), and fabricating placeholder figures for them would be worse
than not showing them.

### Bug found and fixed during this pass

- **`TEXT_BODY` undefined name in `streamlit_app.py`.** Pre-existing, not
  introduced by this sprint: `TEXT_BODY` was used in half a dozen
  narrative-card f-strings (Overview, Valuation, Future Growth, Past
  Performance, Financial Health, Dividend, Management, Ownership) but was
  never imported from `config` in this file — a `NameError` that would
  have crashed the app the instant any equity report actually rendered.
  It went undetected because no test previously exercised
  `streamlit_app.py`'s own script body (every other test calls
  `athenaeum/*` functions directly) — see the new end-to-end smoke test
  below, which would have caught it, and now guards against its
  recurrence or any similar mistake in this sprint's UI rewrite.
- **`numpy` pin (`requirements.txt`)** raised from `<2.0` to `>=2.0,<3.0`.
  A modern Streamlit's `st.dataframe` imports `pyarrow`, and current
  `pyarrow` releases require NumPy ≥ 2 — the old pin made
  `st.dataframe(...)` raise `ImportError` at render time on a fresh
  install. Verified nothing else in the app (statsmodels, pandas, the
  WACC/DCF math) depends on pre-2.0 NumPy behavior: full suite re-run
  clean under NumPy 2.5 with no code changes needed elsewhere.

### New test

- `tests/test_streamlit_app_smoke.py` — end-to-end `AppTest`-based smoke
  test that actually runs `streamlit_app.py` (not just the functions it
  calls), fills in a ticker, clicks Analyse against a fully mocked data
  pipeline, and asserts the whole equity report — sidebar, Snowflake,
  all 8 numbered sections, every chart — renders with no exception. This
  is the test that would have caught the `TEXT_BODY` bug above, and is
  the only test in the suite that exercises the main script directly.

**241 tests pass** (240 from Parts 1/2 and the three follow-up bug fixes,
+ 1 new end-to-end smoke test for this sprint).
