# Athenaeum Financial Intelligence

Athenaeum Financial Intelligence is a Streamlit-based stock-analysis application designed to combine sector-aware fundamental analysis, intrinsic valuation, technical analysis, catalyst/risk signals, model-confidence assessment, and an AI-generated narrative report.

The project is built around a clear separation of responsibilities:

- **Methodology = Why** the model behaves the way it does.
- **Code = How** those rules are implemented.
- **Tests = Proof** that the implementation follows the methodology.

The accompanying `methodology.md` is the authoritative specification for the financial logic. The application code should implement that methodology rather than silently redefine it.

---

## Features

Athenaeum produces a single-page stock report containing:

- Company and market overview
- Current price and key valuation metrics
- Fundamental score
- Intrinsic valuation and target price
- Technical score and market-timing assessment
- Price-history visualisation
- Recent news and catalyst signals
- Quantitative strengths and weaknesses
- Dividend and capital-allocation information
- Management and compensation information
- Ownership / insider-related information where available
- Corporate events and mutual-fund holdings where available
- Model confidence and supporting evidence
- AI-generated analyst-style narrative
- Sector-aware peer/alternative-stock suggestions when the primary stock receives `OBSERVE` or `DON'T BUY`

The quantitative engine remains responsible for financial calculations; the AI layer is intended as a synthesis and explanation layer.

---

## How the Analysis Works

At a high level, the application follows this workflow:

```text
Company / Ticker
      ↓
Data Acquisition
      ↓
Data Normalization & Provenance
      ↓
Sector Classification
      ↓
Fundamental Analysis
      ↓
Relative Valuation
      ↓
Intrinsic Valuation
      ↓
Growth Assumption
      ↓
Technical Analysis
      ↓
Catalyst / Risk Analysis
      ↓
Data Confidence
      ↓
Composite Score
      ↓
Sanity Veto
      ↓
Fundamental View + Technical Timing
      ↓
Final Verdict
      ↓
AI Narrative / Explanation
```

This reflects the methodology specification rather than treating the AI narrative as the source of the investment decision.

---

## Fundamental Analysis

The Fundamental Score evaluates three pillars:

1. **Valuation**
2. **Past Performance**
3. **Financial Health**

For standard companies, the methodology uses:

| Pillar | Weight |
|---|---:|
| Valuation | 35% |
| Past Performance | 35% |
| Financial Health | 30% |

For financial companies such as banks and NBFCs:

| Pillar | Weight |
|---|---:|
| Valuation | 45% |
| Past Performance | 35% |
| Financial Health | 20% |

The system is sector-aware because leverage, margins, and appropriate valuation measures differ across business types.

Missing data is not automatically treated as a pass. The methodology requires the model to distinguish available evidence from unavailable information.

---

## Relative vs. Intrinsic Valuation

Athenaeum keeps relative valuation and intrinsic valuation separate.

### Relative valuation

Relative valuation considers measures such as:

- P/E
- P/B
- EV/EBITDA
- Other sector-appropriate multiples

These contribute to the **Fundamental Score**.

### Intrinsic valuation

Intrinsic valuation estimates a company's underlying fair value using an appropriate valuation model.

The documented hierarchy is broadly:

1. Primary valuation model
2. Secondary valuation model
3. Sector-specific valuation model
4. Defensive fallback
5. No reliable valuation if the available information is insufficient

The application records the valuation model used rather than arbitrarily averaging incompatible valuation methods.

### Valuation models

Depending on data availability and company characteristics, the methodology supports:

- 2-stage DCF
- 3-year forward EPS valuation
- Justified P/B
- Dividend Discount Model
- Defensive valuation proxies

The DCF is the primary intrinsic method when reliable positive free cash flow is available.

---

## Cost of Equity

The methodology uses CAPM:

```text
Ke = Risk-Free Rate + Beta × Equity Risk Premium
```

The documented inputs include:

- Dynamic Indian 10-year government bond yield
- Stock beta
- Equity Risk Premium of 5.5%

Cost of equity is constrained between:

```text
Minimum: 9%
Maximum: 20%
```

---

## Growth Assumptions

Growth assumptions follow a documented priority:

1. Analyst consensus EPS growth
2. Trailing PAT YoY growth
3. 8% model baseline

The methodology caps:

- Consensus growth at **35%**
- Historical PAT growth at **25%**

A qualifying turnaround may receive a **20% growth floor**.

The model should identify whether a growth figure is observed, estimated, assumed, or adjusted rather than presenting a model assumption as an observed financial fact.

---

## Technical Analysis

Technical analysis is deliberately separated from the fundamental investment thesis.

Its role is to assess:

- Market trend
- Momentum
- Timing
- Entry levels
- Support
- Stop-loss levels

The technical framework includes:

- 252-day log-price linear-regression drift
- ARIMA(5,1,0) validation when sufficient price history exists
- 30-day ARIMA forecasting
- Volume-profile support detection
- ATR-based stop-loss calculation

ARIMA is used as a validation component rather than as an independent investment thesis.

