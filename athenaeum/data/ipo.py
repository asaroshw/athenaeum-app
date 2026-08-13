"""Indian IPO data: Screener primary, Chittorgarh, ipomarket, AI GMP."""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta
import requests
import streamlit as st
from bs4 import BeautifulSoup
import plotly.graph_objects as go
import plotly.express as px

from athenaeum.utils.helpers import (
    html_escape_fn, _parse_date_flex, _parse_money_inr, _parse_gmp, _parse_price_band,
    _slug_from_href, _classify_bucket, to_float,
)
html_escape = html_escape_fn

from athenaeum.config import GREEN, RED, MUTED, BLUE, BORDER, ORANGE, CARD_BG, BG
from athenaeum.data.equity import fetch_google_news
from athenaeum.ui.components import custom_metric, card
from athenaeum.ai.reports import ipo_ai_narrative
from athenaeum.utils.helpers import style_verdict_text, rating_color

logger = logging.getLogger("athenaeum")
_IPO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_ipomarket_list(path: str) -> list:
    url = f"https://www.ipomarket.in{path}"
    try:
        r = requests.get(url, headers=_IPO_HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []

    results, seen = [], set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if not any("company" in h for h in headers):
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            link = None
            for a in row.find_all("a", href=True):
                if "/ipo/" in a["href"] and "affiliate" not in a["href"]:
                    link = a["href"]
                    break
            slug = _slug_from_href(link) or re.sub(r"[^a-z0-9]+", "-", texts[0].lower())[:60]
            if slug in seen:
                continue
            seen.add(slug)

            def col(keys, default=""):
                for i, h in enumerate(headers):
                    if any(k in h for k in keys) and i < len(texts):
                        return texts[i]
                return default

            name_raw = texts[0]
            name = re.sub(r"^[A-Z]{1,3}\s+", "", name_raw)
            name = re.sub(r"\s+(Agriculture|Others|Mainboard|SME)$", "", name).strip()
            # Derive status strictly from the path requested, not from a scraped cell that
            # is often missing or inconsistent across sections of the same page.
            if "open" in path:
                status = "OPEN"
            elif "listed" in path:
                status = "LISTED"
            else:
                status = "UPCOMING"
            open_s = col(["open", "expected"])
            close_s = col(["close"])
            band_s = col(["price band", "price"])
            lot_s = col(["lot"])
            min_inv = col(["min"])
            gmp_s = col(["gmp"])
            sub_s = col(["subscription", "subs"])
            issue_s = col(["issue size", "size"])
            plo, phi = _parse_price_band(band_s)
            gmp_rs, gmp_pct = _parse_gmp(gmp_s)
            open_d = _parse_date_flex(open_s)
            close_d = _parse_date_flex(close_s)

            results.append({
                "symbol": slug,
                "slug": slug,
                "name": name,
                "status": status,
                "date": open_s or "",
                "open_date": open_d.isoformat() if open_d else None,
                "close_date": close_d.isoformat() if close_d else None,
                "price_low": plo,
                "price_high": phi,
                "price_band_str": band_s or "",
                "lot_size": lot_s if lot_s not in ("TBA", "—", "") else None,
                "min_investment": min_inv if min_inv not in ("TBA", "—", "") else None,
                "gmp": gmp_rs,
                "gmp_pct": gmp_pct,
                "gmp_str": gmp_s if gmp_s not in ("—", "") else None,
                "subscription_str": sub_s if sub_s not in ("—", "") else None,
                "issue_size_cr": _parse_money_inr(issue_s),
                "issue_size_str": issue_s or "",
                "exchange": "NSE/BSE",
                "detail_url": f"https://www.ipomarket.in/ipo/{slug}",
                "source": "ipomarket",
            })
    return results


@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_chittorgarh_dashboard() -> list:
    """Chittorgarh only provides a name+link — no dates, no status.
    We deliberately leave status blank so _classify_bucket uses date evidence
    rather than blindly trusting a CURRENT hint that covers listed & upcoming too."""
    out, seen = [], set()
    try:
        r = requests.get("https://www.chittorgarh.com/ipo/", headers=_IPO_HEADERS, timeout=20)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            t = a.get_text(" ", strip=True)
            if "/ipo_review/" not in href:
                continue
            name = re.sub(r"\s+IPO$", "", t).strip()
            if not name or name.lower() in ("ipo", "sme ipo"):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if slug in seen:
                continue
            seen.add(slug)
            full = ("https://www.chittorgarh.com" + href) if href.startswith("/") else href
            out.append({
                "symbol": slug, "slug": slug, "name": name,
                # status intentionally left blank — no date data available from this source
                "status": "", "date": "", "open_date": None, "close_date": None,
                "price_low": None, "price_high": None, "price_band_str": "",
                "exchange": "NSE/BSE", "detail_url": full, "source": "chittorgarh",
            })
    except Exception:
        pass
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_screener_ipo_list() -> dict:
    out = {"current": [], "closed": [], "upcoming": []}
    H = _IPO_HEADERS

    def _parse_period(period):
        if not period or period in ("-", "—", ""):
            return None, None, ""
        # Strip ordinal suffixes before date parsing
        period = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", period)
        parts = re.split(r"\s*[-–]\s*", period)
        today = datetime.today().date()
        year = today.year

        def _with_year(piece):
            piece = piece.strip()
            if re.search(r"\d{4}", piece):
                return _parse_date_flex(piece)
            # Try current year first, fall back to next year for month-only strings
            # that appear to already be in the past (handles year boundary wraps)
            d = _parse_date_flex(f"{piece} {year}")
            if d is None:
                d = _parse_date_flex(f"{piece} {year + 1}")
            return d

        open_d = _with_year(parts[0]) if parts else None
        close_d = _with_year(parts[1]) if len(parts) > 1 else None
        return open_d, close_d, period

    try:
        r = requests.get("https://www.screener.in/ipo/", headers=H, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            main = None
            for t in soup.find_all("table"):
                hdrs = [c.get_text(" ", strip=True) for c in (t.find_all("tr")[0].find_all(["th", "td"]) if t.find_all("tr") else [])]
                if hdrs and hdrs[0] == "Name" and any("Subscription Period" in h for h in hdrs):
                    main = t
                    break
            if main:
                for row in main.find_all("tr")[1:]:
                    a = row.find("a", href=re.compile(r"/company/"))
                    if not a:
                        continue
                    cells = row.find_all("td", recursive=False) or row.find_all("td")
                    texts = [c.get_text(" ", strip=True) for c in cells]
                    if len(texts) < 4:
                        continue
                    name = re.sub(r"\s+", " ", re.sub(r"\s*(NSE|BSE)\s*", " ", a.get_text(" ", strip=True))).strip()
                    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                    open_d, close_d, period = _parse_period(texts[1] if len(texts) > 1 else "")
                    plo, phi = _parse_price_band(texts[2] if len(texts) > 2 else "")
                    listing_s = texts[3] if len(texts) > 3 else ""
                    mcap = texts[4] if len(texts) > 4 else ""

                    rec = {
                        "symbol": slug, "slug": slug, "name": name,
                        "date": period, "open_date": open_d.isoformat() if open_d else None,
                        "close_date": close_d.isoformat() if close_d else None,
                        "price_low": plo, "price_high": phi,
                        "price_band_str": texts[2] if len(texts) > 2 else "",
                        "issue_size_cr": _parse_money_inr(mcap),
                        "issue_size_str": f"₹{mcap} Cr" if mcap else "",
                        "listing_date_str": listing_s,
                        "exchange": "NSE/BSE",
                        "detail_url": "https://www.screener.in" + a["href"],
                        "source": "screener",
                    }
                    out["current"].append(rec)
    except Exception:
        pass

    try:
        r = requests.get("https://www.screener.in/ipo/recent/", headers=H, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup.find_all("table"):
                for row in t.find_all("tr")[1:]:
                    a = row.find("a", href=re.compile(r"/company/"))
                    if not a:
                        continue
                    cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                    name = a.get_text(" ", strip=True)
                    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                    ipo_price = float(re.search(r"([\d.]+)", cells[3]).group(1)) if len(cells) > 3 and re.search(r"([\d.]+)", cells[3]) else None
                    cur_price = float(re.search(r"([\d.]+)", cells[4]).group(1)) if len(cells) > 4 and re.search(r"([\d.]+)", cells[4]) else None
                    chg = float(re.search(r"([+-]?[\d.]+)\s*%", cells[5]).group(1)) if len(cells) > 5 and re.search(r"([+-]?[\d.]+)\s*%", cells[5]) else (round((cur_price / ipo_price - 1) * 100, 2) if ipo_price and cur_price else None)

                    out["closed"].append({
                        "symbol": slug, "slug": slug, "name": name,
                        "date": cells[1] if len(cells) > 1 else "",
                        "open_date": None, "close_date": None,
                        "price_low": ipo_price, "price_high": ipo_price,
                        "listing_date_str": cells[1] if len(cells) > 1 else "",
                        "listing_price": cur_price,
                        "listing_gain_pct": chg,
                        "exchange": "NSE/BSE",
                        "detail_url": "https://www.screener.in" + a["href"],
                        "source": "screener_recent",
                    })
    except Exception:
        pass

    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _screener_company_lookup(name: str, screener_url: str = None) -> dict:
    result = {}
    H = _IPO_HEADERS
    try:
        link = screener_url
        if not link and name:
            q = requests.utils.quote(name)
            r = requests.get(f"https://www.screener.in/search/?q={q}", headers=H, timeout=12)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    if a["href"].startswith("/company/"):
                        link = "https://www.screener.in" + a["href"].split("?")[0]
                        break
        if not link:
            return result
        if not link.startswith("http"):
            link = "https://www.screener.in" + link
        result["screener_url"] = link
        r2 = requests.get(link, headers=H, timeout=20)
        if r2.status_code != 200:
            return result
        soup2 = BeautifulSoup(r2.text, "html.parser")
        about_el = soup2.select_one(".company-info, #top .about, .about")
        if about_el:
            result["about_screener"] = about_el.get_text(" ", strip=True)[:1200]
        else:
            for p in soup2.find_all("p"):
                t = p.get_text(" ", strip=True)
                if len(t) > 80 and ("incorporated" in t.lower() or "business" in t.lower() or "Ltd" in t):
                    result["about_screener"] = t[:1200]
                    break
        financials = []
        for t in soup2.find_all("table"):
            rows = t.find_all("tr")
            if not rows:
                continue
            hdrs = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
            if not any(re.search(r"Mar|Jan|Dec|202[0-9]", h) for h in hdrs):
                continue
            years = hdrs[1:]
            sales_row = pat_row = None
            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if not cells:
                    continue
                label = cells[0].lower()
                if sales_row is None and ("sales" in label or "revenue" in label):
                    sales_row = cells[1:]
                if pat_row is None and (label.startswith("net profit") or label == "pat" or "net profit" in label):
                    pat_row = cells[1:]
            if sales_row:
                for i, y in enumerate(years):
                    if i >= len(sales_row):
                        break
                    rev = _parse_money_inr(sales_row[i])
                    pat = _parse_money_inr(pat_row[i]) if pat_row and i < len(pat_row) else None
                    financials.append({"year": y, "revenue_cr": rev, "pat_cr": pat, "eps": None})
                if financials:
                    break
        if financials:
            # screener returns columns newest→oldest; reverse to chronological (oldest first)
            financials = list(reversed(financials))
            result["financials"] = financials
            revs = [f["revenue_cr"] for f in financials if f.get("revenue_cr")]
            # FIX: CAGR = (newest / oldest) ^ (1/n) - 1  (revs[0]=oldest, revs[-1]=newest after reversal)
            if len(revs) >= 2 and revs[0] and revs[0] > 0:
                years_n = len(revs) - 1
                result["revenue_cagr"] = round(((revs[-1] / revs[0]) ** (1 / years_n) - 1) * 100, 2)
            pats = [f["pat_cr"] for f in financials if f.get("pat_cr") is not None]
            if pats:
                # pats[0]=oldest, pats[-1]=newest after reversal
                result["is_profitable_latest"] = pats[-1] > 0
                result["is_profitable_all"] = all(p > 0 for p in pats)
    except Exception:
        pass
    return result


def _ai_google_gmp(company_name: str) -> dict:
    result = {"gmp": None, "gmp_pct": None, "gmp_str": None, "gmp_source": None}
    if not company_name:
        return result
    headlines = fetch_google_news(f"{company_name} IPO GMP grey market premium")
    blob = " | ".join([h.get("title", "") for h in headlines])
    m = re.search(r"GMP[^\d₹]*₹?\s*([\d.]+)\s*(?:\(?\s*([+-]?[\d.]+)\s*%\s*\)?)?", blob, re.I)
    if m:
        try:
            result["gmp"] = float(m.group(1))
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                result["gmp_pct"] = float(m.group(2))
            result["gmp_str"] = f"GMP ₹{result['gmp']}" + (f" ({result['gmp_pct']:+.1f}%)" if result.get("gmp_pct") is not None else "")
            result["gmp_source"] = "google_news_headlines"
        except (TypeError, ValueError):
            pass
    return result


def _normalize_company_name(name):
    """Normalize a company name to a short dedup key.

    Strips common legal-entity suffixes and generic sector words, then truncates.
    Uses a MINIMUM length guard (≥4 chars after stripping) so that short names
    like 'AB Corp' and 'AB Ltd' do not collapse to the same 2-char key and
    falsely merge two completely different companies.
    """
    if not name:
        return ""
    n = name.lower()
    # Strip legal suffixes and very generic sector nouns
    for term in [
        "ltd", "limited", "private", "pvt", "co", "company",
        "corporation", "corp", "enterprises", "holdings", "group",
    ]:
        n = re.sub(rf"\b{term}\b", "", n)
    # Strip sector words only when the remaining key would still be ≥4 chars
    sector_words = [
        "food", "foods", "engg", "engineering", "medicare",
        "industries", "technologies", "tech", "solutions",
        "infra", "infrastructure", "labs", "pharmaceuticals",
        "pharma", "dairy",
    ]
    n_stripped = n
    for term in sector_words:
        candidate = re.sub(rf"\b{term}\b", "", n_stripped)
        cleaned = re.sub(r"[^a-z0-9]", "", candidate)
        if len(cleaned) >= 4:
            n_stripped = candidate
    key = re.sub(r"[^a-z0-9]", "", n_stripped)[:16]
    # If we stripped too much and ended up with <4 chars, fall back to a slug of the raw name
    if len(key) < 4:
        key = re.sub(r"[^a-z0-9]", "", name.lower())[:16]
    return key


def _merge_ipo_records(primary: list, secondary: list) -> list:
    by = {}
    for rec in primary:
        k = _normalize_company_name(rec.get("name") or rec.get("slug"))
        if k:
            by[k] = dict(rec)

    for rec in secondary:
        k = _normalize_company_name(rec.get("name") or rec.get("slug"))
        if not k:
            continue
        if k not in by:
            by[k] = dict(rec)
            continue

        base = by[k]
        for field in ("price_low", "price_high", "price_band_str", "lot_size", "min_investment",
                      "gmp", "gmp_pct", "gmp_str", "subscription_str", "issue_size_cr",
                      "issue_size_str", "detail_url", "open_date", "close_date", "date",
                      "listing_gain_pct", "listing_price", "listing_date_str"):
            if base.get(field) in (None, "", []) and rec.get(field) not in (None, "", []):
                base[field] = rec[field]
        # Prefer the source that had actual date information
        if base.get("open_date") is None and rec.get("open_date"):
            base["open_date"] = rec["open_date"]
        if base.get("close_date") is None and rec.get("close_date"):
            base["close_date"] = rec["close_date"]
        # Keep the status from whichever source is more trustworthy (non-blank wins)
        if not base.get("status") and rec.get("status"):
            base["status"] = rec["status"]
        by[k] = base

    return list(by.values())


def _deduplicate_list(items: list) -> list:
    unique = {}
    for item in items:
        name = item.get("name") or item.get("slug") or ""
        k = _normalize_company_name(name)
        if not k:
            k = re.sub(r"[^a-z0-9]", "", name.lower())[:16]
        if not k:
            continue
        if k not in unique:
            unique[k] = dict(item)
        else:
            base = unique[k]
            for field, val in item.items():
                if base.get(field) in (None, "", []) and val not in (None, "", []):
                    base[field] = val
    return list(unique.values())


def _bucket_ipo(ipo: dict) -> str:
    """Determine the correct tab for a single IPO record.

    Priority order (most reliable evidence first):
    1. screener_recent source or listing_gain_pct/listing_price → closed
    2. status == LISTED → closed
    3. Parsed close_date in the past → closed
    4. Parsed listing_date in the past → closed
    5. status == OPEN and close_date >= today → current
    6. Parsed open_date <= today <= close_date → current
    7. open_date == today and close_date unknown → current
    8. open_date in the past and close_date unknown but listing_date unknown → current
       (IPO opened but close/listing date not yet scraped — safer to show as current)
    9. status == UPCOMING or open_date in future → upcoming
    10. Default → upcoming
    """
    today = datetime.today().date()
    source = str(ipo.get("source") or "").lower()
    status = str(ipo.get("status") or "").upper()

    op_d  = _parse_date_flex(ipo.get("open_date"))
    cl_d  = _parse_date_flex(ipo.get("close_date"))
    lst_d = _parse_date_flex(ipo.get("listing_date_str"))

    # ── 1. Hard closed signals ─────────────────────────────────────────────
    if (
        "screener_recent" in source
        or ipo.get("listing_gain_pct") is not None
        or ipo.get("listing_price") is not None
        or status == "LISTED"
    ):
        return "closed"

    # ── 2. close_date in the past ─────────────────────────────────────────
    if cl_d and cl_d < today:
        return "closed"

    # ── 3. listing_date in the past (and no open subscription signal) ─────
    if lst_d and lst_d <= today and not (op_d and op_d <= today <= (cl_d or today)):
        return "closed"

    # ── 4. Explicitly open: status says OPEN and close not yet passed ──────
    if status == "OPEN":
        # Trust status=OPEN only if close_date hasn't passed (or is unknown)
        if cl_d is None or cl_d >= today:
            return "current"
        # close_date passed → closed, regardless of status string
        return "closed"

    # ── 5. Date-range says currently open ─────────────────────────────────
    if op_d and cl_d and op_d <= today <= cl_d:
        return "current"

    # ── 6. open_date is today but close_date unknown ──────────────────────
    if op_d and op_d == today and cl_d is None:
        return "current"

    # ── 7. open_date is in the past, close_date unknown, no listing signal
    #       Most likely still open; show as current rather than misclassifying upcoming
    if op_d and op_d < today and cl_d is None and lst_d is None:
        return "current"

    # ── 8. open_date is in the future → upcoming ──────────────────────────
    if op_d and op_d > today:
        return "upcoming"

    # ── 9. status hint for upcoming ───────────────────────────────────────
    if status in ("UPCOMING",):
        return "upcoming"

    # ── 10. Default: no date information → upcoming (TBA) ─────────────────
    return "upcoming"


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ipo_list_categorized() -> dict:
    scr = _scrape_screener_ipo_list()
    chitt = _scrape_chittorgarh_dashboard()
    im_current  = _scrape_ipomarket_list("/ipo/open")
    im_upcoming = _scrape_ipomarket_list("/ipo/upcoming")
    im_closed   = _scrape_ipomarket_list("/ipo/listed")

    master_pool: list = []
    for lst in [scr.get("current"), scr.get("upcoming"), scr.get("closed"),
                chitt, im_current, im_upcoming, im_closed]:
        if lst:
            master_pool = _merge_ipo_records(master_pool, lst)

    final_current, final_upcoming, final_closed = [], [], []
    for ipo in master_pool:
        b = _bucket_ipo(ipo)
        ipo["bucket"] = b
        if b == "current":
            final_current.append(ipo)
        elif b == "closed":
            # Tag listing status for display in the closed tab
            lst_d = _parse_date_flex(ipo.get("listing_date_str"))
            today = datetime.today().date()
            ipo["listing_status_override"] = (
                "NOT YET LISTED" if lst_d and lst_d > today else None
            )
            final_closed.append(ipo)
        else:
            final_upcoming.append(ipo)

    return {
        "current":  _deduplicate_list(final_current),
        "closed":   _deduplicate_list(final_closed)[:40],
        "upcoming": _deduplicate_list(final_upcoming),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources_note": "Date-evidence-first bucketing; status hints used only as tie-breakers.",
    }


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ipo_detail(slug: str, company_name: str = "") -> dict:
    detail = {
        "symbol": slug,
        "slug": slug,
        "name": company_name or slug.replace("-", " ").title(),
        "source": "ipomarket",
        "detail_url": f"https://www.ipomarket.in/ipo/{slug}",
    }
    url = detail["detail_url"]
    try:
        r = requests.get(url, headers=_IPO_HEADERS, timeout=20)
        if r.status_code != 200:
            detail["error"] = f"Detail page status {r.status_code}"
            return detail
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        detail["error"] = str(e)
        return detail

    kv = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        if all(len(tr.find_all(["td", "th"])) == 2 for tr in rows[: min(5, len(rows))]):
            for tr in rows:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) == 2:
                    kv[cells[0].strip().lower()] = cells[1].strip()
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["td", "th"])]
        if any("revenue" in h for h in headers) and any("fiscal" in h or "year" in h for h in headers):
            fin_rows = []
            for tr in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 3:
                    fin_rows.append({
                        "year": cells[0],
                        "revenue_cr": _parse_money_inr(cells[1]),
                        "pat_cr": _parse_money_inr(cells[2]) if len(cells) > 2 else None,
                        "eps": cells[3] if len(cells) > 3 else None,
                        "raw": cells,
                    })
            if fin_rows:
                detail["financials"] = fin_rows

    def g(*keys):
        for k in keys:
            for hk, hv in kv.items():
                if k in hk:
                    return hv
        return None

    detail["issue_size_str"]   = g("issue size")
    detail["fresh_issue_str"]  = g("fresh issue", "fresh capital")
    detail["ofs_str"]          = g("ofs", "offer for sale")
    detail["face_value"]       = g("face value")
    detail["exchange"]         = g("exchange") or "NSE/BSE"
    detail["registrar"]        = g("registrar")
    detail["isin"]             = g("isin")
    detail["lot_size"]         = g("lot size")
    detail["issue_price"]      = g("issue price", "final price")
    detail["price_band_str"]   = g("price band")
    detail["listing_date_str"] = g("listing", "listed on")
    detail["open_date_str"]    = g("ipo open", "open")
    detail["close_date_str"]   = g("ipo close", "close")

    fresh = detail.get("fresh_issue_str") or ""
    ofs   = detail.get("ofs_str") or ""
    if fresh and ofs and "—" not in fresh and "—" not in ofs:
        detail["offer_type"] = f"Fresh Issue ({fresh}) + OFS ({ofs})"
    elif fresh and "—" not in fresh and fresh not in ("", "0"):
        detail["offer_type"] = f"Fresh Issue ({fresh})"
    elif ofs and "—" not in ofs:
        detail["offer_type"] = f"Offer for Sale ({ofs})"
    else:
        detail["offer_type"] = "See RHP / issue documents"

    full_text = soup.get_text("\n", strip=True)
    about = ""
    for marker in ("About\n", "About the Company", "About "):
        idx = full_text.find(marker)
        if idx >= 0:
            chunk = full_text[idx: idx + 800]
            about = re.sub(r"^About[^\n]*\n?", "", chunk).strip()
            about = about.split("Strengths")[0].split("Risk")[0].strip()
            break
    detail["about"] = about or detail.get("longBusinessSummary") or "Business description not available from aggregator."

    strengths, risks = [], []
    for line in full_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(x in low for x in ["risk", "threat", "litigation", "contingent", "adversely", "dependent", "concentration", "competition", "regulatory", "cyclical", "debt", "loss"]):
            if len(item := line.lstrip("•- *").strip()) > 15 and item not in risks and len(risks) < 8:
                risks.append(item)
        elif any(x in low for x in ["strong", "leading", "leader", "profitable", "scalable", "growth", "brand", "network", "advantage"]):
            if len(item := line.lstrip("•- *").strip()) > 15 and item not in strengths and len(strengths) < 8:
                strengths.append(item)
    detail["strengths"] = strengths
    detail["risks"]     = risks

    m = re.search(r"GMP\s*₹\s*([\d.]+)\s*\(\s*([+-]?[\d.]+)\s*%", full_text)
    if m:
        detail["gmp"]     = float(m.group(1))
        detail["gmp_pct"] = float(m.group(2))
    m = re.search(r"(?:Total|Overall).*?([\d.]+)\s*x", full_text, re.I)
    if m:
        detail["subscription_total"] = float(m.group(1))
    for cat, key in [("QIB", "subscription_qib"), ("NII", "subscription_nii"),
                     ("Retail", "subscription_retail"), ("RII", "subscription_retail")]:
        m = re.search(rf"{cat}[^\d]{{0,20}}([\d.]+)\s*x", full_text, re.I)
        if m:
            detail[key] = float(m.group(1))

    m = re.search(r"listing\s*(?:gain|return|pop)?[^\d%]*([+-]?[\d.]+)\s*%", full_text, re.I)
    if m:
        detail["listing_gain_pct"] = float(m.group(1))
    m = re.search(r"Close Price on Listing[^\d]*([\d.]+)", full_text, re.I)
    if m:
        detail["listing_price"] = float(m.group(1))

    # ── Financial computations (ipomarket financials are newest→oldest) ────
    fins = detail.get("financials") or []
    # ipomarket tables typically show rows newest-first; reverse to chronological
    if fins:
        fins = list(reversed(fins))
        detail["financials"] = fins
    revs = [f["revenue_cr"] for f in fins if f.get("revenue_cr")]
    pats = [f["pat_cr"]     for f in fins if f.get("pat_cr") is not None]
    # FIX: (newest / oldest) ^ (1/n) - 1  — revs[0]=oldest, revs[-1]=newest
    if len(revs) >= 2 and revs[0] and revs[0] > 0:
        years = len(revs) - 1
        try:
            detail["revenue_cagr"] = round(((revs[-1] / revs[0]) ** (1 / years) - 1) * 100, 2)
        except Exception:
            detail["revenue_cagr"] = None
    else:
        detail["revenue_cagr"] = None
    if pats:
        detail["is_profitable_latest"] = pats[-1] is not None and pats[-1] > 0
        detail["is_profitable_all"]    = all(p is not None and p > 0 for p in pats)
    else:
        detail["is_profitable_latest"] = None
        detail["is_profitable_all"]    = None

    detail["ipo_news"] = fetch_google_news(f"{detail['name']} IPO GMP subscription 2026")

    if detail.get("gmp") is None and detail.get("gmp_pct") is None:
        gmp_info = _ai_google_gmp(detail.get("name") or "")
        for k, v in gmp_info.items():
            if v is not None and detail.get(k) is None:
                detail[k] = v

    scr_url = detail.get("screener_url")
    scr = _screener_company_lookup(detail.get("name") or "", screener_url=scr_url)
    if scr.get("screener_url"):
        detail["screener_url"] = scr["screener_url"]
    if scr.get("about_screener"):
        if (not detail.get("about") or detail.get("about", "").startswith("Business description")
                or len(detail.get("about", "")) < 60):
            detail["about"] = scr["about_screener"]
    if scr.get("financials") and (not detail.get("financials") or len(detail.get("financials") or []) < 2):
        detail["financials"] = scr["financials"]
    if scr.get("revenue_cagr") is not None and detail.get("revenue_cagr") is None:
        detail["revenue_cagr"] = scr["revenue_cagr"]
    if scr.get("is_profitable_latest") is not None and detail.get("is_profitable_latest") is None:
        detail["is_profitable_latest"] = scr["is_profitable_latest"]
    if scr.get("is_profitable_all") is not None and detail.get("is_profitable_all") is None:
        detail["is_profitable_all"] = scr["is_profitable_all"]

    return detail


