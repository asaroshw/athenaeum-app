Athenaeum Financial Intelligence: Model Methodology
Version: 2.0
Status: Authoritative Methodology Specification

Overview
This document defines the financial rationale, weighting, assumptions, decision rules, and confidence framework behind the Athenaeum Financial Intelligence stock analysis engine.
It serves as the single source of truth for why the model behaves the way it does.
The codebase is responsible for how these rules are implemented.
The test suite is responsible for demonstrating that the implementation correctly follows this methodology.
Core Principle
Athenaeum is designed around the following hierarchy:
Methodology = Why
Code = How
Tests = Proof that How implements Why
The model should prioritize:
Correctness over complexity
Transparency over false precision
Explicit uncertainty over fabricated assumptions
Validated simple models over unvalidated sophisticated models
Consistent methodology over ad-hoc exceptions
The model should never create a false impression of confidence simply because a numerical score can be calculated.

1. Fundamental Scoring
The Fundamental Score (0–100) evaluates a company's historical operating excellence, balance-sheet strength, and relative valuation.
A strong business purchased at an excessive price can still be a poor investment. The fundamental score therefore evaluates the company through three pillars:
Valuation
Past Performance
Financial Health
1.1 Standard Sector Weights
For standard companies:
Pillar
Weight
Valuation
35%
Past Performance
35%
Financial Health
30%

1.2 Financial Sector Weights
For financial companies such as banks and NBFCs:
Pillar
Weight
Valuation
45%
Past Performance
35%
Financial Health
20%

Rationale
Financial institutions are structurally different from most non-financial businesses.
Their leverage is an intrinsic part of their operating model, and traditional Debt-to-Equity measures are therefore less informative than they are for industrial or consumer companies.
Greater emphasis is consequently placed on:
Relative valuation, particularly P/B
ROE
Historical operating performance
Debt-to-Equity is intentionally down-weighted.
1.3 Scoring Transparency
A score must distinguish between:
A company that genuinely passed many checks
A company for which most checks were simply unavailable
Missing data must not automatically be interpreted as either a pass or a fail unless the methodology explicitly defines such treatment.
The system should record:
Number of applicable checks
Number of available checks
Number passed
Number failed
Data completeness
This prevents sparse data from creating an artificially strong score.

2. Relative Valuation vs Intrinsic Valuation
Athenaeum distinguishes between two different concepts of valuation.
Relative Valuation
Relative valuation evaluates how the market currently prices the company compared with relevant financial metrics.
Examples include:
P/E
P/B
EV/EBITDA
Other sector-appropriate multiples
Relative valuation contributes to the Fundamental Score.
Intrinsic Valuation
Intrinsic valuation attempts to estimate the company's underlying fair value using a fundamental valuation model.
Examples include:
DCF
Forward EPS valuation
Justified P/B
DDM
Defensive fallback methods
Intrinsic valuation contributes separately to the Intrinsic Score.
These two concepts must not be conflated.
A company can be:
Fundamentally high quality but intrinsically expensive.
Likewise, a company can be:
Relatively inexpensive but fundamentally weak.

3. Intrinsic Valuation Methodology
The engine uses a strict sequential fallback hierarchy to determine Intrinsic Fair Value.
Fundamentally incompatible valuation models should not be arbitrarily averaged together.
For example, the engine should not average a DCF value and a book-value haircut simply because both numbers are available.
Instead, the engine selects the highest-priority valid methodology and proceeds down the permitted hierarchy only when the preceding model cannot produce a reliable result.
3.1 Valuation Hierarchy
The general hierarchy is:
Primary valuation model
Secondary valuation model
Sector-specific valuation model where applicable
Defensive fallback
No reliable valuation if the available information remains insufficient
Every valuation result should record which model was actually used.
For example:
Model Used:
2-Stage DCF

or:
Model Used:
3Y Forward EPS

or:
Model Used:
Defensive Book Value Proxy

