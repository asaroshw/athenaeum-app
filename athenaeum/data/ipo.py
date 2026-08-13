"""Indian IPO data: Screener primary, Chittorgarh, ipomarket, AI GMP."""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta
import requests
import streamlit as st
from bs4 import BeautifulSoup
import plotly.graph_objects as go

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
            status = col(["status"], "OPEN" if "open" in path else ("LISTED" if "listed" in path else "UPCOMING"))
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
                "status": "CURRENT", "date": "", "open_date": None, "close_date": None,
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
        period = period.replace("th", "").replace("st", "").replace("nd", "").replace("rd", "")
        parts = re.split(r"\s*[-–]\s*", period)
        today = datetime.today().date()
        year = today.year

        def _with_year(piece):
            piece = piece.strip()
            if re.search(r"\d{4}", piece):
                return _parse_date_flex(piece)
            return _parse_date_flex(f"{piece} {year}")

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
            financials = list(reversed(financials))
            result["financials"] = financials
            revs = [f["revenue_cr"] for f in financials if f.get("revenue_cr")]
            if len(revs) >= 2 and revs[-1] and revs[-1] > 0:
                years_n = len(revs) - 1
                result["revenue_cagr"] = round(((revs[0] / revs[-1]) ** (1 / years_n) - 1) * 100, 2)
            pats = [f["pat_cr"] for f in financials if f.get("pat_cr") is not None]
            if pats:
                result["is_profitable_latest"] = pats[0] > 0
                result["is_profitable_all"] = all(p > 0 for p in pats[:3])
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
    if not name:
        return ""
    n = name.lower()
    for term in [
        "ltd", "limited", "food", "foods", "engg", "engineering", "private", "pvt", 
        "co", "company", "corporation", "corp", "medicare", "enterprises", 
        "industries", "technologies", "tech", "solutions", "infra", "infrastructure",
        "group", "holdings", "labs", "pharmaceuticals", "pharma", "dairy"
    ]:
        n = re.sub(rf"\b{term}\b", "", n)
    return re.sub(r"[^a-z0-9]", "", n)[:12]


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
        by[k] = base
        
    return list(by.values())