def score_ipo(detail: dict, bucket: str = "current") -> tuple:
    if bucket != "current":
        return None, None, [], []

    pros, cons, score = [], [], 50

    cagr = detail.get("revenue_cagr")
    if cagr is not None:
        if cagr > 20:
            pros.append(f"Strong revenue CAGR of {cagr:.1f}%"); score += 10
        elif cagr > 10:
            pros.append(f"Decent revenue CAGR of {cagr:.1f}%"); score += 5
        elif cagr < 0:
            cons.append(f"Revenue contraction ({cagr:.1f}% CAGR)"); score -= 10
        else:
            cons.append(f"Slow revenue growth ({cagr:.1f}% CAGR)"); score -= 5

    if detail.get("is_profitable_all") is True:
        pros.append("Profitable across all reported years"); score += 10
    elif detail.get("is_profitable_latest") is True:
        pros.append("Profitable in latest reported year"); score += 4
    elif detail.get("is_profitable_latest") is False:
        cons.append("Latest reported year was loss-making"); score -= 12

    gmp_pct = detail.get("gmp_pct")
    if gmp_pct is not None:
        if gmp_pct >= 20:
            pros.append(f"Market sentiment: elevated unofficial GMP ({gmp_pct:.1f}%)"); score += 4
        elif gmp_pct >= 5:
            pros.append(f"Market sentiment: positive unofficial GMP ({gmp_pct:.1f}%)"); score += 2
        elif gmp_pct < 0:
            cons.append(f"Market sentiment: negative unofficial GMP ({gmp_pct:.1f}%)"); score -= 5

    sub = detail.get("subscription_total")
    if sub is not None:
        if sub >= 10:
            pros.append(f"Strong subscription ({sub:.1f}x)"); score += 8
        elif sub >= 1:
            pros.append(f"Subscribed ({sub:.1f}x)"); score += 3
        else:
            cons.append(f"Weak subscription ({sub:.1f}x)"); score -= 12

    score = int(min(max(score, 0), 100))
    verdict = "BUY" if score >= 60 else "ABSTAIN"
    return score, verdict, pros, cons