3.2 Cost of Equity
The discount rate, or Cost of Equity (Ke), uses the Capital Asset Pricing Model (CAPM).
The methodology is:
Ke = Risk-Free Rate + Beta × Equity Risk Premium
The model uses:
Dynamic Indian 10-Year Government Bond yield as the Risk-Free Rate
Stock Beta
Equity Risk Premium (ERP) of 5.5%
Ke Constraint
Ke is constrained to:
Minimum: 9%
Maximum: 20%
This prevents extreme market or data conditions from producing implausibly high or low discount rates.
The exact source and timestamp of the Risk-Free Rate should be retained as part of data provenance.
If a required CAPM input is unavailable, the model must follow the documented fallback behavior rather than silently substituting an unexplained value.

4. Primary Valuation: 2-Stage DCF
The primary intrinsic valuation model is a 2-stage Discounted Cash Flow model when reliable positive Free Cash Flow is available.
FCF is used because sustained cash generation provides an important measure of economic value and is less dependent on accounting earnings alone.
Eligibility
The DCF should only be used when sufficient and reliable FCF information exists and the relevant inputs satisfy the model's validity requirements.
If FCF is unavailable, unreliable, or fails the defined eligibility conditions, the model must proceed to the next permitted valuation method.
Required Documentation
The implementation must explicitly document:
Historical FCF period used
Forecast period
Stage-one growth assumptions
Stage-two assumptions
Discounting methodology
Terminal-value methodology
Treatment of cash
Treatment of debt
Share-count methodology
Treatment of exceptional items
These implementation details must not be inferred from variable names or left undocumented.

5. Secondary Valuation: 3Y Forward EPS Compound
The secondary valuation model is used when:
FCF is negative or otherwise unsuitable for the primary DCF
The company is profitable
The company demonstrates sufficient earnings growth under the methodology's eligibility criteria
The existing methodology uses a representative condition of:
PAT growth greater than 15% YoY
The model projects EPS three years forward and applies a terminal P/E.
Rationale
High-growth companies can temporarily produce negative FCF because they are reinvesting heavily in:
Capacity
Working capital
Expansion
New products
Infrastructure
Negative current FCF therefore does not automatically mean that the underlying business has no economic value.
For such companies, future earnings power can provide a more appropriate valuation basis.
Required Documentation
The implementation must explicitly document:
EPS starting point
Growth rate used
Three-year compounding method
Terminal P/E methodology
Whether and how the terminal value is discounted
Treatment of dilution
These details must be reconciled with the implementation rather than assumed.

6. Financial Sector Valuation
Banks and other qualifying financial institutions require sector-specific valuation.
The methodology uses:
Justified P/B
Dividend Discount Model where applicable
Excess Return on Equity logic
The key principle is:
If ROE exceeds the company's Cost of Equity, the company can justify trading above book value.
The relationship between:
ROE
Cost of Equity
Book Value
Sustainable growth
should therefore be considered when assessing justified valuation.
The exact implementation formula must be explicitly documented in the code and methodology once finalized.

7. Defensive Valuation Fallbacks
When reliable cash-flow or earnings information is unavailable, Athenaeum may use a defensive valuation proxy.
The existing methodology permits a 20%–30% haircut to:
Current Price
Book Value
depending on the applicable fallback.
This is intended to represent a conservative floor or defensive scenario rather than a high-conviction estimate of intrinsic economic value.
Important Rule
A defensive fallback must never be presented with the same confidence as a DCF or earnings-based valuation.
The result must explicitly identify:
The fallback model
Why the fallback was required
Which primary inputs were unavailable
The resulting confidence penalty
For example:
Fair Value: ₹X
Method: Defensive Book Value Proxy
Confidence: Low
Reason: Insufficient FCF and EPS data

The defensive fallback is part of the methodology and must not be removed merely because the data is incomplete.

8. Valuation Sensitivity and Uncertainty
Intrinsic valuation is an estimate, not a precise observable fact.
Where sufficient data permits, the model should communicate valuation uncertainty using:
Bear Case
Base Case
Bull Case
and/or sensitivity analysis.
For DCF-style valuations, sensitivity should consider the assumptions that materially influence value, particularly:
Growth
Discount rate
Terminal growth
The purpose is not to create unnecessary complexity.
The purpose is to prevent a single fair-value number from being interpreted as exact.
The UI should expose important assumptions underlying the base valuation.

