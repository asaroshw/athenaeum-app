"""Indian IPO data: Screener primary, Chittorgarh, ipomarket, AI GMP."""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta
import requests
import streamlit as st
from bs4 import BeautifulSoup
from athenaeum.utils.helpers import (
    html_escape_fn, _parse_date_flex, _parse_money_inr, _parse_gmp, _parse_price_band,
    _slug_from_href, _classify_bucket, to_float,
)
html_escape = html_escape_fn  # Fix NameError

from athenaeum.config import GREEN, RED, MUTED, BLUE, BORDER, ORANGE, CARD_BG
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
            logger.warning("ipomarket list %s status %s", path, r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.warning("ipomarket list fetch failed %s: %s", path, e)
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

            bucket = "current" if "open" in path else ("closed" if "listed" in path else "upcoming")
            results.append({
                "symbol": slug,
                "slug": slug,
                "name": name,
                "status": status,
                "bucket": bucket,
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
            if not t or len(t) > 100:
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
                "status": "CURRENT", "bucket": "current",
                "date": "", "open_date": None, "close_date": None,
                "price_low": None, "price_high": None, "price_band_str": "",
                "lot_size": None, "min_investment": None,
                "gmp": None, "gmp_pct": None, "gmp_str": None,
                "subscription_str": None, "issue_size_cr": None, "issue_size_str": "",
                "exchange": "NSE/BSE",
                "detail_url": full,
                "chittorgarh_url": full,
                "source": "chittorgarh",
            })
    except Exception as e:
        logger.warning("Chittorgarh dashboard scrape failed: %s", e)
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
            d0 = _parse_date_flex(f"{piece} {year}")
            if d0 is None:
                return None
            delta = (d0 - today).days
            if delta < -180:
                d1 = _parse_date_flex(f"{piece} {year + 1}")
                if d1 is not None:
                    return d1
            if delta > 200:
                d1 = _parse_date_flex(f"{piece} {year - 1}")
                if d1 is not None and abs((d1 - today).days) < abs(delta):
                    return d1
            return d0

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
                    sub_text = texts[5] if len(texts) > 5 else ""
                    def _times(label):
                        m = re.search(rf"{label}.*?([\d.]+)\s*times", sub_text, re.I)
                        return float(m.group(1)) if m else None
                    total = _times("Total")
                    qib = _times("Institutional")
                    nii = _times("Non-Institutional")
                    retail = _times("Retail")
                    open_d, close_d, period = _parse_period(texts[1] if len(texts) > 1 else "")
                    plo, phi = _parse_price_band(texts[2] if len(texts) > 2 else "")
                    listing_s = texts[3] if len(texts) > 3 else ""
                    mcap = texts[4] if len(texts) > 4 else ""
                    pe_s = texts[6] if len(texts) > 6 else ""
                    roce_s = texts[7] if len(texts) > 7 else ""
                    pe = None
                    try:
                        pe = float(re.sub(r"[^\d.]", "", pe_s)) if pe_s and any(ch.isdigit() for ch in pe_s) else None
                    except ValueError:
                        pe = None
                    roce = None
                    mroce = re.search(r"([+-]?[\d.]+)", roce_s or "")
                    if mroce:
                        try:
                            roce = float(mroce.group(1))
                        except ValueError:
                            pass
                    today = datetime.today().date()
                    if open_d and open_d > today:
                        bucket = "upcoming"
                    elif close_d and close_d < today:
                        bucket = "closed"
                    else:
                        bucket = "current"
                    rec = {
                        "symbol": slug, "slug": slug, "name": name,
                        "status": bucket.upper(), "bucket": bucket,
                        "date": period, "open_date": open_d.isoformat() if open_d else None,
                        "close_date": close_d.isoformat() if close_d else None,
                        "price_low": plo, "price_high": phi,
                        "price_band_str": texts[2] if len(texts) > 2 else "",
                        "lot_size": None, "min_investment": None,
                        "gmp": None, "gmp_pct": None, "gmp_str": None,
                        "subscription_str": f"{total}x" if total is not None else None,
                        "subscription_total": total,
                        "subscription_qib": qib,
                        "subscription_nii": nii,
                        "subscription_retail": retail,
                        "issue_size_cr": _parse_money_inr(mcap),
                        "issue_size_str": f"₹{mcap} Cr" if mcap else "",
                        "listing_date_str": listing_s,
                        "pe_ipo": pe, "roce_ipo": roce,
                        "exchange": "NSE/BSE",
                        "detail_url": "https://www.screener.in" + a["href"],
                        "screener_url": "https://www.screener.in" + a["href"],
                        "source": "screener",
                    }
                    out[bucket].append(rec)
    except Exception as e:
        logger.warning("Screener IPO list failed: %s", e)

    try:
        r = requests.get("https://www.screener.in/ipo/recent/", headers=H, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup.find_all("table"):
                hdrs = [c.get_text(" ", strip=True) for c in (t.find_all("tr")[0].find_all(["th", "td"]) if t.find_all("tr") else [])]
                if not (hdrs and "Listing Date" in hdrs and "IPO Price" in " ".join(hdrs)):
                    continue
                for row in t.find_all("tr")[1:]:
                    a = row.find("a", href=re.compile(r"/company/"))
                    if not a:
                        continue
                    cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                    name = a.get_text(" ", strip=True)
                    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                    if any(x["slug"] == slug for x in out["closed"]):
                        continue
                    ipo_price = None
                    m = re.search(r"([\d.]+)", cells[3] if len(cells) > 3 else "")
                    if m:
                        ipo_price = float(m.group(1))
                    cur_price = None
                    m = re.search(r"([\d.]+)", cells[4] if len(cells) > 4 else "")
                    if m:
                        cur_price = float(m.group(1))
                    chg = None
                    m = re.search(r"([+-]?[\d.]+)\s*%", cells[5] if len(cells) > 5 else "")
                    if m:
                        chg = float(m.group(1))
                    elif ipo_price and cur_price and ipo_price > 0:
                        chg = round((cur_price / ipo_price - 1) * 100, 2)
                    out["closed"].append({
                        "symbol": slug, "slug": slug, "name": name,
                        "status": "CLOSED", "bucket": "closed",
                        "date": cells[1] if len(cells) > 1 else "",
                        "open_date": None, "close_date": None,
                        "price_low": ipo_price, "price_high": ipo_price,
                        "price_band_str": cells[3] if len(cells) > 3 else "",
                        "lot_size": None, "min_investment": None,
                        "gmp": None, "gmp_pct": None, "gmp_str": None,
                        "subscription_str": None, "subscription_total": None,
                        "issue_size_cr": _parse_money_inr(cells[2] if len(cells) > 2 else ""),
                        "issue_size_str": cells[2] if len(cells) > 2 else "",
                        "listing_date_str": cells[1] if len(cells) > 1 else "",
                        "listing_price": cur_price,
                        "listing_gain_pct": chg,
                        "exchange": "NSE/BSE",
                        "detail_url": "https://www.screener.in" + a["href"],
                        "screener_url": "https://www.screener.in" + a["href"],
                        "source": "screener",
                    })
    except Exception as e:
        logger.warning("Screener recent IPO list failed: %s", e)

    return out


@st.cache_data(ttl=3600, show_spinner=False)
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
                    if rev is None and sales_row[i] not in ("", "-", "—"):
                        try:
                            rev = float(str(sales_row[i]).replace(",", ""))
                        except ValueError:
                            pass
                    if pat is None and pat_row and i < len(pat_row) and pat_row[i] not in ("", "-", "—"):
                        try:
                            pat = float(str(pat_row[i]).replace(",", ""))
                        except ValueError:
                            pass
                    financials.append({"year": y, "revenue_cr": rev, "pat_cr": pat, "eps": None, "raw": [y, sales_row[i] if i < len(sales_row) else ""]})
                if financials:
                    break
        if financials:
            financials = list(reversed(financials))
            result["financials"] = financials
            revs = [f["revenue_cr"] for f in financials if f.get("revenue_cr")]
            if len(revs) >= 2 and revs[-1] and revs[-1] > 0:
                years_n = len(revs) - 1
                try:
                    result["revenue_cagr"] = round(((revs[0] / revs[-1]) ** (1 / years_n) - 1) * 100, 2)
                except Exception:
                    result["revenue_cagr"] = None
            pats = [f["pat_cr"] for f in financials if f.get("pat_cr") is not None]
            if pats:
                result["is_profitable_latest"] = pats[0] > 0
                result["is_profitable_all"] = all(p > 0 for p in pats[:3])
        ratios = {}
        for li in soup2.select("#top-ratios li, ul#top-ratios li, .company-ratios li"):
            t = li.get_text(" ", strip=True)
            if ":" in t:
                k, v = t.split(":", 1)
                ratios[k.strip()] = v.strip()
        if ratios:
            result["screener_ratios"] = ratios
    except Exception as e:
        logger.warning("Screener company lookup failed for %s: %s", name, e)
    return result


def _ai_google_gmp(company_name: str) -> dict:
    result = {"gmp": None, "gmp_pct": None, "gmp_str": None, "gmp_source": None, "gmp_note": None}
    if not company_name:
        return result
    headlines = fetch_google_news(f"{company_name} IPO GMP grey market premium")
    titles = [h.get("title", "") for h in headlines]
    blob = " | ".join(titles)
    m = re.search(r"GMP[^\d₹]*₹?\s*([\d.]+)\s*(?:\(?\s*([+-]?[\d.]+)\s*%\s*\)?)?", blob, re.I)
    if not m:
        m = re.search(r"(?:grey market|gmp)\s*(?:premium)?[^\d₹]*₹?\s*([\d.]+)", blob, re.I)
    if m:
        try:
            result["gmp"] = float(m.group(1))
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                result["gmp_pct"] = float(m.group(2))
            result["gmp_str"] = f"GMP ₹{result['gmp']}" + (f" ({result['gmp_pct']:+.1f}%)" if result.get("gmp_pct") is not None else "")
            result["gmp_source"] = "google_news_headlines"
            result["gmp_note"] = "Parsed from public news headlines; unofficial grey-market indicator."
            return result
        except (TypeError, ValueError):
            pass
    return result


def _normalize_company_name(name):
    if not name:
        return ""
    n = name.lower()
    for term in ["ltd", "limited", "food", "foods", "engg", "engineering", "private", "pvt", "co", "company", "corporation", "corp", "medicare", "enterprises"]:
        n = re.sub(rf"\b{term}\b", "", n)
    return re.sub(r"[^a-z0-9]", "", n)[:15]


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
                      "issue_size_str", "detail_url", "open_date", "close_date", "date"):
            if base.get(field) in (None, "", []) and rec.get(field) not in (None, "", []):
                base[field] = rec[field]
                
        src = base.get("source", "")
        if rec.get("source") and rec["source"] not in src:
            base["source"] = f"{src}+{rec['source']}" if src else rec["source"]
        by[k] = base
        
    return list(by.values())


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ipo_list_categorized() -> dict:
    scr = _scrape_screener_ipo_list()
    chitt = _scrape_chittorgarh_dashboard()
    im_current = _scrape_ipomarket_list("/ipo/open")
    im_upcoming = _scrape_ipomarket_list("/ipo/upcoming")
    im_closed = _scrape_ipomarket_list("/ipo/listed")

    # Keep categories strictly isolated from their original scrapers
    current = _merge_ipo_records(scr.get("current") or [], im_current)
    current = _merge_ipo_records(current, [x for x in chitt if x.get("bucket") == "current"])
    for x in current: x["bucket"] = "current"

    upcoming = _merge_ipo_records(scr.get("upcoming") or [], im_upcoming)
    upcoming = _merge_ipo_records(upcoming, [x for x in chitt if x.get("bucket") == "upcoming"])
    for x in upcoming: x["bucket"] = "upcoming"

    closed = _merge_ipo_records(scr.get("closed") or [], im_closed[:40])
    for x in closed: x["bucket"] = "closed"

    return {
        "current": current,
        "closed": closed[:40],
        "upcoming": upcoming,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources_note": "Hybrid IPO sources with strict category isolation.",
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
        if line.startswith("•") or line.startswith("-"):
            item = line.lstrip("•- ").strip()
            if len(item) < 20:
                continue
            low = item.lower()
            boilerplate = any(x in low for x in [
                "general economic", "economic downturn", "theft", "natural disaster",
                "force majeure", "pandemic", "covid", "currency fluctuation",
                "interest rate risk", "political instability", "act of god",
            ])
            if any(x in low for x in ["risk", "litigation", "dependent", "concentration",
                                       "competition", "regulatory", "customer", "working capital",
                                       "promoter", "related party", "debt", "loss"]):
                if not boilerplate and len(risks) < 8:
                    risks.append(item)
            elif any(x in low for x in ["strong", "leading", "profitable", "scalable",
                                         "growth", "brand", "network", "platform",
                                         "diversified", "experienced", "market share"]):
                if len(strengths) < 8:
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
        if gmp_info.get("gmp_note"):
            detail.setdefault("data_notes", []).append(gmp_info["gmp_note"])

    scr_url = detail.get("screener_url")
    scr = _screener_company_lookup(detail.get("name") or "", screener_url=scr_url)
    if scr.get("screener_url"):
        detail["screener_url"] = scr["screener_url"]
    if scr.get("about_screener"):
        if (not detail.get("about") or detail.get("about", "").startswith("Business description")
                or len(detail.get("about", "")) < 60):
            detail["about"] = scr["about_screener"]
            detail.setdefault("data_notes", []).append("About text from Screener.in")
    if scr.get("financials") and (not detail.get("financials") or len(detail.get("financials") or []) < 2):
        detail["financials"] = scr["financials"]
        detail.setdefault("data_notes", []).append("RHP-style financials from Screener.in")
    if scr.get("revenue_cagr") is not None and detail.get("revenue_cagr") is None:
        detail["revenue_cagr"] = scr["revenue_cagr"]
    if scr.get("is_profitable_latest") is not None and detail.get("is_profitable_latest") is None:
        detail["is_profitable_latest"] = scr["is_profitable_latest"]
    if scr.get("is_profitable_all") is not None and detail.get("is_profitable_all") is None:
        detail["is_profitable_all"] = scr["is_profitable_all"]
    if scr.get("screener_ratios"):
        detail["screener_ratios"] = scr["screener_ratios"]

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

    offer = (detail.get("offer_type") or "").lower()
    if "fresh" in offer and "ofs" in offer:
        pros.append("Mix of fresh capital and OFS"); score += 2
    elif "offer for sale" in offer and "fresh" not in offer:
        cons.append("Primarily Offer for Sale — limited capital for growth"); score -= 4

    if detail.get("strengths"):
        pros.append(f"{len(detail['strengths'])} business strengths noted in filings summary")
        score += min(len(detail["strengths"]), 4)
    if detail.get("risks"):
        cons.append(f"{len(detail['risks'])} business-specific risks flagged")
        score -= min(len(detail["risks"]), 4)

    score = int(min(max(score, 0), 100))
    verdict = "BUY" if score >= 60 else "ABSTAIN"
    return score, verdict, pros, cons


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
        cols = st.columns([3.2, 1.4, 1.6, 1.2, 1.2])
        with cols[0]:
            st.markdown(
                f"<b>{html_escape(name)}</b><br>"
                f"<span style='color:{MUTED};font-size:0.8em;'>"
                f"{html_escape(str(sym))} · {html_escape(str(ipo.get('exchange','')))}</span>",
                unsafe_allow_html=True)
        with cols[1]:
            st.markdown(
                f"<span style='font-size:0.75em;color:{MUTED};'>Open</span><br>"
                f"<b>{html_escape(str(ipo.get('date') or ipo.get('open_date') or 'TBA'))}</b>",
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
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Status</span><br>"
                    f"<b>Closed/Listed</b>",
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<span style='font-size:0.75em;color:{MUTED};'>Status</span><br>"
                    f"<b>Upcoming</b>",
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
                              "subscription_qib", "subscription_nii", "subscription_retail",
                              "issue_size_cr", "issue_size_str", "screener_url", "pe_ipo",
                              "roce_ipo", "listing_date_str", "listing_gain_pct", "listing_price"):
                        if d.get(k) is None and ipo.get(k) is not None:
                            d[k] = ipo[k]
                    if ipo.get("screener_url"):
                        d["screener_url"] = ipo["screener_url"]
                        scr = _screener_company_lookup(name, screener_url=ipo["screener_url"])
                        if scr.get("financials") and not d.get("financials"):
                            d["financials"] = scr["financials"]
                        if scr.get("revenue_cagr") is not None:
                            d["revenue_cagr"] = scr["revenue_cagr"]
                        if scr.get("is_profitable_latest") is not None:
                            d["is_profitable_latest"] = scr["is_profitable_latest"]
                        if scr.get("is_profitable_all") is not None:
                            d["is_profitable_all"] = scr["is_profitable_all"]
                        if scr.get("about_screener") and (not d.get("about") or len(d.get("about","")) < 60):
                            d["about"] = scr["about_screener"]
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
            right = f"<div style='font-size:1.4em;font-weight:800;color:{GREEN if gain>=0 else RED};'>" \
                    f"Listing {gain:+.1f}%</div>"
        else:
            right = f"<div style='color:{MUTED};'>Listing: " \
                    f"{html_escape(str(detail.get('listing_date_str') or 'Pending / see exchange'))}</div>"
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
            · {html_escape(str(detail.get('offer_type','Offer type TBA')))}
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
                else (detail.get("gmp_str") or ("N/A" if bucket == "upcoming" else "N/A")))
    with m4:
        if bucket == "upcoming":
            custom_metric("Revenue CAGR",
                f"{detail['revenue_cagr']}%" if detail.get("revenue_cagr") is not None else "N/A")
        else:
            sub = detail.get("subscription_total")
            custom_metric("Subscription", f"{sub:.2f}x" if sub is not None else (detail.get("subscription_str") or "N/A"))

    if bucket != "upcoming":
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
        st.markdown("##### RHP Financial Highlights")
        rows = []
        for f in fins[:4]:
            rows.append({
                "Year": f.get("year"),
                "Revenue (₹ Cr)": f.get("revenue_cr") if f.get("revenue_cr") is not None else "—",
                "PAT (₹ Cr)": f.get("pat_cr") if f.get("pat_cr") is not None else "—",
                "EPS": f.get("eps") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if detail.get("revenue_cagr") is not None:
            st.caption(f"Revenue CAGR (from available years): {detail['revenue_cagr']}%")

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

    if detail.get("ipo_news"):
        n_html = "".join(
            f"<div style='padding:5px 0;border-bottom:1px solid {BORDER};'>"
            f"<a href='{html_escape(str(n.get('link','#')), quote=True)}' target='_blank' "
            f"rel='noopener noreferrer' style='color:{BLUE};'>🔗 {html_escape(str(n.get('title','')))}</a></div>"
            for n in detail["ipo_news"][:5])
        card("Latest IPO News", n_html)

    with st.spinner("Generating AI note..."):
        narr = ipo_ai_narrative(detail, score, verdict, pros, cons, bucket=bucket)
    card("AI Research Note",
         f"<p style='color:#c9d1d9;font-size:0.9em;line-height:1.6;white-space:pre-wrap;'>"
         f"{style_verdict_text(narr)}</p>")

    if bucket == "current" and verdict:
        st.markdown(f"""
        <div class="swf-card" style="border:2px solid {vc};text-align:center;padding:24px;margin-top:16px;">
          <div style="font-size:0.9em;color:{MUTED};margin-bottom:6px;">IPO SCREENING VERDICT (CURRENT ONLY — not a full issue-price valuation)</div>
          <div style="font-size:2.8em;font-weight:900;color:{vc};">{verdict}</div>
          <div style="color:{MUTED};font-size:0.85em;margin-top:6px;">
            Score {score}/100 · Screening model based on RHP numbers, GMP & subscription —
            not a guarantee of listing performance. Always read the RHP.
          </div>
        </div>
        """, unsafe_allow_html=True)
    elif bucket == "closed":
        st.info("Closed IPOs do not receive a BUY/ABSTAIN verdict. Review listing outcome / pending listing date above.")
    else:
        st.info("Upcoming IPOs do not show GMP, live subscription, or a BUY/ABSTAIN verdict until the issue is open.")

    notes = detail.get("data_notes") or []
    if detail.get("gmp_source"):
        notes.append(f"GMP source: {detail.get('gmp_source')}")
    if notes:
        with st.expander(f"Data notes ({len(notes)})"):
            for n in notes:
                st.markdown(f"- {html_escape(str(n))}")
    if detail.get("screener_url"):
        st.caption(f"Screener backup: {detail['screener_url']}")
    st.caption(
        "Sources: Chittorgarh (calendar) · structured issue fields from public aggregators · "
        "Screener.in backup for company text · GMP from aggregator or news/AI extraction (unofficial). "
        "Not financial advice."
    )
