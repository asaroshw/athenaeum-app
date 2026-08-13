"""Multi-model valuation engine."""
from __future__ import annotations
import logging
import numpy as np
from athenaeum.config import TERMINAL_GROWTH_PCT

logger = logging.getLogger("athenaeum")

def justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share, pb_floor=0.4, pb_cap=None):
    """Fix 12: pb_cap is dynamic — high-ROE institutions (ROE>20%) justify >8x P/B."""
    if pb_cap is None:
        pb_cap = 12.0 if (roe_pct and roe_pct > 20) else 8.0
    if not book_value_per_share or book_value_per_share <= 0 or roe_pct is None:
        return None, None
    roe, ke, g = roe_pct / 100, ke_pct / 100, growth_pct / 100
    if ke <= g:
        g = ke - 0.02
    jpb = 1 + (roe - ke) / (ke - g)
    jpb = min(max(jpb, pb_floor), pb_cap)
    return round(jpb, 2), round(jpb * book_value_per_share, 2)


def ddm_fair_value(dividend_per_share, ke_pct, growth_pct):
    if not dividend_per_share or dividend_per_share <= 0:
        return None
    ke, g = ke_pct / 100, growth_pct / 100
    if ke <= g:
        g = ke - 0.02
    return round((dividend_per_share * (1 + g)) / (ke - g), 2)