9. Sector-Specific Rules
Sectors are identified using available company industry/sector information, with keyword-based detection where required.
The system should prefer reliable structured sector/industry information when available and use keyword classification as a fallback.
An Unknown sector classification should be allowed.
The system must not force an uncertain company into an inappropriate sector merely to avoid an unknown classification.

9.1 Financials
For financial companies:
Revenue is mapped to Total Interest Income or Operating Income where appropriate.
Debt-to-Equity requirements are relaxed.
The applicable threshold is relaxed from <1.0x to <10.0x.
Rationale
Leverage is fundamental to the banking/NBFC operating model and therefore should not be penalized using the same framework applied to ordinary companies.

9.2 Capex-Intensive and Cyclical Companies
Applicable sectors include:
Auto
Infrastructure
Materials
Other qualifying capital-intensive/cyclical businesses
Net margin requirements may be relaxed from:
>10% → >6%
when:
Operating Margins are strong
Revenue CAGR is strong
Rationale
These businesses can naturally exhibit lower net margins because of:
Depreciation
Interest expense
Capital intensity
Cyclicality
The relaxation must not apply automatically merely because a company belongs to a broad sector. The additional operating and growth conditions must also be satisfied.

10. Growth Assumptions
Future growth is one of the most sensitive inputs in intrinsic valuation.
Athenaeum therefore prioritizes forward-looking evidence over historical performance where reliable forward-looking evidence exists.
10.1 Growth Priority
The model uses the following priority:
Priority 1 — Analyst Consensus EPS Growth
Consensus EPS growth is preferred where available.
Maximum allowed growth assumption:
35%
Priority 2 — Trailing PAT YoY
If analyst consensus is unavailable, the model may use trailing PAT YoY.
Maximum allowed growth assumption:
25%
Priority 3 — Default Baseline
If neither source is available:
8%
The 8% value is explicitly a model assumption, not observed company growth.
The UI and internal data model should identify it as such.

10.2 Turnaround Adjustment
A company may be classified as a turnaround when:
Trailing earnings are negative
Recent quarter is positive
Recent quarter demonstrates >50% QoQ growth
For qualifying turnarounds:
Growth assumption floor = 20%
Rationale
Turnaround companies can exhibit mathematically extreme growth because they are recovering from a depressed earnings base.
A conventional historical growth calculation can therefore undervalue the recovery potential.
The turnaround adjustment should be explicitly recorded rather than silently changing the growth assumption.

11. Growth Assumption Provenance
Every growth assumption should identify its origin.
Possible sources include:
Analyst Consensus
Historical PAT Growth
Default Baseline
Turnaround Adjustment

The model should distinguish between:
Observed growth
Analyst-estimated growth
Model-assumed growth
Adjusted growth
This prevents a model assumption from being mistaken for an observed financial fact.

12. Technical Scoring & Timing
Technicals do not drive the fundamental investment thesis.
Their primary purpose is to assess:
Market trend
Momentum
Timing
Entry levels
Stop-loss levels
Technical analysis contributes to the Technical Score and therefore to the final composite score, but it should remain conceptually distinct from fundamental valuation.

12.1 Trend & Drift
The model uses log-price linear regression to estimate annualized compounding drift over a 252-day period.
The implementation should document:
Minimum data requirement
Regression input
Annualization method
Treatment of missing observations

12.2 ARIMA Validation
Where at least 100 days of price data exist:
ARIMA(5,1,0) is fitted
The next 30 days are forecast
Forecast direction is compared with the linear drift signal
ARIMA agreement with linear drift is required before upgrading a Neutral technical signal to:
12–18 Month Accelerated
ARIMA must not independently generate an investment thesis.
Validation Requirement
The implementation should retain the current methodology while allowing historical backtesting of:
Directional accuracy
Forecast error
Comparison with a naive baseline
Effect on subsequent investment outcomes
Backtesting is intended to evaluate the usefulness of the rule, not silently change the rule.

13. Support and Stop Loss
Support identification uses Volume Weighted Average Price profiling across 20 price bins to identify high-volume accumulation zones.
Stop losses incorporate Average True Range (ATR).
Rationale
ATR padding is intended to prevent normal daily volatility from triggering an unnecessarily tight stop.
The technical system should distinguish between:
Fundamental fair value
Preferred entry zone
Technical support
Stop-loss level
These are different concepts and should not be represented as interchangeable values.

