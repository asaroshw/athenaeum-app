"""Indian IPO data: Screener primary, Chittorgarh, ipomarket, AI GMP."""
from __future__ import annotations
import difflib
import logging
import re
import time
import urllib.robotparser
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import requests
import streamlit as st
from bs4 import BeautifulSoup
import plotly.graph_objects as go
import plotly.express as px

from athenaeum.utils.helpers import (
    html_escape_fn, _parse_date_flex, _parse_money_inr, _parse_gmp, _parse_price_band,
    _slug_from_href, to_float,
)
html_escape = html_escape_fn

from athenaeum.config import GREEN, RED, MUTED, BLUE, BORDER, ORANGE, CARD_BG, BG, GOLD, TEXT, TEXT_BODY
from athenaeum.data.equity import fetch_google_news
from athenaeum.ui.components import custom_metric, card
from athenaeum.ai.reports import ipo_ai_narrative
from athenaeum.utils.helpers import style_verdict_text, rating_color
from athenaeum.ui.components import verdict_pill, status_pill

logger = logging.getLogger("athenaeum")
_IPO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

# Per-domain robots.txt cache (process lifetime — robots.txt changes rarely
# enough that re-fetching before every single request would be wasteful).
# Added alongside the ipopremium.in scraper specifically because that site's
# robots.txt could not be manually verified during development (network
# access for the fetch/search tooling used to build this app could not
# retrieve it in that session). Rather than ship a scraper resting on an
# unverified assumption, every _http_get call — for all four sources, not
# only the new one — now checks robots.txt programmatically at runtime.
_ROBOTS_CACHE: dict = {}


def _robots_allow(url: str) -> bool:
    """True if robots.txt for `url`'s domain permits fetching it (or if
    robots.txt can't be retrieved/parsed at all — a missing or unreachable
    robots.txt is conventionally treated as "no restriction," not as a
    disallow signal; only an explicit Disallow rule blocks a fetch here)."""
    try:
        parsed = urlparse(url)
        domain_root = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return True
    if domain_root not in _ROBOTS_CACHE:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{domain_root}/robots.txt")
        try:
            rp.read()
            _ROBOTS_CACHE[domain_root] = rp
        except Exception:
            _ROBOTS_CACHE[domain_root] = None  # unreachable -> fail open
    rp = _ROBOTS_CACHE[domain_root]
    if rp is None:
        return True
    try:
        return rp.can_fetch(_IPO_HEADERS["User-Agent"], url)
    except Exception:
        return True


def _http_get(url, headers=None, timeout=20, retries=2):
    """requests.get with a couple of quick retries.

    IPO list results are cached for 30 minutes (@st.cache_data). Without a
    retry, one transient failure — a timeout, a momentary rate limit, a
    single dropped connection — gets cached as an empty result and the user
    sees a blank tab for the full cache window even though the source was
    fine a few seconds later. This trades a little latency on the rare
    failing request for materially better reliability on the common case.
    Returns the Response on a 200, or None if every attempt failed, robots.txt
    disallows the fetch, or the URL couldn't be parsed — callers already
    treat None as "source unavailable right now" and degrade gracefully
    rather than raising.
    """
    if not _robots_allow(url):
        logger.warning("Skipping fetch — robots.txt disallows: %s", url)
        return None
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers or _IPO_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            last_err = f"HTTP {r.status_code}"
            logger.debug("_http_get non-200 (attempt %d/%d) for %s: %s",
                         attempt + 1, retries + 1, url, last_err)
        except Exception as e:
            last_err = str(e)
            logger.debug("_http_get failed (attempt %d/%d) for %s: %s",
                         attempt + 1, retries + 1, url, last_err)
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))  # brief, increasing backoff
    logger.warning("_http_get exhausted %d attempts for %s: %s", retries + 1, url, last_err)
    return None

@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_ipomarket_list(path: str) -> list:
    url = f"https://www.ipomarket.in{path}"
    r = _http_get(url)
    if r is None:
        return []
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.warning("Failed to parse ipomarket %s: %s", path, e)
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
            # These suffix words are sometimes glued with no separating space
            # (e.g. "ShiprocketOthers") because the sector tag is a sibling
            # span with no text-node gap in the source markup.
            name = re.sub(r"\s*(Agriculture|Others|Mainboard|SME)$", "", name).strip()
            # Derive status strictly from the path requested, not from a scraped cell that
            # is often missing or inconsistent across sections of the same page.
            if "open" in path:
                status = "OPEN"
            elif "listed" in path:
                status = "LISTED"
            else:
                status = "UPCOMING"

            if "listed" in path:
                # /ipo/listed uses a completely different column set:
                # Company | Listed Date | IPO Price | Listing Price | Listing Gain | CMP | Return
                # None of open/close/price-band/lot/gmp/subscription exist here.
                listed_s = col(["listed date"])
                ipo_price_s = col(["ipo price"])
                listing_price_s = col(["listing price"])
                listing_gain_s = col(["listing gain"])
                cmp_s = col(["cmp"])
                listed_d = _parse_date_flex(listed_s)
                ipo_price_v = to_float(ipo_price_s)
                listing_price_v = to_float(listing_price_s)
                cmp_v = to_float(cmp_s)
                gain_m = re.search(r"([+-]?[\d.]+)\s*%", listing_gain_s or "")
                gain_v = float(gain_m.group(1)) if gain_m else None
                if gain_v is None and ipo_price_v and listing_price_v:
                    gain_v = round((listing_price_v / ipo_price_v - 1) * 100, 2)

                results.append({
                    "symbol": slug, "slug": slug, "name": name, "status": status,
                    "date": listed_s or "", "open_date": None, "close_date": None,
                    "listing_date_str": listed_s or "",
                    "price_low": ipo_price_v, "price_high": ipo_price_v,
                    "price_band_str": f"₹{ipo_price_v:g}" if ipo_price_v else "",
                    "listing_price": listing_price_v if listing_price_v else cmp_v,
                    "listing_gain_pct": gain_v,
                    "exchange": "NSE/BSE",
                    "detail_url": f"https://www.ipomarket.in/ipo/{slug}",
                    "source": "ipomarket",
                })
                continue

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


def _parse_dd_mon_range(text: str):
    """Parse Chittorgarh's dashboard date-range format, e.g. 'O12 - 14 Aug',
    'CT11 - 13 Aug', '30 Jul - 03 Aug', '24 - 27 Aug'.

    The site glues a 1-3 letter status badge (O/CT/P/LT/etc, meaning varies and
    is NOT reliable across rows) directly onto the first digit with no space —
    we strip that, then parse 'D [Mon] - D Mon' (month on the open side is
    optional and borrows the close side's month when absent). We deliberately
    do NOT trust the badge text itself for status — only the dates, which is
    what every other source in this module also keys bucketing off of.
    Returns (open_date, close_date) as date objects, or (None, None).
    """
    if not text:
        return None, None
    t = text.strip()
    t = re.sub(r"^[A-Z]{1,3}(?=\d)", "", t)  # strip glued status badge e.g. "O12" -> "12"
    t = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", t)  # strip ordinal suffixes
    m = re.match(r"^(\d{1,2})(?:\s+([A-Za-z]{3,9}))?\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,9})$", t)
    if not m:
        return None, None
    d1, mon1, d2, mon2 = m.groups()
    mon1 = mon1 or mon2
    year = datetime.today().date().year

    def _mk(day, mon, yr):
        return _parse_date_flex(f"{day} {mon} {yr}")

    open_d = _mk(d1, mon1, year)
    close_d = _mk(d2, mon2, year)
    if open_d and close_d and close_d < open_d:
        # Year-boundary wrap, e.g. "29 Dec - 02 Jan"
        close_d = _mk(d2, mon2, year + 1)
    return open_d, close_d