def compute_multi_model_values(
    current_price, ke_pct, growth_pct, financial, book_value_per_share, dividend_per_share,
    roe_pct, effective_eps, resolved_pe, fcf_history, shares, precomputed_jpb, precomputed_ddm,
    sector_profile, pat_yoy_pct,
):
    """Multi-model valuation with one explicit forecast growth and non-overlapping model families.
    Near-term growth may exceed Ke; only terminal growth is constrained below Ke.
    DCF is a simplified FCF extrapolation (not a full operating DCF).
    """
    models = {}
    ke = ke_pct / 100.0
    # Explicit near-term forecast (may exceed Ke); used for stage-1 projections
    near_term_g = min(max(float(growth_pct) / 100.0, 0.02), 0.40)
    # Terminal growth always below Ke
    tg = min(TERMINAL_GROWTH_PCT / 100.0, max(ke - 0.02, 0.02))
    # Fade path for multi-year models: y1-3 near-term, y4-5 toward terminal
    def growth_at_year(t):
        if t <= 3:
            return near_term_g
        if t == 4:
            return 0.65 * near_term_g + 0.35 * tg
        return 0.35 * near_term_g + 0.65 * tg

    if financial:
        # Prefer independent family members — do NOT also emit Blended + JPB + DDM
        jpb_ratio, jpb_value = (precomputed_jpb if precomputed_jpb is not None
                               else justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share))
        ddm_val = precomputed_ddm if precomputed_ddm is not None else ddm_fair_value(dividend_per_share, ke_pct, growth_pct)
        if jpb_value:
            models["Justified P/B"] = float(jpb_value)
        if ddm_val:
            models["DDM"] = float(ddm_val)
        # Residual income is a separate accounting-family cross-check (below)
    else:
        # Income-multiple family: use model_growth (growth_pct), not PAT YoY override
        if effective_eps and effective_eps > 0:
            g_eps = near_term_g  # same hierarchy as pipeline
            term_pe = 28 if growth_pct >= 25 else (22 if growth_pct >= 15 else 16)
            eps = float(effective_eps)
            for t in range(1, 4):
                eps *= (1 + growth_at_year(t))
            models["3Y Forward EPS"] = (eps * term_pe) / ((1 + ke) ** 3)

            hist_pe = resolved_pe if (resolved_pe and resolved_pe > 0) else 18.0
            target_pe = min(max(hist_pe, 12), 28)
            models["Trailing EPS × Target P/E"] = effective_eps * target_pe

        # Simplified FCF DCF (extrapolation — not full operating DCF)
        if fcf_history is not None and len(fcf_history) > 0 and shares and shares > 0:
            try:
                fcf_chrono = fcf_history.iloc[::-1] if hasattr(fcf_history, "iloc") else list(reversed(list(fcf_history)))
                fcf_vals = [float(x) for x in fcf_chrono if x is not None and not (isinstance(x, float) and np.isnan(x))]
                if fcf_vals:
                    weights = np.arange(1, len(fcf_vals) + 1, dtype=float)
                    avg_fcf = float(np.average(fcf_vals, weights=weights))
                    fcf_ps = avg_fcf / shares
                    if fcf_ps > 0:
                        pv = 0.0
                        fcf_t = fcf_ps
                        for t in range(1, 6):
                            fcf_t *= (1 + growth_at_year(t))
                            pv += fcf_t / ((1 + ke) ** t)
                        tv = (fcf_t * (1 + tg)) / (ke - tg)
                        models["Simplified FCF DCF"] = pv + tv / ((1 + ke) ** 5)
            except Exception as e:
                logger.warning("Multi-model DCF failed: %s", e)

        if book_value_per_share and book_value_per_share > 0:
            models["Book × 0.8"] = book_value_per_share * 0.80

    # Accounting-family residual income (simplified cross-check)
    if book_value_per_share and book_value_per_share > 0 and roe_pct is not None:
        excess = (roe_pct / 100.0) - ke
        if excess > -0.05:
            ri = 0.0
            bv = book_value_per_share
            for t in range(1, 6):
                ri += (excess * bv) / ((1 + ke) ** t)
                bv *= (1 + min(near_term_g, 0.12))
            models["Simplified Residual Income"] = book_value_per_share + ri

    # Non-overlapping family weights (one representative per economic family where possible)
    if financial or sector_profile == "financial":
        weight_map = {
            "Justified P/B": 0.50, "DDM": 0.25, "Simplified Residual Income": 0.25,
            "Book × 0.8": 0.05, "Defensive Haircut 25%": 0.05,
        }
        preference = ["Justified P/B", "DDM", "Simplified Residual Income", "Book × 0.8"]
    elif sector_profile in ("capex_intensive", "materials", "cyclical"):
        weight_map = {
            "Simplified FCF DCF": 0.45, "3Y Forward EPS": 0.30,
            "Trailing EPS × Target P/E": 0.15, "Simplified Residual Income": 0.10,
            "Book × 0.8": 0.05, "Defensive Haircut 25%": 0.05,
        }
        preference = ["Simplified FCF DCF", "3Y Forward EPS", "Trailing EPS × Target P/E",
                      "Simplified Residual Income", "Book × 0.8"]
    else:
        weight_map = {
            "Simplified FCF DCF": 0.35, "3Y Forward EPS": 0.30,
            "Trailing EPS × Target P/E": 0.20, "Simplified Residual Income": 0.15,
            "Book × 0.8": 0.05, "Defensive Haircut 25%": 0.05,
            "Justified P/B": 0.20, "DDM": 0.15,
        }
        preference = ["Simplified FCF DCF", "3Y Forward EPS", "Trailing EPS × Target P/E",
                      "Simplified Residual Income", "Justified P/B", "DDM", "Book × 0.8"]

    primary_name, primary_val = None, None
    for name in preference:
        if name in models and models[name] and models[name] > 0:
            primary_name, primary_val = name, models[name]
            break
    if primary_val is None and current_price:
        models["Defensive Haircut 25%"] = current_price * 0.75
        primary_name, primary_val = "Defensive Haircut 25%", current_price * 0.75

    core = {k: v for k, v in models.items() if v and v > 0 and k not in ("Book × 0.8", "Defensive Haircut 25%")}
    if not core:
        core = {k: v for k, v in models.items() if v and v > 0}
    wsum = wval = 0.0
    for k, v in core.items():
        w = weight_map.get(k, 0.10)
        wsum += w
        wval += w * v
    blended = (wval / wsum) if wsum > 0 else primary_val

    vals = list(core.values())
    conf = "Low"
    valuation_cv = None
    if len(vals) >= 2:
        mu = float(np.mean(vals))
        sd = float(np.std(vals))
        valuation_cv = (sd / mu) if mu > 0 else 9.0
        if valuation_cv < 0.15 and len(vals) >= 3:
            conf = "High"
        elif valuation_cv < 0.25:
            conf = "Medium-High"
        elif valuation_cv < 0.40:
            conf = "Medium"
        else:
            conf = "Low"
    elif len(vals) == 1:
        conf = "Low-Medium"

    rounded = {}
    for k, v in models.items():
        if v is None:
            continue
        rounded[k] = round(v) if v > 50 else round(v, 1)

    return {
        "models": rounded,
        "primary_name": primary_name or "N/A",
        "primary_value": round(primary_val) if primary_val and primary_val > 50 else (round(primary_val, 1) if primary_val else None),
        "blended_value": round(blended) if blended and blended > 50 else (round(blended, 1) if blended else None),
        "n_models": len(rounded),
        "valuation_confidence": conf,
        "valuation_cv": round(valuation_cv, 3) if valuation_cv is not None else None,
        "near_term_growth_used": round(near_term_g * 100, 2),
        "terminal_growth_used": round(tg * 100, 2),
    }