14. Catalyst Scoring
Catalyst analysis is qualitative and is based primarily on recent news headlines and predefined Catalyst/Risk keywords.
The system identifies:
Positive catalysts
Risks
Neutral information
The catalyst system should not be treated as equivalent to audited financial data.

14.1 Materiality Filter
Order-book wins affect the growth assumption only when the stated order value exceeds:
5% of trailing revenue
Rationale
The objective is to distinguish potentially material business developments from promotional announcements that are unlikely to meaningfully affect the company's economics.
A ₹50 crore order is not automatically material for a ₹10,000 crore company.
Where reliable information exists, future implementations may also consider:
Order value relative to EBITDA
Order value relative to existing order book
Contract duration
Expected margins
Such additions require an explicit methodology update before becoming binding model rules.

14.2 Risk Negation
Risk keywords such as:
Fraud
Probe
can produce a severe:
−20 composite-score penalty
unless accompanied by recognized negation terms such as:
Cleared
Dismissed
Because headline-level keyword matching can misinterpret context, the implementation should make risk classification as context-aware as reasonably possible.
Risk classification must not silently assume that the presence or absence of one keyword proves the underlying allegation true or false.

15. Composite Verdict
The final verdict is based on three distinct pillars.
Component
Weight
Fundamental Score
40%
Intrinsic Score
35%
Technical Score
25%

Fundamental Score — 40%
Represents the historical and current fundamental reality of the business.
Intrinsic Score — 35%
Represents the company's modeled margin of safety relative to intrinsic fair value.
Technical Score — 25%
Represents market trend, momentum, support, and timing-related evidence.
These weights are methodology constants.
They may be centralized in configuration for maintainability, but must not be altered during ordinary refactoring.

16. Sanity Veto
Regardless of the composite score, the model applies a valuation sanity check.
If the target price implies:
>15% downside
the verdict is forcibly downgraded to:
OBSERVE
or DON'T BUY
as appropriate.
Rationale
A company should not receive a Buy verdict merely because it has excellent historical fundamentals when its modeled valuation indicates substantial downside.
The Sanity Veto is a deliberate override and should remain visible in the analysis output.

17. Fundamental Thesis vs Timing Thesis
Athenaeum explicitly separates:
Fundamental Thesis
Answers:
Is this a fundamentally attractive business at the current valuation?
Technical Timing
Answers:
Is the current market setup attractive for entering the position?
Therefore, the system should be capable of producing combinations such as:
Long-Term View: BUY
Technical Timing: NEUTRAL

or:
Long-Term View: BUY
Technical Timing: FAVOURABLE

This separation is intentional.
Technical strength must not be allowed to disguise fundamental overvaluation.
Likewise, temporary technical weakness must not automatically invalidate a fundamentally attractive long-term thesis.

18. Data Confidence
A high score on incomplete data is a potential false positive.
Athenaeum therefore treats model confidence separately from the investment score.
A company can have:
Fundamental Score: 88/100
Model Confidence: 52/100

This means the available evidence appears positive, but the evidence base is incomplete.

18.1 Confidence Factors
Confidence should consider factors such as:
Data completeness
Data freshness
Availability of valuation inputs
Availability of historical financial information
Analyst estimate availability
Technical history availability
News coverage
Peer-data availability
Data-source failures
Valuation fallback depth
Consistency of financial inputs

18.2 Fallback Penalty
Confidence should decline as the model moves farther down the valuation hierarchy.
For example:
Primary DCF
    ↓
High valuation confidence

Forward EPS
    ↓
Moderate valuation confidence

Defensive Book Value Proxy
    ↓
Low valuation confidence

The exact numerical confidence penalty must be defined in the implementation and documented once finalized.

18.3 Confidence Must Not Create False Precision
Confidence is not a guarantee of future returns.
It represents confidence in the quality and completeness of the evidence supporting the model output.
The UI should clearly distinguish:
Investment Score

from:
Model Confidence


19. Data Provenance
Important financial inputs should retain provenance.
Where practical, each major metric should include:
Value
Source
Period/date
Currency
Retrieval timestamp
Data freshness
Examples:
Revenue
Source: FMP
Period: FY2025
Currency: INR