# ── Chart helpers ──────────────────────────────────────────────────────────────

def render_ipo_financials_chart(fin_rows: list) -> None:
    """Grouped bar chart: Revenue vs PAT across years."""
    if not fin_rows:
        return
    years = [f.get("year", "") for f in fin_rows]
    revs  = [f.get("revenue_cr") or 0 for f in fin_rows]
    pats  = [f.get("pat_cr")     or 0 for f in fin_rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=revs, name="Revenue (₹ Cr)", marker_color=BLUE,
                         text=[f"₹{v:,.0f}" for v in revs], textposition="outside"))
    fig.add_trace(go.Bar(x=years, y=pats, name="PAT (₹ Cr)", marker_color=GREEN,
                         text=[f"₹{v:,.0f}" for v in pats], textposition="outside"))
    fig.update_layout(
        title="Revenue & PAT Trend (₹ Cr)",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=300, margin=dict(t=40, b=20, l=10, r=10),
        barmode="group", legend=dict(orientation="h", y=-0.25),
        yaxis=dict(gridcolor="#2d333b"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_margin_chart(fin_rows: list) -> None:
    """Line chart: Net margin trend across years."""
    rows_with_both = [
        f for f in fin_rows
        if f.get("revenue_cr") and f.get("pat_cr") is not None and f["revenue_cr"] > 0
    ]
    if len(rows_with_both) < 2:
        return
    years   = [f["year"] for f in rows_with_both]
    margins = [round(f["pat_cr"] / f["revenue_cr"] * 100, 2) for f in rows_with_both]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=margins, mode="lines+markers+text",
        name="Net Margin %",
        line=dict(color=ORANGE, width=2),
        marker=dict(size=8),
        text=[f"{m:.1f}%" for m in margins],
        textposition="top center",
    ))
    fig.update_layout(
        title="Net Margin Trend (%)",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=250, margin=dict(t=40, b=20, l=10, r=10),
        yaxis=dict(ticksuffix="%", gridcolor="#2d333b"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_revenue_growth_chart(fin_rows: list) -> None:
    """Bar chart: YoY revenue growth %."""
    revs_valid = [(f["year"], f["revenue_cr"]) for f in fin_rows if f.get("revenue_cr")]
    if len(revs_valid) < 2:
        return
    growth_years  = [revs_valid[i][0] for i in range(1, len(revs_valid))]
    growth_values = [
        round((revs_valid[i][1] / revs_valid[i - 1][1] - 1) * 100, 1)
        for i in range(1, len(revs_valid))
        if revs_valid[i - 1][1] > 0
    ]
    if not growth_values:
        return
    colors = [GREEN if g >= 0 else RED for g in growth_values]
    fig = go.Figure(go.Bar(
        x=growth_years, y=growth_values,
        marker_color=colors,
        text=[f"{g:+.1f}%" for g in growth_values],
        textposition="outside",
    ))
    fig.update_layout(
        title="YoY Revenue Growth (%)",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=250, margin=dict(t=40, b=20, l=10, r=10),
        yaxis=dict(ticksuffix="%", gridcolor="#2d333b"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_subscription_chart(detail: dict) -> None:
    """Horizontal bar chart: QIB / NII / Retail subscription multiples."""
    cats = {
        "QIB":    detail.get("subscription_qib"),
        "NII/HNI": detail.get("subscription_nii"),
        "Retail": detail.get("subscription_retail"),
    }
    cats = {k: v for k, v in cats.items() if v is not None}
    if not cats:
        return
    labels = list(cats.keys())
    values = list(cats.values())
    colors = [GREEN if v >= 1 else RED for v in values]
    fig = go.Figure(go.Bar(
        y=labels, x=values, orientation="h",
        marker_color=colors,
        text=[f"{v:.1f}x" for v in values],
        textposition="outside",
    ))
    fig.add_vline(x=1, line_dash="dot", line_color=MUTED, annotation_text="1x (fully subscribed)")
    fig.update_layout(
        title="Subscription by Category",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=220, margin=dict(t=40, b=20, l=10, r=10),
        xaxis=dict(ticksuffix="x", gridcolor="#2d333b"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_offer_structure_chart(detail: dict) -> None:
    """Donut chart: Fresh Issue vs OFS split."""
    fresh_str = detail.get("fresh_issue_str") or ""
    ofs_str   = detail.get("ofs_str") or ""
    fresh_val = _parse_money_inr(fresh_str)
    ofs_val   = _parse_money_inr(ofs_str)
    if not fresh_val and not ofs_val:
        return
    labels = []
    values = []
    colors_list = []
    if fresh_val:
        labels.append("Fresh Issue"); values.append(fresh_val); colors_list.append(BLUE)
    if ofs_val:
        labels.append("OFS");          values.append(ofs_val);   colors_list.append(ORANGE)
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=colors_list),
        hole=0.55,
        textinfo="label+percent",
    ))
    fig.update_layout(
        title="Offer Structure (₹ Cr)",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=250, margin=dict(t=40, b=0, l=0, r=0),
        showlegend=True, legend=dict(orientation="h", y=-0.1),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── List + Detail renderers ────────────────────────────────────────────────────

def _render_ipo_list_rows(ipos, bucket, currency="₹"):
    if not ipos:
        st.info(f"No {bucket} IPOs found from live sources right now.")
        return
    for idx, ipo in enumerate(ipos):
        sym  = ipo.get("slug") or ipo.get("symbol") or f"ipo_{idx}"
        name = ipo.get("name", "Unknown")
        band = ipo.get("price_band_str") or (
            f"{currency}{ipo['price_low']} – {currency}{ipo['price_high']}"
            if ipo.get("price_low") is not None else "Price TBA"
        )

        if bucket == "upcoming":
            cols = st.columns([4, 2])
            with cols[0]:
                st.markdown(
                    f"<b>{html_escape(name)}</b><br>"
                    f"<span style='color:{MUTED};font-size:0.8em;'>"
                    f"{html_escape(str(sym))} · {html_escape(str(ipo.get('exchange','NSE/BSE')))}</span>"
                    f"<br><span style='color:{MUTED};font-size:0.78em;'>Price Band: {html_escape(str(band))}</span>",
                    unsafe_allow_html=True)
            with cols[1]:
                date_val = ipo.get("date") or ipo.get("open_date") or "TBA"
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Opening Date</span><br>"
                    f"<b>{html_escape(str(date_val))}</b>",
                    unsafe_allow_html=True)
            st.markdown(f"<hr style='border:0;border-top:1px solid {BORDER};margin:6px 0;'>", unsafe_allow_html=True)
            continue

        cols = st.columns([3.2, 1.4, 1.6, 1.2, 1.2])
        with cols[0]:
            st.markdown(
                f"<b>{html_escape(name)}</b><br>"
                f"<span style='color:{MUTED};font-size:0.8em;'>"
                f"{html_escape(str(sym))} · {html_escape(str(ipo.get('exchange','')))}</span>",
                unsafe_allow_html=True)
        with cols[1]:
            if bucket == "closed":
                date_label = "Listing Date"
                date_val   = ipo.get("listing_date_str") or ipo.get("date") or "Closed"
            else:
                date_label = "Open → Close"
                open_v  = ipo.get("date") or ipo.get("open_date") or "TBA"
                close_v = ipo.get("close_date") or ""
                date_val = f"{open_v} → {close_v}" if close_v else open_v
            st.markdown(
                f"<span style='font-size:0.75em;color:{MUTED};'>{date_label}</span><br>"
                f"<b>{html_escape(str(date_val))}</b>",
                unsafe_allow_html=True)
        with cols[2]:
            st.markdown(
                f"<span style='font-size:0.75em;color:{MUTED};'>Price Band</span><br>"
                f"<b>{html_escape(str(band))}</b>",
                unsafe_allow_html=True)
        with cols[3]:
            if bucket == "current" and ipo.get("gmp_str"):
                gmp_color = GREEN if (ipo.get("gmp_pct") or 0) >= 0 else RED
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>GMP</span><br>"
                    f"<b style='color:{gmp_color};'>{html_escape(str(ipo.get('gmp_str')))}</b>",
                    unsafe_allow_html=True)
            elif bucket == "closed":
                override = ipo.get("listing_status_override")
                gain     = ipo.get("listing_gain_pct")
                if override == "NOT YET LISTED":
                    gain_str, gain_color = "NOT YET LISTED", MUTED
                elif gain is not None:
                    gain_str  = f"{gain:+.1f}%"
                    gain_color = GREEN if gain >= 0 else RED
                else:
                    gain_str, gain_color = "Listed (Gain N/A)", MUTED
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Listing Gain</span><br>"
                    f"<b style='color:{gain_color};'>{html_escape(gain_str)}</b>",
                    unsafe_allow_html=True)
            else:
                sub = ipo.get("subscription_total")
                sub_str = f"{sub:.1f}x" if sub is not None else "Ongoing"
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Subscription</span><br>"
                    f"<b>{html_escape(sub_str)}</b>",
                    unsafe_allow_html=True)
        with cols[4]:
            if st.button("Analyse →", key=f"ipo_{bucket}_{sym}_{idx}", use_container_width=True):
                with st.spinner(f"Loading {name}..."):
                    st.session_state.selected_ipo = sym
                    st.session_state.ipo_bucket   = bucket
                    st.session_state.ipo_detail   = fetch_ipo_detail(sym, name)
                    d = st.session_state.ipo_detail
                    for k in ("gmp", "gmp_pct", "gmp_str", "price_low", "price_high", "lot_size",
                              "min_investment", "subscription_str", "subscription_total",
                              "issue_size_cr", "issue_size_str", "screener_url", "listing_date_str",
                              "listing_gain_pct", "listing_price"):
                        if d.get(k) is None and ipo.get(k) is not None:
                            d[k] = ipo[k]
                    st.session_state.ipo_detail = d
                st.rerun()
        st.markdown(f"<hr style='border:0;border-top:1px solid {BORDER};margin:6px 0;'>",
                    unsafe_allow_html=True)