def _scrape_chittorgarh_one_dashboard(url: str) -> list:
    out, seen = [], set()
    r = _http_get(url)
    if r is None:
        return out
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.warning("Failed to parse chittorgarh dashboard %s: %s", url, e)
        return out

    # The real dashboard link pattern is /ipo/{slug}-ipo/{id}/ (WITH a numeric id).
    # This is deliberately distinct from /ipo_review/{slug}/{id}/, which is a
    # separate ratings-only table on the same page with no date information.
    link_re = re.compile(r"^/ipo/[a-z0-9-]+-ipo/\d+/?$")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not link_re.match(href):
            continue
        # The date range may be a sibling text node within the SAME <td> as the
        # link, or live in a separate sibling <td> — real-world markup for this
        # varies, so try the narrower scope first and fall back to the full row.
        td = a.find_parent("td")
        cell_text = td.get_text(" ", strip=True) if td is not None else ""
        link_text = a.get_text(" ", strip=True)
        name = link_text.strip()
        if not name or name.lower() in ("ipo", "sme ipo"):
            continue
        slug = _slug_from_href(href) or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug in seen:
            continue
        seen.add(slug)

        date_from_cell = cell_text[len(link_text):].strip() if cell_text.startswith(link_text) else cell_text.replace(link_text, "").strip()
        open_d, close_d = _parse_dd_mon_range(date_from_cell)
        date_text = date_from_cell

        if open_d is None:
            # Fall back to scanning the whole row (link + date in separate <td>s)
            tr = a.find_parent("tr")
            if tr is not None:
                row_text = tr.get_text(" ", strip=True)
                date_from_row = row_text[len(link_text):].strip() if row_text.startswith(link_text) else row_text.replace(link_text, "").strip()
                open_d2, close_d2 = _parse_dd_mon_range(date_from_row)
                if open_d2 is not None:
                    open_d, close_d = open_d2, close_d2
                    date_text = date_from_row

        full = ("https://www.chittorgarh.com" + href) if href.startswith("/") else href
        out.append({
            "symbol": slug, "slug": slug, "name": name,
            # status intentionally left blank — bucketing is date-driven for this
            # source since the site's own status badges are not reliably decodable
            "status": "", "date": date_text,
            "open_date": open_d.isoformat() if open_d else None,
            "close_date": close_d.isoformat() if close_d else None,
            "price_low": None, "price_high": None, "price_band_str": "",
            "exchange": "NSE/BSE", "detail_url": full, "source": "chittorgarh",
        })
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_chittorgarh_dashboard() -> list:
    """Scrape both the mainboard and SME IPO dashboards. Companies in this app's
    'current' state (e.g. many SME issues) live only on the SME dashboard, so
    both must be fetched or those IPOs have no date evidence from this source."""
    combined = []
    seen_slugs = set()
    for url in (
        "https://www.chittorgarh.com/ipo/ipo_dashboard.asp",
        "https://www.chittorgarh.com/ipo/ipo_dashboard.asp?a=sme",
    ):
        for rec in _scrape_chittorgarh_one_dashboard(url):
            if rec["slug"] in seen_slugs:
                continue
            seen_slugs.add(rec["slug"])
            combined.append(rec)
    return combined