or:
Price
Source: Yahoo Finance
Timestamp: 10 Aug 2026

Data from different sources must not be merged blindly when their definitions, periods, currencies, or accounting treatments may differ.
Source precedence should be defined at the field level where necessary.

20. Missing Data Policy
Missing data must remain explicitly missing.
The model must distinguish between:
Observed
Estimated
Assumed
Unavailable

The following are not equivalent:
Growth = 8%

and:
Growth unavailable

If the model uses the 8% baseline, the result must explicitly identify:
Source: Model Default Baseline
The system must not silently convert missing information into an observed financial value.

21. Data Fallback Policy
When a primary data provider fails, the system may use an approved secondary source.
However, fallback data should be recorded.
For example:
Primary Source: FMP
Status: unavailable

Fallback Source: Yahoo Finance
Status: used

The application should not silently present a fallback result as though it came from the primary source.
Partial failures should also be visible internally and, where material, to the user.

22. AI Narrative Layer
The AI component is a synthesis layer, not the financial calculation engine.
The AI must not independently invent:
Financial metrics
Growth assumptions
Valuation figures
News events
Company facts
Analyst estimates
The quantitative engine remains authoritative for numerical conclusions.
The AI should synthesize the structured evidence supplied by the analysis engine.
Where possible, AI output should use structured fields rather than relying on exact prose headings or regex-based parsing.
The AI should distinguish:
Quantitative Evidence
Forward-Looking Interpretation
AI Synthesis

The AI should be permitted to disagree with the preliminary verdict when the supplied evidence supports disagreement, but any such disagreement should be presented as an interpretation rather than a replacement of the underlying quantitative calculations.

23. Explainability Requirements
Every major model conclusion should be explainable.
The report should ideally allow the user to understand:
Why the stock scored well
For example:
Strong ROE
Strong revenue growth
Attractive relative valuation
Why it scored poorly
For example:
Weak balance sheet
Expensive P/E
Poor historical margins
Why intrinsic value was selected
For example:
DCF selected because reliable positive FCF was available.

Why confidence is low
For example:
Confidence reduced because FCF history is incomplete and
the valuation required a defensive fallback.

The objective is not to expose every internal calculation to the user, but to make the important decision path auditable.

24. Methodology Preservation Rule
The methodology document is the authoritative specification for the financial logic of the application.
During refactoring or code improvement:
Do not change financial thresholds, weights, assumptions, sector rules, valuation hierarchy, scoring rules, or verdict rules without explicit approval.
If the current code differs from the methodology, do not silently choose one interpretation.
Identify the discrepancy and document:
Methodology behavior
Current code behavior
Recommended behavior
Expected impact
Refactoring should preserve behavior wherever the existing behavior is consistent with this methodology.
When the code contradicts the methodology, the implementation should be corrected to match the methodology unless an explicit methodology change is approved.
When the methodology is ambiguous, document the ambiguity rather than inventing an assumption.
Any intentional methodology change must include:
Methodology update
Code update
Updated tests
Rationale
Expected impact

25. Mathematical Specification Requirements
Each quantitative model must eventually have a formally documented mathematical definition.
At minimum, the methodology/code documentation should explicitly define:
CAPM / Cost of Equity
DCF
Forward EPS valuation
Justified P/B
DDM
Fundamental Score
Intrinsic Score
Technical Score
Composite Score
Confidence Score
The purpose is to prevent important financial assumptions from existing only implicitly inside the code.
Where the current methodology does not yet specify an exact mathematical implementation, the implementation must not invent a permanent rule without updating this document.

26. Comments and Code Documentation
The codebase should explain the reasoning behind non-obvious decisions.
Comments should explain why, not merely repeat what the code does.
Poor comment
# Calculate growth

Better comment
# Growth follows the methodology priority:
# 1. Analyst consensus EPS growth
# 2. Trailing PAT YoY
# 3. 8% model baseline.
#
# Consensus is capped at 35% and historical PAT growth
# at 25% to prevent extreme growth observations from
# dominating intrinsic valuation.

