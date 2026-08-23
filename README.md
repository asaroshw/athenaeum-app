# Athenaeum Financial Intelligence

A Streamlit dashboard for Indian-equity and IPO research: multi-model valuation,
technical scoring, fundamental checklists, and AI-assisted narrative summaries —
built for a professional-trader style workflow.

## What it does

**Equity Analysis**
- Pulls quote, fundamentals, financial statements, and price history from
  [Financial Modeling Prep](https://financialmodelingprep.com) (if an API key is
  configured) with an automatic, field-by-field fallback to
  [`yfinance`](https://github.com/ranaroussi/yfinance) — the app runs fully
  without any paid API key.
- Runs a multi-model valuation engine (DCF-style FCF extrapolation, forward-EPS
  multiples, Justified P/B, DDM, residual income) and blends the results into a
  Bear / Base / Bull scenario matrix, an entry range, a target price, a stop
  loss, and a Risk/Reward ratio.
- Scores the stock on valuation, past performance, financial health, and
  technicals (SMA trend, volume ratio, Nifty-relative momentum), each shown as
  its own checklist and "traffic light" badge.
- Surfaces recent news and order-book/guidance signals, and weaves them into a
  final narrative — written by Gemini if `GEMINI_API_KEY` is configured,
  otherwise generated algorithmically from the same underlying numbers so the
  report never goes blank.
- Advanced analytics (opened on demand, not on page load): a 5-year Historical
  Valuation Band chart, a monthly Seasonality Heatmap, and an Interactive Peer
  Scatter Plot (ROE vs. P/E, sized by market cap) against sector peers.

**IPO Analysis**
- Aggregates IPO listings from Screener.in, Chittorgarh, and ipomarket.in,
  merges and deduplicates across sources, and buckets each issue into
  Current / Upcoming / Closed.
- Per-IPO detail view, structured as a Tier-1 institutional research report:
  1. **Key Details & Issue Size** — total issue size, fresh issue vs. OFS
     split, price band, lot size, and the full date sequence.
  2. **GMP Trend & Subscription** — a GMP-trajectory line chart with
     estimated listing gain on a secondary axis, plus a stacked bar chart of
     the QIB/NII/Retail subscription buildup (when the source page exposes
     day-level data; falls back to the latest snapshot otherwise).
  3. **3-Year Financials & Profitability** — a grouped bar chart across Total
     Income, EBITDA, PAT, Assets, Net Worth, and Borrowings, plus a margins
     table (ROE, ROCE, PAT Margin, Debt/Equity — the latter two always
     derivable from the financials themselves; ROE/ROCE shown only when the
     source page has a ratios table).
  4. **Valuation Matrix** — Pre-IPO vs. Post-IPO EPS, P/E, and Market Cap,
     computed from scraped pre/post-issue share counts (shown only when that
     data is available — never estimated).
  5. **The Verdict & Context** — Key Strengths, Key Risks, and a Should-You-
     Invest verdict, written by Gemini if configured (with recent news woven
     in as real-world context, not just listed), otherwise generated
     algorithmically from the same data.

## Architecture

```
streamlit_app.py          Page routing and layout — the only file that calls
                           st.* layout functions directly for the equity view
                           (IPO layout lives inside athenaeum/data/ipo.py).

athenaeum/
  config.py                Color palette, keyword lists, tunable constants.
  data/
    equity.py               FMP + yfinance fetching, news, sector-peer data.
    ipo.py                  IPO scraping, scoring, and IPO-specific UI render
                             functions (list rows + detail view).
    rfr.py                  Dynamic risk-free-rate lookup for the DCF/DDM models.
  models/
    technical.py             Technical scoring + the standalone regime-badge signals.
    fundamentals.py          Checklist functions + continuous 0-100 sub-scores.
    valuation.py              Multi-model valuation engine.
    sector.py                 Sector/industry classification.
    pipeline.py                Orchestrates the above into one prediction dict.
  analysis/
    sentiment.py               News keyword scan + optional LLM materiality pass.
  ai/
    reports.py                  Gemini narrative generation + algorithmic fallback.
  ui/
    components.py                Reusable Streamlit/Plotly render functions.
  utils/
    helpers.py                    Parsing, formatting, and other pure helpers.

tests/                       pytest suite — see "Testing" below.
```

**Design principle used throughout:** data fetching (`athenaeum/data`, `athenaeum/ai`)
never calls Streamlit layout functions except where IPO's render helpers live in
`ipo.py` alongside its data functions for cohesion; everything under `models/`
and `utils/` is pure Python with no Streamlit or network dependency, which is
what makes most of the test suite possible without mocking a live app.

## Setup

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Requires Python 3.11+ (matches `.devcontainer/devcontainer.json`).

### Configuration

Both API keys are **optional** — the app is fully functional with neither
configured, falling back to `yfinance` for market data and an algorithmic
(non-AI) narrative for the written summary. Set either via Streamlit secrets
(`.streamlit/secrets.toml`) or environment variables picked up by your
deployment platform:

```toml
# .streamlit/secrets.toml
FMP_API_KEY = ""      # optional — financialmodelingprep.com; unlocks the
                       # primary (faster, richer) data path
GEMINI_API_KEY = ""   # optional — unlocks AI-written narrative sections
                       # instead of the algorithmic-fallback summary
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The suite (`tests/`, 110 tests) covers:
- **Pure logic** — valuation models, fundamental scoring, technical signals,
  sector classification, news sentiment, IPO dedup/merge/bucketing, the
  header-aware financials parsers, and the Pre/Post-IPO valuation-matrix
  computation — with synthetic inputs and explicit expected values.
- **Async equivalence** — every parallelized data-fetching path (see below)
  is tested against mocked HTTP/`yfinance` responses to confirm the concurrent
  version produces the same merged output a sequential implementation would.
- **UI component smoke tests** — every render function, including the full
  5-section institutional IPO detail view end-to-end, is exercised with
  normal, edge-case, and missing-data inputs to confirm it never raises.

Network access is never required to run the suite — every external call is
mocked.

## Performance notes

Equity and IPO data fetching dispatch their independent HTTP/`yfinance` calls
concurrently via `concurrent.futures.ThreadPoolExecutor` rather than
sequentially:

- `fetch_fmp_data` — all 7 FMP endpoints dispatch at once.
- `fetch_stock_data` — the FMP fetch and all 8 `yfinance` property reads
  (`.info`, `.history`, `.quarterly_financials`, `.financials`,
  `.balance_sheet`, `.cashflow`, `.mutualfund_holders`, `.calendar`) dispatch
  at once; each `yfinance` call uses its own `Ticker` instance rather than
  sharing one across threads, since `yfinance`'s internal caching isn't
  documented as thread-safe for concurrent access on a single object.
- `fetch_ipo_list_categorized` — all 5 source scrapes dispatch at once.
- `fetch_ipo_detail` — the detail-page fetch, news fetch, and screener
  cross-reference lookup dispatch at once (all three depend only on the slug
  and company name, known before the detail page is even parsed).

In every case, result *processing* stays in its original sequential order —
only the network dispatch moved earlier — so scoring, valuation, and merge
logic are unchanged from a sequential implementation. The `tests/test_async_equivalence.py`
suite is the concrete check for this.

## Known limitations

- The Historical Valuation Band chart approximates a historical P/E (or P/B)
  band by holding the *current* trailing multiple constant across the
  lookback window, rather than using a true historical-EPS time series —
  free data sources don't reliably provide 3–5 years of quarterly
  fundamentals. This is labeled in the chart's caption.
- Day-by-day GMP and subscription-buildup charts render only when the
  underlying IPO source page happens to expose that granularity; otherwise
  they fall back gracefully (a single current-value point, or no chart).
- The 3-Year Financials chart, Margins table (ROE/ROCE specifically), and
  Valuation Matrix all depend on the source IPO page exposing that level of
  detail (extra financial-statement columns, a ratios table, and pre/post-
  issue share counts respectively). Each degrades gracefully — showing
  whichever metrics were found and omitting the rest — rather than
  estimating a number that wasn't actually on the page.
- IPO and news scraping depend on third-party site markup that can change
  without notice; scrapers are written defensively (regex/keyword-based, with
  try/except around every network call) but aren't guaranteed against
  upstream layout changes.