---

## Catalyst and Risk Analysis

Recent news headlines are scanned for predefined catalyst and risk signals.

Examples of positive catalyst themes include:

- Acquisitions
- Profit improvement
- Turnarounds
- Order wins
- Expansion
- Partnerships
- Record revenue
- Upgrades
- Contract wins

Risk themes include:

- Fraud
- Resignation
- Default
- Investigation
- Lawsuits
- Bankruptcy
- Insolvency
- Delisting

The methodology includes context-aware risk negation so that headlines containing terms such as `cleared` or `dismissed` are not automatically treated as confirmed negative events.

Order-book signals are subject to a materiality filter: the methodology states that an order should exceed **5% of trailing revenue** before it affects the growth assumption.

News-based signals are qualitative evidence and should not be treated as equivalent to audited financial information.

---

## Composite Verdict

The final composite score uses:

| Component | Weight |
|---|---:|
| Fundamental Score | 40% |
| Intrinsic Score | 35% |
| Technical Score | 25% |

The model also applies a valuation sanity check. If modeled target value implies more than **15% downside**, the verdict can be forcibly downgraded to `OBSERVE` or `DON'T BUY`.

The application intentionally distinguishes:

```text
Fundamental thesis
        vs.
Technical timing
```

For example, a company can be fundamentally attractive while having neutral technical timing.

---

## Model Confidence

Investment score and model confidence are separate concepts.

A high score does not necessarily mean high confidence if the underlying evidence is incomplete.

Confidence can be affected by:

- Data completeness
- Data freshness
- Availability of valuation inputs
- Historical financial information
- Analyst estimates
- Technical history
- News coverage
- Peer data
- Data-provider failures
- Valuation fallback depth
- Consistency of financial inputs

Moving farther down the valuation fallback hierarchy should reduce valuation confidence.

---

## Data Sources and Integrations

The application uses several external services/libraries.

### Financial Modeling Prep

FMP is used as the primary structured financial-data source when an API key is configured.

The code reads:

```text
FMP_API_KEY
```

from Streamlit secrets.

### Yahoo Finance

`yfinance` is used extensively for market data and as a fallback when primary financial data is unavailable.

### Google Gemini

The AI narrative layer uses Google's Gemini API.

The application reads:

```text
GEMINI_API_KEY
```

from Streamlit secrets.

### Google News RSS

Recent news headlines are retrieved through Google News RSS.

### Plotly

Plotly is used for interactive financial charts and visualisations.

---

## Requirements

The supplied `requirements.txt` includes:

```text
fastapi
uvicorn
yfinance
pandas
numpy
google-genai
requests
streamlit
plotly
beautifulsoup4
statsmodels
```

Install them with:

```bash
pip install -r requirements.txt
```

Python 3.10+ is recommended.

---

## Project Structure

A simple project layout is recommended:

```text
athenaeum/
├── streamlit_app.py
├── athenaeum_commented_code.py
├── methodology.md
├── requirements.txt
├── Logo.png
├── README.md
└── .streamlit/
    └── secrets.toml
```

The supplied application file is currently named:

```text
streamlit_app (1)(1).py
```

For a cleaner project structure, rename it to:

```text
streamlit_app.py
```

The commented implementation can be retained separately as a reference/documentation version.

---

## Streamlit Secrets Configuration

The current application is already configured to read its API credentials from **Streamlit Secrets** using `st.secrets`. You do **not** need to put the keys into the Python source code.

The application expects these two secret names:

```text
FMP_API_KEY
GEMINI_API_KEY
```

### Streamlit Cloud / deployed application

If the application is deployed through Streamlit Community Cloud, add the secrets in the app's **Settings → Secrets** section.

Use this structure:

```toml
FMP_API_KEY = "YOUR_FMP_API_KEY"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
```

Replace the placeholder values with the actual API keys. Keep the keys private.

The code reads them as follows:

```python
FMP_KEY = st.secrets.get("FMP_API_KEY", "")
GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
```

FMP is the application's primary structured financial-data source when `FMP_API_KEY` is available. If the FMP key is absent or FMP does not provide a field, the application can fall back to `yfinance` for supported data. fileciteturn2file8L524-L560

Gemini is used for the final qualitative synthesis/report narrative. The application reads `GEMINI_API_KEY` from Streamlit Secrets before creating the Gemini client. fileciteturn2file1L91-L104

### Local development

For local Streamlit development, create:

```text
.streamlit/secrets.toml
```

with:

```toml
FMP_API_KEY = "YOUR_FMP_API_KEY"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
```

Then run:

```bash
streamlit run streamlit_app.py
```

### Important security rules

**Never:**

- Put the real API keys in `streamlit_app.py`.
- Put the real API keys in `README.md`.
- Commit `.streamlit/secrets.toml` to Git.
- Paste the keys into public issues, documentation, screenshots, or source-control commits.

Add the secrets file to `.gitignore`:

```gitignore
.streamlit/secrets.toml
__pycache__/
*.pyc
.venv/
.env
```