Important methodology-driven code should reference the relevant methodology section where useful.
Example:
# Financial-sector leverage threshold is intentionally relaxed
# because leverage is intrinsic to the banking/NBFC business model.
# See Methodology §9.1.

Development-history comments such as:
Fix 1
Fix 2
Fix 12

should be removed unless they provide meaningful historical context.

27. Testing Requirements
The test suite should demonstrate that the implementation follows this methodology.
Tests should cover at minimum:
Valuation
Positive FCF
Negative FCF
Missing FCF
Profitable/high-growth company
Missing EPS
Financial-sector valuation
Defensive fallback
No reliable valuation
Correct fallback ordering
Ke lower bound
Ke upper bound
Growth
Analyst consensus available
Consensus above 35%
PAT growth fallback
PAT growth above 25%
8% baseline
Turnaround adjustment
Growth source identification
Fundamental Scoring
Standard-sector weights
Financial-sector weights
Complete data
Partial data
Missing data
Boundary conditions
Technicals
Insufficient history
100+ day ARIMA eligibility
Linear drift
ARIMA agreement
ARIMA disagreement
Support detection
ATR stop-loss calculation
Catalysts
Positive catalyst
Risk keyword
Negated risk
Material order
Immaterial order
Missing order-value information
Composite Verdict
Normal composite score
15% downside veto
Technical/fundamental disagreement
Boundary conditions
Confidence
Complete dataset
Partial dataset
Provider failure
Secondary data source
Defensive valuation fallback
Multiple missing inputs

28. Backtesting and Model Validation
The methodology should be evaluated historically without changing its rules simply to improve historical results.
Validation should measure, where applicable:
Subsequent 3-month performance
Subsequent 6-month performance
Subsequent 12-month performance
Benchmark-relative performance
Maximum drawdown
Hit rate
Directional accuracy
Forecast error
Performance by verdict category
Technical forecasting components such as ARIMA should also be compared against appropriate simple baselines.
Backtesting must avoid:
Look-ahead bias
Survivorship bias
Leakage of future financial information
Using revised information that was unavailable at the original decision date
The purpose of validation is to determine whether the methodology adds useful predictive information, not to retrofit the methodology to historical outcomes.

29. Model Limitations
Athenaeum is an analytical decision-support system.
Its outputs are estimates rather than guarantees.
The model can be affected by:
Incorrect or delayed financial data
Accounting differences
Market regime changes
Unexpected corporate events
Poor analyst estimates
Incomplete news coverage
Sector classification errors
Model assumptions
Forecast uncertainty
Data-provider failures
A high score does not guarantee positive future returns.
A low score does not guarantee a negative future return.
The purpose of the model is to provide a structured, transparent framework for evaluating evidence and uncertainty.

30. Guiding Principles
The Athenaeum model should consistently follow these principles:
1. Explicit beats implicit
Important assumptions should be visible.
2. Missing data beats fabricated certainty
Unavailable information should remain unavailable unless an explicitly documented fallback is used.
3. Transparency beats false precision
A range with assumptions is preferable to an unjustifiably precise fair-value number.
4. Validated simplicity beats unvalidated sophistication
A simpler model with demonstrated usefulness is preferable to a complex model that has not been validated.
5. Fundamental thesis and market timing are distinct
A good company and a good entry point are not necessarily the same thing.
6. Confidence is not the same as score
A high score based on weak data should not be treated as a high-confidence conclusion.
7. Fallbacks must be visible
The user should know when the model is relying on a defensive proxy rather than a high-conviction valuation model.
8. Methodology changes must be deliberate
Financial logic must never change accidentally as a consequence of ordinary code refactoring.
9. Code should implement the methodology, not redefine it
When implementation and methodology disagree, the discrepancy must be identified and resolved explicitly.
10. The model should explain itself
Every major conclusion should be traceable to evidence, assumptions, and defined decision rules.

31. Summary of the Athenaeum Decision Framework
The complete decision process can be summarized as:
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

The resulting output should communicate four things clearly:
WHAT does the model conclude?
WHY does it reach that conclusion?
HOW STRONG is the evidence?
WHAT are the major uncertainties and risks?

That is the intended philosophy of Athenaeum Financial Intelligence.