def _deduplicate_list(items: list) -> list:
    unique = {}
    for item in items:
        name = item.get("name") or item.get("slug") or ""
        k = _normalize_company_name(name)
        if not k:
            k = re.sub(r"[^a-z0-9]", "", name.lower())[:12]
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


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ipo_list_categorized() -> dict:
    scr = _scrape_screener_ipo_list()
    chitt = _scrape_chittorgarh_dashboard()
    im_current = _scrape_ipomarket_list("/ipo/open")
    im_upcoming = _scrape_ipomarket_list("/ipo/upcoming")
    im_closed = _scrape_ipomarket_list("/ipo/listed")

    master_pool = []
    for lst in [scr.get("current"), scr.get("upcoming"), scr.get("closed"), chitt, im_current, im_upcoming, im_closed]:
        if lst:
            master_pool = _merge_ipo_records(master_pool, lst)

    today = datetime.today().date()
    final_current, final_upcoming, final_closed = [], [], []

    for ipo in master_pool:
        status_lower = str(ipo.get("status", "")).lower()
        source_lower = str(ipo.get("source", "")).lower()
        date_str = str(ipo.get("date") or ipo.get("open_date") or ipo.get("listing_date_str") or "").lower()

        has_active_market_price = (
            ipo.get("listing_gain_pct") is not None or 
            ipo.get("listing_price") is not None or 
            ipo.get("current_price") is not None or
            "screener_recent" in source_lower or
            "listed" in status_lower
        )

        op_d = _parse_date_flex(ipo.get("open_date") or ipo.get("date"))
        cl_d = _parse_date_flex(ipo.get("close_date"))
        lst_d = _parse_date_flex(ipo.get("listing_date_str") or ipo.get("date"))

        future_keywords = ["aug", "sep", "oct", "nov", "dec", "before", "targeted", "est", "target", "tba", "upcoming"]
        is_future_text = any(k in date_str for k in future_keywords) and ("2026" in date_str or "2027" in date_str)

        if has_active_market_price and not is_future_text:
            ipo["bucket"] = "closed"
            ipo["listing_status_override"] = None
            final_closed.append(ipo)
            continue

        if is_future_text or (op_d and today < op_d):
            ipo["bucket"] = "upcoming"
            final_upcoming.append(ipo)
            continue

        if op_d and cl_d and op_d <= today <= cl_d:
            ipo["bucket"] = "current"
            final_current.append(ipo)
            continue
        if op_d and not cl_d and op_d == today:
            ipo["bucket"] = "current"
            final_current.append(ipo)
            continue

        if (cl_d and cl_d < today) or (lst_d and lst_d < today):
            ipo["bucket"] = "closed"
            if lst_d and today < lst_d:
                ipo["listing_status_override"] = "NOT YET LISTED"
            else:
                ipo["listing_status_override"] = None
            final_closed.append(ipo)
        else:
            ipo["bucket"] = "upcoming"
            final_upcoming.append(ipo)

    return {
        "current": _deduplicate_list(final_current),
        "closed": _deduplicate_list(final_closed)[:40],
        "upcoming": _deduplicate_list(final_upcoming),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources_note": "Strict lifecycle sorted IPO pools with active market price guard.",
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

    detail["issue_size_str"] = g("issue size")
    detail["fresh_issue_str"] = g("fresh issue", "fresh capital")
    detail["ofs_str"] = g("ofs", "offer for sale")
    detail["face_value"] = g("face value")
    detail["exchange"] = g("exchange") or "NSE/BSE"
    detail["registrar"] = g("registrar")
    detail["isin"] = g("isin")
    detail["lot_size"] = g("lot size")
    detail["issue_price"] = g("issue price", "final price")
    detail["price_band_str"] = g("price band")
    detail["listing_date_str"] = g("listing", "listed on")
    detail["open_date_str"] = g("ipo open", "open")
    detail["close_date_str"] = g("ipo close", "close")

    fresh = detail.get("fresh_issue_str") or ""
    ofs = detail.get("ofs_str") or ""
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
    detail["risks"] = risks

    m = re.search(r"GMP\s*₹\s*([\d.]+)\s*\(\s*([+-]?[\d.]+)\s*%", full_text)
    if m:
        detail["gmp"] = float(m.group(1))
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

    fins = detail.get("financials") or []
    revs = [f["revenue_cr"] for f in fins if f.get("revenue_cr")]
    pats = [f["pat_cr"] for f in fins if f.get("pat_cr") is not None]
    if len(revs) >= 2 and revs[-1] and revs[-1] > 0:
        years = len(revs) - 1
        try:
            detail["revenue_cagr"] = round(((revs[0] / revs[-1]) ** (1 / years) - 1) * 100, 2)
        except Exception:
            detail["revenue_cagr"] = None
    else:
        detail["revenue_cagr"] = None
    if pats:
        detail["is_profitable_latest"] = pats[0] is not None and pats[0] > 0
        detail["is_profitable_all"] = all(p is not None and p > 0 for p in pats[:3])
    else:
        detail["is_profitable_latest"] = None
        detail["is_profitable_all"] = None

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
        pros.append("Profitable across reported years"); score += 10
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


def render_ipo_financials_chart(fin_rows):
    if not fin_rows:
        return
    years = [f.get("year", "") for f in fin_rows]
    revs = [f.get("revenue_cr") or 0 for f in fin_rows]
    pats = [f.get("pat_cr") or 0 for f in fin_rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=revs, name="Total Revenue (₹ Cr)", marker_color=BLUE))
    fig.add_trace(go.Bar(x=years, y=pats, name="Net Profit / PAT (₹ Cr)", marker_color=GREEN))
    
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        height=280,
        margin=dict(t=20, b=20, l=10, r=10),
        barmode="group",
        legend=dict(orientation="h", y=-0.2)
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_ipo_list_rows(ipos, bucket, currency="₹"):
    if not ipos:
        st.info(f"No {bucket} IPOs found from live sources right now.")
        return
    for idx, ipo in enumerate(ipos):
        sym = ipo.get("slug") or ipo.get("symbol") or f"ipo_{idx}"
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
                    f"{html_escape(str(sym))} · {html_escape(str(ipo.get('exchange','NSE/BSE')))}</span>",
                    unsafe_allow_html=True)
            with cols[1]:
                date_val = ipo.get('date') or ipo.get('open_date') or 'TBA'
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
                date_val = ipo.get('listing_date_str') or ipo.get('date') or 'Closed'
            else:
                date_label = "Open"
                date_val = ipo.get('date') or ipo.get('open_date') or 'TBA'
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
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>GMP</span><br>"
                    f"<b style='color:{GREEN};'>{html_escape(str(ipo.get('gmp_str')))}</b>",
                    unsafe_allow_html=True)
            elif bucket == "closed":
                override = ipo.get("listing_status_override")
                gain = ipo.get("listing_gain_pct")
                
                if override == "NOT YET LISTED":
                    gain_str = "NOT YET LISTED"
                    gain_color = MUTED
                elif gain is not None:
                    gain_str = f"{gain:+.1f}%"
                    gain_color = GREEN if gain >= 0 else RED
                else:
                    gain_str = "Listed (Gain N/A)"
                    gain_color = MUTED
                    
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Listing Gain</span><br>"
                    f"<b style='color:{gain_color};'>{html_escape(gain_str)}</b>",
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Status</span><br>"
                    f"<b>Active</b>",
                    unsafe_allow_html=True)
        with cols[4]:
            if st.button("Analyse →", key=f"ipo_{bucket}_{sym}_{idx}", use_container_width=True):
                with st.spinner(f"Loading {name}..."):
                    st.session_state.selected_ipo = sym
                    st.session_state.ipo_bucket = bucket
                    st.session_state.ipo_detail = fetch_ipo_detail(sym, name)
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
    sym = detail.get("slug") or detail.get("symbol") or ""
    name = detail.get("name", sym)
    score, verdict, pros, cons = score_ipo(detail, bucket=bucket)
    vc = rating_color(verdict) if verdict else MUTED

    if st.button("← Back to IPO List"):
        st.session_state.selected_ipo = None
        st.session_state.ipo_detail = None
        st.session_state.ipo_bucket = None
        st.rerun()

    right = ""
    if bucket == "current" and verdict:
        right = f"<div style='font-size:2em;font-weight:900;color:{vc};'>{verdict}</div>" \
                f"<div style='color:{MUTED};font-size:0.85em;'>Score: {score}/100</div>"
    elif bucket == "closed":
        gain = detail.get("listing_gain_pct")
        if gain is not None:
            gain_color = GREEN if gain >= 0 else RED
            right = f"<div style='font-size:1.4em;font-weight:800;color:{gain_color};'>" \
                    f"Listing {gain:+.1f}%</div>"
        else:
            right = f"<div style='color:{MUTED};'>Listing: " \
                    f"{html_escape(str(detail.get('listing_date_str') or 'Pending'))}</div>"
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
            custom_metric("Subscription", f"{sub:.2f}x" if sub is not None else (detail.get("subscription_str") or "N/A"))

    if bucket != "upcoming" and (detail.get("subscription_qib") is not None or detail.get("subscription_nii") is not None or detail.get("subscription_retail") is not None):
        s1, s2, s3 = st.columns(3)
        with s1:
            custom_metric("QIB", f"{detail['subscription_qib']:.2f}x" if detail.get("subscription_qib") is not None else "N/A")
        with s2:
            custom_metric("NII", f"{detail['subscription_nii']:.2f}x" if detail.get("subscription_nii") is not None else "N/A")
        with s3:
            custom_metric("Retail", f"{detail['subscription_retail']:.2f}x" if detail.get("subscription_retail") is not None else "N/A")

    card("Business Overview",
         f"<p style='color:#c9d1d9;font-size:0.9em;line-height:1.6;'>"
         f"{html_escape(str(detail.get('about') or 'Not available.'))}</p>")

    fins = detail.get("financials") or []
    if fins:
        st.markdown("##### 📊 Financial Trajectory (Revenue vs. PAT)")
        render_ipo_financials_chart(fins)
        
        st.markdown("##### RHP Financial Highlights Table")
        rows = []
        for f in fins[:4]:
            rows.append({
                "Year": f.get("year"),
                "Revenue (₹ Cr)": f.get("revenue_cr") if f.get("revenue_cr") is not None else "—",
                "PAT (₹ Cr)": f.get("pat_cr") if f.get("pat_cr") is not None else "—",
                "Historical EPS": f.get("eps") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

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

    with st.spinner("Generating AI note..."):
        narr = ipo_ai_narrative(detail, score, verdict, pros, cons, bucket=bucket)
    card("AI Research Note",
         f"<p style='color:#c9d1d9;font-size:0.9em;line-height:1.6;white-space:pre-wrap;'>"
         f"{style_verdict_text(narr)}</p>")