@st.cache_data(ttl=1800, show_spinner=False)
def _scrape_ipopremium_list() -> list:
    """Scrape ipopremium.in's homepage table to cross-reference GMP and dates.

    Deliberately scoped to the single homepage list, not a per-IPO detail
    crawl — "a fast scraper... to cross-reference GMPs and dates" is a list-
    level cross-check, and one cached request every 30 minutes stays fast
    and light regardless of how many IPOs are listed, unlike fetching
    dozens of individual detail pages would be.

    Two real quirks found while building this, both handled below:

    1. The homepage renders TWO tables. One is header-only in a plain fetch
       (Company Name / GMP Rumors * / Open / Close / Price / Lot Size /
       Issue Size (cr) / LM / Allotment Date / Listing Date / Action) — it
       is populated client-side by JavaScript this scraper does not
       execute, so it always looks empty here. The actual data lives in a
       SEPARATE, statically-rendered table with a different header set
       (Company Name / Type / GMP (₹) / Open / Close / Price Band (₹) /
       Listing Date). Matched by header signature, not position, so this
       keeps working if the tables' order on the page changes.
    2. Company names carry a parenthetical exchange/board suffix baked
       directly into the name — "Lumino Industries Ltd (MAINBOARD)",
       "Complete Sports & Management India Ltd (BSE SME)" — with
       inconsistent casing ("Mainboard" vs "MAINBOARD"). Left in, this
       would poison the merge engine: a record from this source would
       normalize differently from the same company's record on any other
       source, and the two would never merge — the exact class of bug the
       merge engine above exists to fix. Stripped here at the source, plus
       _normalize_company_name also strips a bare trailing parenthetical as
       defense-in-depth for whatever the next new source's convention is.
    """
    r = _http_get("https://www.ipopremium.in/")
    if r is None:
        return []
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.warning("Failed to parse ipopremium.in: %s", e)
        return []

    target_table = None
    target_headers = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if any("company" in h for h in headers) and any("gmp" in h for h in headers) \
                and any("price band" in h for h in headers):
            target_table = table
            target_headers = headers
            break
    if target_table is None:
        logger.warning("ipopremium.in: could not find the populated IPO table "
                        "(page structure may have changed).")
        return []

    def col_index(*keys):
        for i, h in enumerate(target_headers):
            if any(k in h for k in keys):
                return i
        return None

    idx_name = col_index("company")
    idx_gmp = col_index("gmp")
    idx_open = col_index("open")
    idx_close = col_index("close")
    idx_band = col_index("price band")
    idx_listing = col_index("listing date", "listing")
    if idx_name is None:
        return []

    today = datetime.today().date()
    results = []
    for row in target_table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= idx_name:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]

        a = row.find("a", href=True)
        detail_url = a["href"] if a and a["href"].startswith("http") else \
            (f"https://www.ipopremium.in{a['href']}" if a else "")
        slug = _slug_from_href(detail_url) or None
        if not slug and detail_url:
            m = re.search(r"/view/ipo/\d+/([a-z0-9\-]+)", detail_url)
            slug = m.group(1) if m else None

        name_raw = texts[idx_name]
        # Strip the glued-on exchange/board suffix — see docstring point 2.
        name = re.sub(r"\s*\((?:mainboard|bse sme|nse sme|sme)\)\s*$", "", name_raw, flags=re.IGNORECASE).strip()
        if not name:
            continue
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        open_d = _parse_date_flex(texts[idx_open]) if idx_open is not None and idx_open < len(texts) else None
        close_d = _parse_date_flex(texts[idx_close]) if idx_close is not None and idx_close < len(texts) else None
        listing_s = texts[idx_listing] if idx_listing is not None and idx_listing < len(texts) else ""

        plo, phi = (None, None)
        if idx_band is not None and idx_band < len(texts):
            plo, phi = _parse_price_band(texts[idx_band])
            if plo == 0 and phi == 0:  # "0–0" placeholder for a not-yet-priced IPO
                plo, phi = None, None

        gmp_v = to_float(texts[idx_gmp]) if idx_gmp is not None and idx_gmp < len(texts) else None
        gmp_pct_v = round((gmp_v / phi) * 100, 2) if (gmp_v is not None and phi) else None

        # This source has no explicit status column — derive a light hint
        # from dates so _bucket_ipo has something beyond raw dates alone to
        # cross-check; the dates themselves remain the authoritative signal.
        if close_d is not None and close_d < today:
            status_hint = "CLOSED"
        elif open_d is not None and open_d > today:
            status_hint = "UPCOMING"
        elif open_d is not None:
            status_hint = "OPEN"
        else:
            status_hint = ""

        results.append({
            "symbol": slug, "slug": slug, "name": name, "status": status_hint,
            "date": texts[idx_open] if idx_open is not None and idx_open < len(texts) else "",
            "open_date": open_d.isoformat() if open_d else None,
            "close_date": close_d.isoformat() if close_d else None,
            "price_low": plo, "price_high": phi,
            "price_band_str": f"₹{plo:g}–₹{phi:g}" if (plo and phi) else "",
            "gmp": gmp_v, "gmp_pct": gmp_pct_v,
            "gmp_str": f"₹{gmp_v:g} ({gmp_pct_v:+.1f}%)" if (gmp_v is not None and gmp_pct_v is not None) else "",
            "listing_date_str": listing_s,
            "exchange": "NSE/BSE", "detail_url": detail_url, "source": "ipopremium",
        })
    return results


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

        def _with_year(piece, ref_date=None):
            piece = piece.strip()
            if re.search(r"\d{4}", piece):
                return _parse_date_flex(piece)
            # Try current year first, fall back to next year for month-only strings
            # that appear to already be in the past (handles year boundary wraps)
            d = _parse_date_flex(f"{piece} {year}")
            if d is None:
                d = _parse_date_flex(f"{piece} {year + 1}")
            if d is not None and ref_date is not None and d < ref_date:
                # close date rolled before the open date -> actually next year
                # (e.g. period spans a Dec -> Jan year boundary)
                d_next = _parse_date_flex(f"{piece} {ref_date.year + 1}")
                if d_next is not None:
                    d = d_next
            return d

        open_d = _with_year(parts[0]) if parts else None
        close_d = _with_year(parts[1], ref_date=open_d) if len(parts) > 1 else None
        return open_d, close_d, period

    r = _http_get("https://www.screener.in/ipo/", headers=H)
    try:
        if r is not None:
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
    except Exception as e:
        logger.warning("Failed to parse screener.in/ipo/ current table: %s", e)

    r2 = _http_get("https://www.screener.in/ipo/recent/", headers=H)
    try:
        if r2 is not None:
            soup = BeautifulSoup(r2.text, "html.parser")
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
    except Exception as e:
        logger.warning("Failed to parse screener.in/ipo/recent/ table: %s", e)

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
            row_data = {}
            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if not cells:
                    continue
                label = cells[0].lower()
                values = cells[1:]
                if "sales_row" not in row_data and ("sales" in label or "revenue" in label):
                    row_data["sales_row"] = values
                elif "pat_row" not in row_data and (label.startswith("net profit") or label == "pat" or "net profit" in label):
                    row_data["pat_row"] = values
                elif "ebitda_row" not in row_data and ("operating profit" in label or "ebitda" in label):
                    row_data["ebitda_row"] = values
                elif "assets_row" not in row_data and ("total assets" in label):
                    row_data["assets_row"] = values
                elif "networth_row" not in row_data and ("net worth" in label or "networth" in label
                                                          or "equity capital" in label):
                    row_data["networth_row"] = values
                elif "borrow_row" not in row_data and ("borrowing" in label):
                    row_data["borrow_row"] = values
            if row_data.get("sales_row"):
                sales_row = row_data["sales_row"]
                for i, y in enumerate(years):
                    if i >= len(sales_row):
                        break
                    def _at(key):
                        vals = row_data.get(key)
                        return _parse_money_inr(vals[i]) if vals and i < len(vals) else None
                    financials.append({
                        "year": y, "revenue_cr": _at("sales_row"), "pat_cr": _at("pat_row"),
                        "ebitda_cr": _at("ebitda_row"), "assets_cr": _at("assets_row"),
                        "net_worth_cr": _at("networth_row"), "borrowings_cr": _at("borrow_row"),
                        "eps": None,
                    })
                if financials:
                    break
        # Best-effort: a separate "Ratios" table often carries ROE%/ROCE% year-by-year
        # on Screener company pages. Purely additive — absent if not found.
        ratio_years, roe_row, roce_row = [], None, None
        for t in soup2.find_all("table"):
            rows = t.find_all("tr")
            if not rows:
                continue
            hdrs = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
            if not any(re.search(r"Mar|Jan|Dec|202[0-9]", h) for h in hdrs):
                continue
            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if not cells:
                    continue
                label = cells[0].lower()
                if roe_row is None and ("roe" in label and "%" in " ".join(cells)):
                    roe_row, ratio_years = cells[1:], hdrs[1:]
                elif roce_row is None and "roce" in label:
                    roce_row = cells[1:]
            if roe_row or roce_row:
                break
        if roe_row or roce_row:
            result["ratio_years"] = ratio_years
            result["roe_by_year"] = [_parse_money_inr(v) for v in roe_row] if roe_row else None
            result["roce_by_year"] = [_parse_money_inr(v) for v in roce_row] if roce_row else None
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
    Uses a MINIMUM length guard (≥5 chars after stripping — raised from the
    original ≥4, see below) so that short names like 'AB Corp' and 'AB Ltd'
    do not collapse to the same 2-char key and falsely merge two completely
    different companies.
    """
    if not name:
        return ""
    n = name.lower()
    # Strip any trailing parenthetical annotation — "(Mainboard)", "(BSE
    # SME)", "(NSE SME)" (ipopremium.in glues an exchange/board tag directly
    # onto the company name, inconsistently cased). Defense-in-depth: the
    # ipopremium.in scraper already strips its own specific known suffixes
    # at the source, but a bare parenthetical strip here catches whatever
    # the next new source's equivalent convention turns out to be, without
    # needing to enumerate it in advance.
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    # Treat '&' and the word 'and' as the same connector before anything else —
    # otherwise 'Wire & Engineering' and 'Wire and Engineering Limited' (a real
    # duplicate-entry pattern observed on ipomarket.in itself) produce different
    # keys and end up as two separate, non-deduplicated rows.
    n = re.sub(r"\s*&\s*", " and ", n)
    n = re.sub(r"\band\b", "", n)
    # Strip legal suffixes and very generic sector nouns. Expanded to include
    # common ABBREVIATIONS of the same words (Ltd/Ld, Corp/Corpn, Co/Cos) —
    # aggregators frequently differ only in which abbreviation they use for
    # the identical legal suffix, and the original list only had the one
    # spelling of each, so two sources' records for the same company
    # produced different keys and never merged.
    for term in [
        "ltd", "limited", "ld", "private", "pvt", "p", "co", "cos", "company",
        "corporation", "corp", "corpn", "enterprises", "enterprise",
        "holdings", "holding", "group", "grp",
    ]:
        n = re.sub(rf"\b{term}\b", "", n)
    # Strip sector words only when the remaining key would still be ≥5 chars
    # (raised from ≥4 — see MIN_SECTOR_STRIP_LEN below). Also expanded with
    # common abbreviations of the same sector words (Inds/Ind for Industries,
    # Engrs/Engineers alongside Engg/Engineering, Mfg for Manufacturing,
    # Intl for International, Tech/Technology already covered both ways) —
    # this is the specific gap behind the "Lumino Inds" vs "Lumino
    # Industries" duplicate: "Industries" was stripped, "Inds" was not, so
    # the same company produced two different keys depending on which
    # source's abbreviation convention was used.
    sector_words = [
        "food", "foods", "engg", "engineering", "engineers", "engineer", "engrs",
        "medicare", "healthcare",
        "industries", "industry", "inds", "ind",
        "technologies", "technology", "tech",
        "solutions", "soln",
        "infra", "infrastructure",
        "labs", "laboratories", "laboratory",
        "pharmaceuticals", "pharmaceutical", "pharma",
        "international", "intl",
        "national", "natl",
        "manufacturing", "mfg",
        "dairy",
    ]
    # MIN_SECTOR_STRIP_LEN=5, not 4: at 4, "Apex Industries" and "Apex Pharma"
    # BOTH strip down to the generic remainder "apex" and would incorrectly
    # merge as the same company — a real false-positive risk discovered while
    # calibrating this fix, and one the original ≥4 guard did not catch since
    # it was only ever exercised by short-name legal-suffix cases like
    # 'AB Corp'/'AB Ltd', not by common single-word brand names paired with a
    # sector word. Verified against every existing normalization test plus
    # this case in test_ipo_pure_logic.py before changing the constant.
    MIN_SECTOR_STRIP_LEN = 5
    n_stripped = n
    for term in sector_words:
        candidate = re.sub(rf"\b{term}\b", "", n_stripped)
        cleaned = re.sub(r"[^a-z0-9]", "", candidate)
        if len(cleaned) >= MIN_SECTOR_STRIP_LEN:
            n_stripped = candidate
    key = re.sub(r"[^a-z0-9]", "", n_stripped)[:16]
    # If we stripped too much and ended up with <4 chars, fall back to a slug of the raw name
    if len(key) < 4:
        key = re.sub(r"[^a-z0-9]", "", name.lower())[:16]
    return key


def _leading_token(name):
    """First alphanumeric token of a company name, lowercased — used as a
    cheap guard on fuzzy matching below. Company names are almost always
    distinguished by their lead word ('Force Motors' vs 'Force Intermediate
    Products' are different companies despite both starting with 'Force' and
    scoring reasonably on raw string similarity), so requiring the lead
    token to match before trusting a fuzzy match blocks the most likely
    false-positive shape without needing a much stricter (and more
    false-negative-prone) similarity threshold."""
    if not name:
        return ""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return words[0] if words else ""


def _fuzzy_key_match(key, first_token, candidate_keys_with_tokens, threshold=0.90):
    """Find an existing (key, first_token) pair that `key`/`first_token`
    should be treated as the same company as, when no EXACT key match was
    found. Returns the matched key, or None.

    Exact-key matching (via the expanded _normalize_company_name above)
    handles every abbreviation variant this codebase has actually observed.
    This fuzzy fallback exists for the long tail it can't enumerate in
    advance — typos, unfamiliar abbreviation styles, and especially new data
    sources (like ipopremium.in) whose exact naming conventions haven't been
    seen yet. difflib.SequenceMatcher is stdlib-only, adequate for short
    company-name strings, and avoids a new dependency for what is a bounded,
    infrequent comparison (dozens of tracked IPOs at a time, not thousands).
    """
    if not key or not first_token:
        return None
    best_ratio, best_key = 0.0, None
    for existing_key, existing_token in candidate_keys_with_tokens:
        if existing_token != first_token:
            continue  # leading-token guard — see _leading_token's docstring
        ratio = difflib.SequenceMatcher(None, key, existing_key).ratio()
        if ratio > best_ratio:
            best_ratio, best_key = ratio, existing_key
    return best_key if best_ratio >= threshold else None


def _merge_ipo_field_values(base, rec):
    """Merge one incoming record's fields into an existing base record,
    filling any field that's empty in `base` with a non-empty value from
    `rec` (never overwriting a value `base` already has). Shared by
    _merge_ipo_records and _deduplicate_list so there is one merge
    implementation, not two that can quietly drift apart.

    Also tracks a `sources` provenance list — which aggregator(s) actually
    contributed data to this merged record — the concrete answer to
    "keeping the best data from each source" once more than two sources can
    feed the same company (see ipopremium.in below)."""
    for field, val in rec.items():
        if field == "sources":
            continue
        if base.get(field) in (None, "", []) and val not in (None, "", []):
            base[field] = val
    incoming_sources = rec.get("sources") or ([rec["source"]] if rec.get("source") else [])
    if incoming_sources:
        existing_sources = base.get("sources") or ([base["source"]] if base.get("source") else [])
        base["sources"] = sorted(set(existing_sources) | set(incoming_sources))
    return base


def _resolve_ipo_key(raw_name, existing_keys, key_tokens, key_order):
    """Normalized key for `raw_name`, reusing an existing key if either an
    EXACT normalized-name match or a fuzzy match (see _fuzzy_key_match)
    already exists in `existing_keys`. Returns None if raw_name is empty."""
    k = _normalize_company_name(raw_name)
    if not k:
        return None
    if k in existing_keys:
        return k
    tok = _leading_token(raw_name)
    fuzzy_key = _fuzzy_key_match(k, tok, [(kk, key_tokens[kk]) for kk in key_order])
    return fuzzy_key if fuzzy_key else k


def _merge_ipo_records(primary: list, secondary: list) -> list:
    """Merge two IPO record lists into one. Two records are treated as the
    same company if their normalized names match exactly, OR — when no exact
    match exists — if they pass the difflib fuzzy check (_fuzzy_key_match):
    same leading word AND ≥90% string similarity on the normalized key. The
    exact-match path (via _normalize_company_name's expanded abbreviation
    list) handles every variant this codebase has actually seen; the fuzzy
    path is the net under it for whatever the next new source's naming
    convention turns out to be."""
    by, tokens, key_order = {}, {}, []
    for rec in list(primary) + list(secondary):
        raw_name = rec.get("name") or rec.get("slug")
        k = _resolve_ipo_key(raw_name, by, tokens, key_order)
        if not k:
            continue
        if k in by:
            by[k] = _merge_ipo_field_values(by[k], rec)
        else:
            by[k] = dict(rec)
            tokens[k] = _leading_token(raw_name)
            key_order.append(k)
    return list(by.values())


def _deduplicate_list(items: list) -> list:
    """Collapse a single list to one record per company. Uses the same
    exact-then-fuzzy matching and field-merge logic as _merge_ipo_records —
    this used to be a separate, purely-exact-match implementation that could
    (and did) disagree with _merge_ipo_records about whether two records
    were the same company."""
    by, tokens, key_order = {}, {}, []
    for item in items:
        raw_name = item.get("name") or item.get("slug") or ""
        k = _resolve_ipo_key(raw_name, by, tokens, key_order)
        if not k:
            k = re.sub(r"[^a-z0-9]", "", raw_name.lower())[:16] or None
        if not k:
            continue
        if k in by:
            by[k] = _merge_ipo_field_values(by[k], item)
        else:
            by[k] = dict(item)
            tokens[k] = _leading_token(raw_name)
            key_order.append(k)
    return list(by.values())


def _bucket_ipo(ipo: dict) -> str:
    """Determine the correct tab for a single IPO record.

    Reasoning order — always use the STRONGEST available signal, falling back
    only when it's genuinely unknown, not when it's merely absent from one
    particular source (records are frequently missing one field or another
    depending on which source(s) covered that IPO):

    1. Real post-listing market data, or status == LISTED → closed.
    2. screener_recent source with no price data: only closed if its own
       listing date has passed or is unknown (that feed can carry IPOs with a
       *future* listing date that haven't actually closed yet).
    3. close_date KNOWN → use it directly:
         < today → closed. >= today → current (unless open_date is ALSO known
         and is itself still in the future, i.e. a fully future window).
    4. close_date unknown, listing_date KNOWN → use it:
         <= today → closed (it has listed; we just don't know exactly when
         bidding closed). > today AND open_date known-past/today → current
         (bidding is confirmed under way or just finished). > today, no
         open_date, but within the next 10 days → current (SEBI timelines
         mean a locked-in near-term listing date implies bidding has already
         started or just finished — it is never "far-future upcoming").
         Otherwise → upcoming (respecting an explicit UPCOMING status first).
    5. Neither close_date nor listing_date known → fall back to status==OPEN,
       then open_date recency (<=15 days old → current, older → closed),
       then status==UPCOMING, then the TBA default of upcoming.
    """
    today = datetime.today().date()
    source = str(ipo.get("source") or "").lower()
    status = str(ipo.get("status") or "").upper()

    op_d  = _parse_date_flex(ipo.get("open_date"))
    cl_d  = _parse_date_flex(ipo.get("close_date"))
    lst_d = _parse_date_flex(ipo.get("listing_date_str"))
    has_price_data = ipo.get("listing_gain_pct") is not None or ipo.get("listing_price") is not None

    # ── 1. Hard closed signals: real post-listing market data ──────────────
    if has_price_data or status == "LISTED":
        return "closed"

    # ── 2. screener_recent with NO price data yet: only trust as closed if
    #        its listing date has passed or is unknown ──────────────────────
    if "screener_recent" in source:
        if lst_d is None or lst_d <= today:
            return "closed"
        # else: real future listing date, no price data — fall through.

    # ── 3. close_date is known: our strongest remaining signal ─────────────
    if cl_d is not None:
        if cl_d < today:
            return "closed"
        # cl_d >= today: the window has not closed yet.
        if op_d is not None and op_d > today:
            return "upcoming"  # both dates known and fully in the future
        return "current"

    # ── 4. close_date unknown, but listing_date is known ────────────────────
    if lst_d is not None:
        if lst_d <= today:
            # It has listed (or lists today); we just don't know exactly when
            # bidding closed. A reached listing date is hard evidence.
            return "closed"
        # lst_d is in the future.
        if op_d is not None and op_d <= today:
            # Confirmed open by direct date evidence, unknown close, but a
            # listing date is already locked in -> in progress or just
            # finished. Direct date evidence outranks a status label, which
            # could simply be stale (e.g. scraped from a page that hasn't
            # caught up to the issue having just opened).
            return "current"
        if status == "UPCOMING":
            # No direct evidence it has opened. An explicit "not yet open"
            # signal from a source dedicated to that distinction outranks
            # the weaker inference below.
            return "upcoming"
        if lst_d <= today + timedelta(days=10):
            # No open_date evidence at all, but a near-term locked-in listing
            # date is strong evidence it is open now or just closed. SEBI
            # timelines don't allow a scheduled listing without the
            # subscription window already being fixed. This is the key
            # safety net for the common case where only one source (often
            # screener.in, which can leave the subscription-period cell
            # blank for the first day or two of a live issue) has any date
            # evidence at all for a given IPO.
            return "current"
        # Far-future listing date with no open_date evidence: genuinely
        # upcoming.
        return "upcoming"

    # ── 5. Neither close_date nor listing_date known — fall back further ───
    if status == "OPEN":
        return "current"

    if op_d is not None:
        if op_d > today:
            return "upcoming"
        # op_d <= today, nothing else known at all
        if (today - op_d).days <= 15:
            return "current"
        return "closed"

    if status == "UPCOMING":
        return "upcoming"

    # ── 6. Default: no date information anywhere → upcoming (TBA) ──────────
    return "upcoming"



@st.cache_data(ttl=900, show_spinner=False)
def fetch_ipo_list_categorized() -> dict:
    # PERFORMANCE UPGRADE (Phase 1): these scrapes hit independent external sites —
    # dispatch them concurrently instead of one after another. What each scraper
    # returns, and how results are merged/bucketed below, is unchanged.
    # ipopremium.in added as a 6th concurrent source (Phase: IPO pipeline
    # fixes) — added AFTER the fuzzy-merge engine above was in place, by
    # design: it enriches existing merged records (fills GMP/date gaps) via
    # the same _merge_ipo_records pass every other source already goes
    # through, rather than creating a new class of unmerged duplicate.
    with ThreadPoolExecutor(max_workers=6) as executor:
        scr_future = executor.submit(_scrape_screener_ipo_list)
        chitt_future = executor.submit(_scrape_chittorgarh_dashboard)
        open_future = executor.submit(_scrape_ipomarket_list, "/ipo/open")
        upcoming_future = executor.submit(_scrape_ipomarket_list, "/ipo/upcoming")
        listed_future = executor.submit(_scrape_ipomarket_list, "/ipo/listed")
        ipopremium_future = executor.submit(_scrape_ipopremium_list)
        scr = scr_future.result()
        chitt = chitt_future.result()
        im_current = open_future.result()
        im_upcoming = upcoming_future.result()
        im_closed = listed_future.result()
        ipopremium = ipopremium_future.result()

    # Per-source record counts, purely for diagnostics — if a source that
    # normally returns data comes back with 0, this is how you'd actually see
    # it happened instead of just staring at an unexpectedly thin tab.
    source_health = {
        "screener_current": len(scr.get("current") or []),
        "screener_closed":  len(scr.get("closed") or []),
        "chittorgarh":      len(chitt or []),
        "ipomarket_open":   len(im_current or []),
        "ipomarket_upcoming": len(im_upcoming or []),
        "ipomarket_listed": len(im_closed or []),
        "ipopremium":       len(ipopremium or []),
    }
    if sum(source_health.values()) == 0:
        logger.warning("fetch_ipo_list_categorized: ALL sources returned zero records — "
                        "likely a network-level issue (blocked/unreachable) rather than no live IPOs.")

    master_pool: list = []
    for lst in [scr.get("current"), scr.get("upcoming"), scr.get("closed"),
                chitt, im_current, im_upcoming, im_closed, ipopremium]:
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
        "source_health": source_health,
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

    # PERFORMANCE UPGRADE (Phase 1): the detail-page fetch, the news fetch, and the
    # screener cross-reference lookup are independent — none needs anything beyond
    # url / detail['name'], both already set above, before the page has even been
    # parsed. Dispatch all three concurrently; each is still consumed at its
    # original call site below, so parsing/merge order and error handling are
    # unchanged. _ai_google_gmp stays sequential — it's genuinely conditional on
    # what parsing the detail page finds.
    _executor = ThreadPoolExecutor(max_workers=3)
    page_future = _executor.submit(_http_get, url)
    news_future = _executor.submit(fetch_google_news, f"{detail['name']} IPO GMP subscription 2026")
    screener_future = _executor.submit(_screener_company_lookup, detail.get("name") or "", screener_url=detail.get("screener_url"))
    _executor.shutdown(wait=False)

    r = page_future.result()
    if r is None:
        detail["error"] = "Detail page unreachable after retries"
        return detail
    try:
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
        if any("revenue" in h or "total income" in h for h in headers) and any("fiscal" in h or "year" in h for h in headers):
            col_map = {}
            for i, h in enumerate(headers):
                if "year" in h or "fiscal" in h or "period" in h:
                    col_map.setdefault("year", i)
                elif "revenue" in h or "total income" in h:
                    col_map.setdefault("revenue_cr", i)
                elif "ebitda" in h:
                    col_map.setdefault("ebitda_cr", i)
                elif "eps" in h:
                    col_map.setdefault("eps", i)
                elif "pat" in h or "profit after tax" in h or ("net" in h and "profit" in h):
                    col_map.setdefault("pat_cr", i)
                elif "asset" in h:
                    col_map.setdefault("assets_cr", i)
                elif "net worth" in h or "networth" in h:
                    col_map.setdefault("net_worth_cr", i)
                elif "borrowing" in h or "debt" in h:
                    col_map.setdefault("borrowings_cr", i)
            # fall back to the original fixed layout (year, revenue, pat, eps) if the
            # header text didn't clearly identify revenue/pat columns by keyword
            if "revenue_cr" not in col_map and len(headers) > 1:
                col_map.setdefault("revenue_cr", 1)
            if "pat_cr" not in col_map and len(headers) > 2:
                col_map.setdefault("pat_cr", 2)
            if "eps" not in col_map and len(headers) > 3:
                col_map.setdefault("eps", 3)
            fin_rows = []
            for tr in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 3:
                    continue
                row = {"year": cells[col_map.get("year", 0)], "raw": cells}
                for key in ("revenue_cr", "ebitda_cr", "pat_cr", "assets_cr", "net_worth_cr", "borrowings_cr"):
                    idx = col_map.get(key)
                    row[key] = _parse_money_inr(cells[idx]) if idx is not None and idx < len(cells) else None
                eps_idx = col_map.get("eps")
                row["eps"] = cells[eps_idx] if eps_idx is not None and eps_idx < len(cells) else None
                fin_rows.append(row)
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
    detail["lead_manager"]     = g("lead manager", "book running lead", "brlm")
    detail["allotment_date_str"]    = g("allotment date", "basis of allotment")
    detail["refund_date_str"]       = g("refund")
    detail["demat_credit_date_str"] = g("credit of shares", "demat credit", "credit to demat")
    detail["anchor_str"]            = g("anchor investor", "anchor allocation", "anchor portion")
    detail["pre_issue_shares_str"]  = g("pre issue", "pre-issue equity shares", "shares outstanding prior")
    detail["post_issue_shares_str"] = g("post issue", "post-issue equity shares", "shares outstanding after")

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

    # Day-by-day subscription buildup (Retail/NII/QIB), best-effort — only some
    # source pages expose this at day-level granularity. Reuses the page already
    # fetched above (no extra network call); if no matching table is found,
    # detail["subscription_by_day"] is simply absent and the buildup chart no-ops.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers_txt = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["td", "th"])]
        if not headers_txt or "day" not in " ".join(headers_txt):
            continue
        if not any(x in " ".join(headers_txt) for x in ["qib", "nii", "hni", "retail", "rii"]):
            continue
        col_map = {}
        for i, h in enumerate(headers_txt):
            if "qib" in h:
                col_map["qib"] = i
            elif "nii" in h or "hni" in h:
                col_map["nii"] = i
            elif "retail" in h or "rii" in h:
                col_map["retail"] = i
            elif "day" in h:
                col_map["day"] = i
        if "day" not in col_map or len(col_map) < 2:
            continue
        sub_days = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= max(col_map.values()):
                continue
            entry = {"day": cells[col_map["day"]]}
            for k in ("qib", "nii", "retail"):
                if k in col_map:
                    mnum = re.search(r"([\d.]+)", cells[col_map[k]])
                    entry[k] = float(mnum.group(1)) if mnum else None
            sub_days.append(entry)
        if len(sub_days) >= 2:
            detail["subscription_by_day"] = sub_days
        break

    # Day-by-day GMP history, best-effort — same reasoning/pattern as
    # subscription_by_day above: only some source pages expose this; if not found,
    # detail["gmp_by_day"] is simply absent and the GMP trend chart falls back to a
    # single current-GMP point instead of a trend line.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers_txt = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["td", "th"])]
        if not headers_txt or "day" not in " ".join(headers_txt) and "date" not in " ".join(headers_txt):
            continue
        if "gmp" not in " ".join(headers_txt):
            continue
        day_col = next((i for i, h in enumerate(headers_txt) if "day" in h or "date" in h), None)
        gmp_col = next((i for i, h in enumerate(headers_txt) if "gmp" in h), None)
        if day_col is None or gmp_col is None:
            continue
        gmp_days = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= max(day_col, gmp_col):
                continue
            mnum = re.search(r"([\d.]+)", cells[gmp_col])
            if mnum:
                gmp_days.append({"day": cells[day_col], "gmp": float(mnum.group(1))})
        if len(gmp_days) >= 2:
            detail["gmp_by_day"] = gmp_days
        break

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

    detail["ipo_news"] = news_future.result()

    if detail.get("gmp") is None and detail.get("gmp_pct") is None:
        gmp_info = _ai_google_gmp(detail.get("name") or "")
        for k, v in gmp_info.items():
            if v is not None and detail.get(k) is None:
                detail[k] = v

    scr = screener_future.result()
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
    if scr.get("roe_by_year") and not detail.get("roe_by_year"):
        detail["roe_by_year"] = scr["roe_by_year"]
        detail["roce_by_year"] = scr.get("roce_by_year")
        detail["ratio_years"] = scr.get("ratio_years")

    # ── Valuation Matrix: Pre-IPO vs Post-IPO (additive, best-effort) ───────
    # Needs pre/post-issue share counts (scraped above) plus the issue price
    # and latest PAT. Any missing input simply omits that part of the matrix —
    # nothing here is estimated or fabricated, and nothing above is altered.
    try:
        issue_price_val = to_float(detail.get("issue_price"))
        if issue_price_val is None:
            _plo, _phi = _parse_price_band(detail.get("price_band_str"))
            issue_price_val = _phi or _plo
        pre_shares = to_float(re.sub(r"[^\d.]", "", detail.get("pre_issue_shares_str") or ""))
        post_shares = to_float(re.sub(r"[^\d.]", "", detail.get("post_issue_shares_str") or ""))
        fresh_amt_cr = _parse_money_inr(detail.get("fresh_issue_str") or "")
        if post_shares is None and pre_shares is not None and fresh_amt_cr is not None and issue_price_val:
            post_shares = pre_shares + (fresh_amt_cr * 1e7) / issue_price_val
        latest_pat_cr = next((f["pat_cr"] for f in reversed(detail.get("financials") or [])
                               if f.get("pat_cr") is not None), None)
        val_matrix = {}
        if issue_price_val and latest_pat_cr is not None:
            latest_pat_rs = latest_pat_cr * 1e7
            if pre_shares and pre_shares > 0:
                eps_pre = latest_pat_rs / pre_shares
                val_matrix["pre"] = {"eps": round(eps_pre, 2),
                                      "pe": round(issue_price_val / eps_pre, 2) if eps_pre > 0 else None,
                                      "market_cap_cr": round(pre_shares * issue_price_val / 1e7, 1)}
            if post_shares and post_shares > 0:
                eps_post = latest_pat_rs / post_shares
                val_matrix["post"] = {"eps": round(eps_post, 2),
                                       "pe": round(issue_price_val / eps_post, 2) if eps_post > 0 else None,
                                       "market_cap_cr": round(post_shares * issue_price_val / 1e7, 1)}
        if val_matrix:
            detail["valuation_matrix"] = val_matrix
    except Exception as e:
        logger.debug("Valuation matrix computation skipped for %s: %s", detail.get("name"), e)

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

def render_ipo_timeline(detail: dict) -> None:
    """Horizontal step timeline: Open -> Close -> Allotment -> Refund -> Demat Credit -> Listing.
    Only renders steps where a date string was actually found; skips entirely
    if nothing beyond open/close is known (avoids a mostly-empty widget for
    IPOs where the aggregator hasn't published post-close dates yet)."""
    steps = [
        ("Opens",        detail.get("open_date_str") or detail.get("date")),
        ("Closes",       detail.get("close_date_str")),
        ("Allotment",    detail.get("allotment_date_str")),
        ("Refund Init.", detail.get("refund_date_str")),
        ("Demat Credit", detail.get("demat_credit_date_str")),
        ("Listing",      detail.get("listing_date_str")),
    ]
    known = [(label, val) for label, val in steps if val and str(val).strip() not in ("", "—", "N/A", "TBA")]
    if len(known) < 3:
        return  # not enough real data to make a timeline worth showing

    today = datetime.today().date()
    cells = []
    for label, val in known:
        d = _parse_date_flex(val)
        is_past = bool(d and d < today)
        is_today = bool(d and d == today)
        dot_color = GREEN if is_past else (ORANGE if is_today else MUTED)
        text_color = TEXT_BODY if (is_past or is_today) else MUTED
        cells.append(
            f"<div style='flex:1;min-width:100px;text-align:center;padding:0 4px;'>"
            f"<div style='width:10px;height:10px;border-radius:50%;background:{dot_color};"
            f"margin:0 auto 6px auto;'></div>"
            f"<div style='font-size:0.72em;color:{MUTED};'>{html_escape(label)}</div>"
            f"<div style='font-size:0.82em;color:{text_color};font-weight:600;'>{html_escape(str(val))}</div>"
            f"</div>"
        )
    line_color = BORDER
    st.markdown(
        f"<div class='swf-card' style='margin-bottom:18px;padding:16px 12px;'>"
        f"<div style='font-size:0.85em;color:{MUTED};margin-bottom:12px;padding-left:4px;'>IPO Timeline</div>"
        f"<div style='display:flex;align-items:flex-start;position:relative;'>"
        f"<div style='position:absolute;top:5px;left:5%;right:5%;height:2px;background:{line_color};z-index:0;'></div>"
        f"<div style='display:flex;width:100%;position:relative;z-index:1;'>{''.join(cells)}</div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


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


def render_ipo_subscription_buildup_chart(detail: dict) -> None:
    """Stacked bar chart: day-by-day subscription buildup (Retail vs NII vs QIB).
    Only renders when the source page exposed day-level granularity — see the
    subscription_by_day best-effort parse in fetch_ipo_detail. Complements (does not
    replace) render_ipo_subscription_chart's final-snapshot view above."""
    sub_days = detail.get("subscription_by_day")
    if not sub_days or len(sub_days) < 2:
        return
    days = [d.get("day", "") for d in sub_days]
    fig = go.Figure()
    for key, label, clr in [("retail", "Retail", BLUE), ("nii", "NII/HNI", ORANGE), ("qib", "QIB", GREEN)]:
        ys = [d.get(key) for d in sub_days]
        if all(y is None for y in ys):
            continue
        fig.add_trace(go.Bar(x=days, y=[y or 0 for y in ys], name=label, marker_color=clr))
    if not fig.data:
        return
    fig.update_layout(
        title="Day-by-Day Subscription Buildup",
        barmode="stack",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=280, margin=dict(t=40, b=20, l=10, r=10),
        yaxis=dict(ticksuffix="x", gridcolor="#2d333b"),
        legend=dict(orientation="h", y=1.2, font=dict(size=10)),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_gmp_trend_chart(detail: dict) -> None:
    """Line chart: historical GMP trajectory (₹) and estimated listing-gain % on a
    secondary axis, when the source page exposes day-level GMP history. Falls back
    to a single-point snapshot of the current GMP if no trend is available, so the
    Institutional-depth 'GMP Trend & Subscription' section always has something to
    show rather than silently disappearing."""
    gmp_days = detail.get("gmp_by_day")
    issue_price = to_float(detail.get("issue_price")) or to_float((detail.get("price_band_str") or "").split("-")[-1])
    if gmp_days and len(gmp_days) >= 2:
        days = [d.get("day", "") for d in gmp_days]
        gmps = [d.get("gmp") for d in gmp_days]
    elif detail.get("gmp") is not None:
        days, gmps = ["Latest"], [detail.get("gmp")]
    else:
        return
    est_gain = [round((g / issue_price) * 100, 1) if (g is not None and issue_price) else None for g in gmps]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=days, y=gmps, mode="lines+markers", name="GMP (₹)",
                              line=dict(color=GOLD, width=2), yaxis="y1"))
    if any(v is not None for v in est_gain):
        fig.add_trace(go.Scatter(x=days, y=est_gain, mode="lines+markers", name="Est. Listing Gain (%)",
                                  line=dict(color=GREEN, width=2, dash="dot"), yaxis="y2"))
    fig.update_layout(
        title="GMP Trend & Estimated Listing Gain",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        height=280, margin=dict(t=40, b=20, l=10, r=40),
        yaxis=dict(title="GMP (₹)", gridcolor="#2d333b"),
        yaxis2=dict(title="Est. Gain (%)", overlaying="y", side="right", ticksuffix="%", showgrid=False),
        legend=dict(orientation="h", y=1.2, font=dict(size=10)),
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


# ============================================================
# INSTITUTIONAL IPO REPORT UPGRADE (additive — Tier-1 style sections)
# ============================================================

def render_ipo_key_details_table(detail: dict) -> None:
    """Section 1 — Key Details & Issue Size: a clean metric-row/table covering
    Total Issue Size, Fresh Issue vs. OFS split, Price Band, Lot Size, and the
    full date sequence. Complements (does not replace) the existing offer-
    structure donut chart."""
    rows = [
        ("Total Issue Size", detail.get("issue_size_str")),
        ("Fresh Issue", detail.get("fresh_issue_str")),
        ("Offer for Sale (OFS)", detail.get("ofs_str")),
        ("Price Band", detail.get("price_band_str")),
        ("Lot Size", detail.get("lot_size")),
        ("Face Value", detail.get("face_value")),
        ("Open Date", detail.get("open_date_str")),
        ("Close Date", detail.get("close_date_str")),
        ("Allotment Date", detail.get("allotment_date_str")),
        ("Listing Date", detail.get("listing_date_str")),
    ]
    rows = [(label, val) for label, val in rows if val not in (None, "", "—")]
    if not rows:
        st.caption("Key issue details unavailable for this IPO.")
        return
    cells = "".join(
        f"<div style='flex:1 1 220px; padding:10px 14px; border-bottom:1px solid {BORDER};'>"
        f"<div style='color:{MUTED}; font-size:0.72em; font-weight:700; text-transform:uppercase; letter-spacing:0.4px;'>"
        f"{html_escape(label)}</div>"
        f"<div style='color:{TEXT}; font-size:1.02em; font-weight:700; margin-top:3px;'>{html_escape(str(val))}</div>"
        f"</div>"
        for label, val in rows
    )
    st.markdown(
        f"<div class='swf-card' style='padding:4px;'>"
        f"<div style='display:flex; flex-wrap:wrap;'>{cells}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_ipo_3yr_financials_chart(fin_rows: list) -> None:
    """Section 3 — 3-Year Financials: a single grouped Plotly bar chart across
    Total Income, EBITDA, PAT, Assets, Net Worth, and Borrowings. Any metric
    missing from the source data is simply omitted as its own series rather
    than plotted as a fabricated zero."""
    if not fin_rows:
        return
    rows = fin_rows[-3:] if len(fin_rows) > 3 else fin_rows
    years = [f.get("year", "") for f in rows]
    series = [
        ("Total Income", "revenue_cr", BLUE),
        ("EBITDA", "ebitda_cr", GOLD),
        ("PAT", "pat_cr", GREEN),
        ("Total Assets", "assets_cr", "#8B7FD6"),
        ("Net Worth", "net_worth_cr", "#4FC3A1"),
        ("Borrowings", "borrowings_cr", RED),
    ]
    fig = go.Figure()
    plotted = 0
    for label, key, clr in series:
        vals = [f.get(key) for f in rows]
        if all(v is None for v in vals):
            continue
        fig.add_trace(go.Bar(name=label, x=years, y=[v or 0 for v in vals], marker_color=clr))
        plotted += 1
    if not plotted:
        st.caption("Detailed 3-year financials unavailable for this IPO.")
        return
    fig.update_layout(
        title="3-Year Financial Snapshot (₹ Cr)",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        barmode="group", height=340, margin=dict(t=40, b=20, l=10, r=10),
        legend=dict(orientation="h", y=1.18, font=dict(size=10)),
        yaxis=dict(gridcolor="#2d333b"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_ipo_margins_table(detail: dict, fin_rows: list) -> None:
    """Section 3 — Margins table: ROE, ROCE, PAT Margin, Debt/Equity by year.
    PAT Margin and Debt/Equity are derived directly from the financials rows
    (always computable once revenue/PAT or borrowings/net-worth are present).
    ROE/ROCE come from the best-effort ratios-table scrape and are shown only
    when found — never estimated."""
    if not fin_rows:
        return
    years = [f.get("year", "") for f in fin_rows]
    pat_margin = [round(f["pat_cr"] / f["revenue_cr"] * 100, 1)
                  if f.get("revenue_cr") and f.get("pat_cr") is not None and f["revenue_cr"] > 0 else None
                  for f in fin_rows]
    debt_equity = [round(f["borrowings_cr"] / f["net_worth_cr"], 2)
                   if f.get("net_worth_cr") and f.get("borrowings_cr") is not None and f["net_worth_cr"] > 0 else None
                   for f in fin_rows]
    roe_map, roce_map = {}, {}
    if detail.get("roe_by_year") and detail.get("ratio_years"):
        roe_map = dict(zip(detail["ratio_years"], detail["roe_by_year"]))
    if detail.get("roce_by_year") and detail.get("ratio_years"):
        roce_map = dict(zip(detail["ratio_years"], detail["roce_by_year"]))

    def _match_ratio(year_label, rmap):
        if year_label in rmap:
            return rmap[year_label]
        for k, v in rmap.items():
            if year_label and (year_label in k or k in year_label):
                return v
        return None

    metric_rows = [
        ("ROE (%)", [_match_ratio(y, roe_map) for y in years]),
        ("ROCE (%)", [_match_ratio(y, roce_map) for y in years]),
        ("PAT Margin (%)", pat_margin),
        ("Debt / Equity", debt_equity),
    ]
    metric_rows = [(label, vals) for label, vals in metric_rows if any(v is not None for v in vals)]
    if not metric_rows:
        return
    header_cells = "".join(f"<th style='padding:8px 12px; text-align:right; color:{MUTED}; font-size:0.78em;'>{html_escape(y)}</th>" for y in years)
    body_rows = ""
    for label, vals in metric_rows:
        val_cells = "".join(
            f"<td style='padding:8px 12px; text-align:right; color:{TEXT}; font-size:0.9em;'>{v if v is not None else '—'}</td>"
            for v in vals
        )
        body_rows += f"<tr style='border-top:1px solid {BORDER};'><td style='padding:8px 12px; color:{MUTED}; font-size:0.85em; font-weight:600;'>{html_escape(label)}</td>{val_cells}</tr>"
    st.markdown(
        f"<div class='swf-card' style='overflow-x:auto; padding:8px 4px;'>"
        f"<table style='width:100%; border-collapse:collapse;'>"
        f"<tr><th style='padding:8px 12px; text-align:left; color:{MUTED}; font-size:0.78em;'>Metric</th>{header_cells}</tr>"
        f"{body_rows}</table></div>",
        unsafe_allow_html=True,
    )


def render_ipo_valuation_matrix(detail: dict, currency="₹") -> None:
    """Section 4 — Valuation Matrix: Pre-IPO vs. Post-IPO EPS, P/E, and Market
    Cap, from the additive computation in fetch_ipo_detail. Renders nothing if
    the underlying share-count data wasn't available (never fabricated)."""
    vm = detail.get("valuation_matrix")
    if not vm or not (vm.get("pre") or vm.get("post")):
        return
    metric_labels = [("eps", "EPS"), ("pe", "P/E Ratio"), ("market_cap_cr", "Market Cap")]
    header_cells = "".join(
        f"<th style='padding:8px 12px; text-align:right; color:{MUTED}; font-size:0.78em;'>{label}</th>"
        for label in ["Pre-IPO", "Post-IPO"]
    )
    body_rows = ""
    for key, label in metric_labels:
        pre_v = vm.get("pre", {}).get(key)
        post_v = vm.get("post", {}).get(key)
        if key == "market_cap_cr":
            pre_s = f"{currency}{pre_v:,.1f} Cr" if pre_v is not None else "—"
            post_s = f"{currency}{post_v:,.1f} Cr" if post_v is not None else "—"
        elif key == "pe":
            pre_s = f"{pre_v:.2f}x" if pre_v is not None else "—"
            post_s = f"{post_v:.2f}x" if post_v is not None else "—"
        else:
            pre_s = f"{currency}{pre_v:.2f}" if pre_v is not None else "—"
            post_s = f"{currency}{post_v:.2f}" if post_v is not None else "—"
        body_rows += (
            f"<tr style='border-top:1px solid {BORDER};'>"
            f"<td style='padding:8px 12px; color:{MUTED}; font-size:0.85em; font-weight:600;'>{label}</td>"
            f"<td style='padding:8px 12px; text-align:right; color:{TEXT}; font-size:0.9em;'>{pre_s}</td>"
            f"<td style='padding:8px 12px; text-align:right; color:{GOLD}; font-size:0.9em; font-weight:700;'>{post_s}</td>"
            f"</tr>"
        )
    st.markdown(
        f"<div class='swf-card' style='overflow-x:auto; padding:8px 4px;'>"
        f"<table style='width:100%; border-collapse:collapse;'>"
        f"<tr><th style='padding:8px 12px; text-align:left; color:{MUTED}; font-size:0.78em;'>Metric</th>{header_cells}</tr>"
        f"{body_rows}</table>"
        f"<div style='padding:6px 12px 2px 12px; color:{MUTED}; font-size:0.75em;'>"
        f"Post-IPO figures are diluted for the fresh-issue share count at the upper price band.</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


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

    if st.button("← Back to IPO List"):
        st.session_state.selected_ipo = None
        st.session_state.ipo_detail   = None
        st.session_state.ipo_bucket   = None
        st.rerun()

    right = ""
    if bucket == "current" and verdict:
        right = (f"<div style='margin-bottom:4px;'>{verdict_pill(verdict)}</div>"
                 f"<div style='color:{MUTED};font-size:0.85em;'>Score: {score}/100</div>")
    elif bucket == "closed":
        gain = detail.get("listing_gain_pct")
        if gain is not None:
            gain_tone = "green" if gain >= 0 else "red"
            right = status_pill(f"Listing {gain:+.1f}%", tone=gain_tone)
        else:
            right = (f"<div style='color:{MUTED};'>Listing: "
                     f"{html_escape(str(detail.get('listing_date_str') or 'Pending'))}</div>")
    else:
        right = status_pill("Upcoming", tone="orange")

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

    # ── Headline metrics ────────────────────────────────────────────────────
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

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1 — KEY DETAILS & ISSUE SIZE
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("#### 1️⃣ Key Details & Issue Size")
    render_ipo_key_details_table(detail)
    extra_cols = st.columns(3)
    with extra_cols[0]:
        custom_metric("Offer Type", detail.get("offer_type") or "N/A")
    with extra_cols[1]:
        custom_metric("Registrar", detail.get("registrar") or "N/A")
    with extra_cols[2]:
        custom_metric("Lead Manager", detail.get("lead_manager") or "N/A")

    # ── Business overview ──────────────────────────────────────────────────
    card("Business Overview",
         f"<p style='color:{TEXT_BODY};font-size:0.9em;line-height:1.6;'>"
         f"{html_escape(str(detail.get('about') or 'Not available.'))}</p>")

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2 — GMP TREND & SUBSCRIPTION
    # ══════════════════════════════════════════════════════════════════════
    has_sub_data = any(detail.get(k) is not None for k in ("subscription_qib", "subscription_nii", "subscription_retail", "subscription_total"))
    if bucket != "upcoming" and (detail.get("gmp") is not None or has_sub_data):
        st.markdown("---")
        st.markdown("#### 2️⃣ GMP Trend & Subscription")
        render_ipo_gmp_trend_chart(detail)
        if has_sub_data:
            sub_col, _ = st.columns([2, 1])
            with sub_col:
                render_ipo_subscription_chart(detail)
            render_ipo_subscription_buildup_chart(detail)
            s1, s2, s3 = st.columns(3)
            with s1:
                custom_metric("QIB", f"{detail['subscription_qib']:.2f}x" if detail.get("subscription_qib") is not None else "N/A")
            with s2:
                custom_metric("NII", f"{detail['subscription_nii']:.2f}x" if detail.get("subscription_nii") is not None else "N/A")
            with s3:
                custom_metric("Retail", f"{detail['subscription_retail']:.2f}x" if detail.get("subscription_retail") is not None else "N/A")
    elif bucket == "upcoming":
        render_ipo_timeline(detail)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 3 — 3-YEAR FINANCIALS & PROFITABILITY
    # ══════════════════════════════════════════════════════════════════════
    fins = detail.get("financials") or []
    if fins:
        st.markdown("---")
        st.markdown("#### 3️⃣ 3-Year Financials & Profitability")
        render_ipo_3yr_financials_chart(fins)
        render_ipo_margins_table(detail, fins)

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
    elif bucket != "upcoming" and (detail.get("gmp") is not None or has_sub_data):
        # timeline wasn't shown above (that branch only fires for 'upcoming') --
        # show it here instead, since we're past the financials section now
        st.markdown("---")
        render_ipo_timeline(detail)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 4 — VALUATION MATRIX
    # ══════════════════════════════════════════════════════════════════════
    if detail.get("valuation_matrix"):
        st.markdown("---")
        st.markdown("#### 4️⃣ Valuation Matrix — Pre-IPO vs. Post-IPO")
        render_ipo_valuation_matrix(detail)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 5 — THE VERDICT & CONTEXT
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("#### 5️⃣ The Verdict & Context")

    # ── Strengths & Risks ──────────────────────────────────────────────────
    pc1, pc2 = st.columns(2)
    with pc1:
        p_html = "".join(
            f"<div style='padding:4px 0'><span style='color:{GREEN}'>✅ {html_escape(p)}</span></div>"
            for p in (pros or detail.get("strengths") or [])[:8]
        ) or f"<div style='color:{MUTED}'>No strengths extracted.</div>"
        card("Key Strengths", p_html)
    with pc2:
        c_html = "".join(
            f"<div style='padding:4px 0'><span style='color:{RED}'>⚠️ {html_escape(c)}</span></div>"
            for c in (cons or detail.get("risks") or [])[:8]
        ) or f"<div style='color:{MUTED}'>No material risks extracted.</div>"
        card("Key Risks", c_html)

    # ── AI Research Note ───────────────────────────────────────────────────
    with st.spinner("Generating AI note..."):
        narr = ipo_ai_narrative(detail, score, verdict, pros, cons, bucket=bucket)
    card("Should You Invest? — AI Research Note",
         f"<p style='color:{TEXT_BODY};font-size:0.9em;line-height:1.6;white-space:pre-wrap;'>"
         f"{style_verdict_text(narr)}</p>")