def _render_ipo_detail_view():
    detail = st.session_state.ipo_detail or {}
    bucket = st.session_state.get("ipo_bucket") or "current"
    sym    = detail.get("slug") or detail.get("symbol") or ""
    name   = detail.get("name", sym)
    score, verdict, pros, cons = score_ipo(detail, bucket=bucket)
    vc = rating_color(verdict) if verdict else MUTED

    if st.button("← Back to IPO List"):
        st.session_state.selected_ipo = None
        st.session_state.ipo_detail   = None
        st.session_state.ipo_bucket   = None
        st.rerun()

    right = ""
    if bucket == "current" and verdict:
        right = (f"<div style='font-size:2em;font-weight:900;color:{vc};'>{verdict}</div>"
                 f"<div style='color:{MUTED};font-size:0.85em;'>Score: {score}/100</div>")
    elif bucket == "closed":
        gain = detail.get("listing_gain_pct")
        if gain is not None:
            gain_color = GREEN if gain >= 0 else RED
            right = (f"<div style='font-size:1.4em;font-weight:800;color:{gain_color};'>"
                     f"Listing {gain:+.1f}%</div>")
        else:
            right = (f"<div style='color:{MUTED};'>Listing: "
                     f"{html_escape(str(detail.get('listing_date_str') or 'Pending'))}</div>")
    else:
        right = f"<div style='color:{ORANGE};font-weight:700;'>UPCOMING</div>"

    st.markdown(f"""
    <div class="swf-card" style="margin-bottom:18px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
          <div style="color:{MUTED};font-size:0.85em;">IPO Research · {bucket.upper()}</div>
          <div style="font-size:1.5em;font-weight:800;">{html_escape(name)}</div>
          <div style="color:{MUTED};font-size:0.9em;">
            {html_escape(str(sym))} · {html_escape(str(detail.get('exchange','NSE/BSE')))}
          </div>
        </div>
        <div style="text-align:right;">{right}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Key metrics row ────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        custom_metric("Issue Size", detail.get("issue_size_str") or "N/A")
    with m2:
        custom_metric("Price Band", detail.get("price_band_str") or "N/A")
    with m3:
        if bucket == "upcoming":
            custom_metric("Lot Size", detail.get("lot_size") or "TBA")
        else:
            custom_metric("GMP",
                f"₹{detail['gmp']} ({detail.get('gmp_pct'):+.1f}%)"
                if detail.get("gmp") is not None and detail.get("gmp_pct") is not None
                else (detail.get("gmp_str") or "N/A"))
    with m4:
        if bucket == "upcoming":
            custom_metric("Revenue CAGR",
                f"{detail['revenue_cagr']}%" if detail.get("revenue_cagr") is not None else "N/A")
        else:
            sub = detail.get("subscription_total")
            custom_metric("Subscription",
                f"{sub:.2f}x" if sub is not None else (detail.get("subscription_str") or "N/A"))

    # ── Offer structure ────────────────────────────────────────────────────
    extra_cols = st.columns(3)
    with extra_cols[0]:
        custom_metric("Offer Type", detail.get("offer_type") or "N/A")
    with extra_cols[1]:
        custom_metric("Face Value", detail.get("face_value") or "N/A")
    with extra_cols[2]:
        custom_metric("Registrar", detail.get("registrar") or "N/A")

    # ── Business overview ──────────────────────────────────────────────────
    card("Business Overview",
         f"<p style='color:#c9d1d9;font-size:0.9em;line-height:1.6;'>"
         f"{html_escape(str(detail.get('about') or 'Not available.'))}</p>")

    # ── Financial charts ───────────────────────────────────────────────────
    fins = detail.get("financials") or []
    if fins:
        st.markdown("##### 📊 Financial Performance")
        ch1, ch2 = st.columns(2)
        with ch1:
            render_ipo_financials_chart(fins)
        with ch2:
            render_ipo_margin_chart(fins)

        st.markdown("##### 📈 Revenue Growth & Offer Structure")
        ch3, ch4 = st.columns(2)
        with ch3:
            render_ipo_revenue_growth_chart(fins)
        with ch4:
            render_ipo_offer_structure_chart(detail)

        # ── Subscription breakdown ─────────────────────────────────────────
        if detail.get("subscription_qib") is not None or detail.get("subscription_nii") is not None or detail.get("subscription_retail") is not None:
            st.markdown("##### 📋 Subscription Breakdown")
            sub_col, _ = st.columns([2, 1])
            with sub_col:
                render_ipo_subscription_chart(detail)

        # ── Category subscription metrics ──────────────────────────────────
        if bucket != "upcoming" and any(detail.get(k) is not None for k in ("subscription_qib", "subscription_nii", "subscription_retail")):
            s1, s2, s3 = st.columns(3)
            with s1:
                custom_metric("QIB", f"{detail['subscription_qib']:.2f}x" if detail.get("subscription_qib") is not None else "N/A")
            with s2:
                custom_metric("NII", f"{detail['subscription_nii']:.2f}x" if detail.get("subscription_nii") is not None else "N/A")
            with s3:
                custom_metric("Retail", f"{detail['subscription_retail']:.2f}x" if detail.get("subscription_retail") is not None else "N/A")

        # ── RHP financial table ────────────────────────────────────────────
        st.markdown("##### RHP Financial Highlights")
        rows = []
        for f in fins:
            rev = f.get("revenue_cr")
            pat = f.get("pat_cr")
            margin = (f"{round(pat/rev*100,1)}%" if rev and rev > 0 and pat is not None else "—")
            rows.append({
                "Year":             f.get("year") or "—",
                "Revenue (₹ Cr)":  f"{rev:,.1f}" if rev is not None else "—",
                "PAT (₹ Cr)":      f"{pat:,.1f}" if pat is not None else "—",
                "Net Margin":       margin,
                "Historical EPS":  f.get("eps") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # ── Strengths & Risks ──────────────────────────────────────────────────
    pc1, pc2 = st.columns(2)
    with pc1:
        p_html = "".join(
            f"<div style='padding:4px 0'><span style='color:{GREEN}'>✅ {html_escape(p)}</span></div>"
            for p in (pros or detail.get("strengths") or [])[:8]
        ) or f"<div style='color:{MUTED}'>No strengths extracted.</div>"
        card("Strengths", p_html)
    with pc2:
        c_html = "".join(
            f"<div style='padding:4px 0'><span style='color:{RED}'>⚠️ {html_escape(c)}</span></div>"
            for c in (cons or detail.get("risks") or [])[:8]
        ) or f"<div style='color:{MUTED}'>No material risks extracted.</div>"
        card("Risks & Concerns", c_html)

    # ── AI Research Note ───────────────────────────────────────────────────
    with st.spinner("Generating AI note..."):
        narr = ipo_ai_narrative(detail, score, verdict, pros, cons, bucket=bucket)
    card("AI Research Note",
         f"<p style='color:#c9d1d9;font-size:0.9em;line-height:1.6;white-space:pre-wrap;'>"
         f"{style_verdict_text(narr)}</p>")