If the keys have already been committed to a public repository, rotate/revoke them through the respective API providers rather than simply deleting them from the latest commit.

`Logo.png` should also be present if you want the configured page icon and application branding to display correctly.

---

## Running the Application

After installing dependencies:

```bash
streamlit run streamlit_app.py
```

If you keep the original uploaded filename, use:

```bash
streamlit run "streamlit_app (1)(1).py"
```

Streamlit will provide a local URL, normally similar to:

```text
http://localhost:8501
```

Open that address in your browser.

---

## Using the Application

1. Start the Streamlit application.
2. Enter a company name or ticker in the search box.
3. Click **Analyse**.
4. The application resolves the input to an exchange ticker where possible.
5. Financial and market data are collected.
6. The company is assigned a sector profile.
7. Fundamental checks and valuation models are calculated.
8. Technical indicators and timing signals are evaluated.
9. Recent news is scanned for catalysts and risks.
10. The composite verdict and confidence framework are applied.
11. Gemini generates the qualitative narrative from the structured analysis.
12. The final report is displayed in the Streamlit interface.

Example inputs may look like:

```text
RELIANCE.NS
TCS.NS
INFY
HDFCBANK
```

Ticker resolution depends on the available market-data provider.

---

## Output

The generated report includes a high-level verdict together with the evidence behind it.

The interface includes, among other components:

- Verdict badge
- Current price
- Scorecard
- Price-history chart
- Composite-score radar
- Recent news
- Company overview
- Quantitative strengths
- Quantitative weaknesses
- Valuation and fair-value information
- Growth and outlook
- Earnings quality
- Financial health
- Dividend/capital allocation
- Management
- Ownership structure
- Narrative summary
- Sector alternative when applicable

The goal is explainability: the report should communicate what the model concludes, why it reaches that conclusion, how strong the evidence is, and what the major uncertainties are.

---

## AI Narrative Layer

The Gemini component is not intended to replace the quantitative engine.

The application supplies the AI with structured information such as:

- Valuation model used
- Growth assumption
- Target price
- Composite score
- Fundamental score
- Intrinsic score
- Technical score
- Sector profile
- Turnaround status
- Catalyst signals
- Recent news headlines

The AI is instructed to produce eight narrative sections:

1. Valuation & Fair Value
2. Future Growth & Outlook
3. Past Performance & Earnings Quality
4. Financial Health & Balance Sheet
5. Dividend & Capital Allocation
6. Management & Compensation
7. Ownership Structure & Insider Sentiment
8. Narrative Verdict

The methodology explicitly requires the AI layer to act as a synthesis layer and not invent financial metrics, valuation figures, company facts, news events, or analyst estimates.

---

## Methodology Contract

`methodology.md` is the authoritative source for the financial logic.

When modifying the code:

- Do not casually change thresholds.
- Do not change score weights during ordinary refactoring.
- Do not silently change valuation assumptions.
- Do not replace missing data with fabricated values.
- Do not hide fallback valuation methods.
- Do not silently resolve disagreements between code and methodology.
- Document intentional methodology changes.
- Update tests when methodology changes.

The intended development hierarchy is:

```text
Methodology = Why
Code        = How
Tests       = Proof
```

---

## Testing and Validation

A dedicated test suite is not included among the supplied project files, so tests should be added before treating the model as production-grade.

The methodology recommends testing at least:

- Valuation eligibility and fallback logic
- Growth-assumption priority
- Fundamental scoring
- Missing-data behaviour
- Sector-specific thresholds
- Technical signals
- ARIMA eligibility
- Catalyst detection
- Risk negation
- Order-book materiality
- Composite scoring
- Sanity veto
- Confidence calculations
- Boundary conditions

For historical validation, the methodology calls for avoiding:

- Look-ahead bias
- Survivorship bias
- Leakage of future financial information
- Use of revised information that was unavailable at the original decision date

Technical forecasting should also be compared against appropriate simple baselines.

---

## Important Limitations

Athenaeum is an analytical decision-support system, not a guarantee of investment returns.

Its output can be affected by:

- Incorrect or delayed financial data
- Accounting differences
- Market-regime changes
- Unexpected corporate events
- Poor analyst estimates
- Incomplete news coverage
- Sector-classification errors
- Model assumptions
- Forecast uncertainty
- Data-provider failures

A high score does not guarantee positive future returns, and a low score does not guarantee negative future returns.

The model should therefore be used as a structured research and decision-support tool rather than as an autonomous trading system.

---

## Development Notes

When extending the application, prefer changes that improve:

1. Correctness
2. Transparency
3. Data provenance
4. Explainability
5. Validation
6. Maintainability

Avoid introducing complexity merely to produce a more sophisticated-looking score.

Important methodology-driven calculations should have comments explaining **why** the calculation exists, rather than merely repeating what the code does.

---

## License

No license was provided with the supplied project files.

If this project will be distributed publicly, add an appropriate license file such as `LICENSE` before publishing.

---
