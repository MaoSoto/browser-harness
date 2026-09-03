import sys
import os
import json
_HARNESS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)
from helpers import *
import urllib.request
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

from selector_manager import get_domain_selectors


WORKPLACE_TOKEN_MAP = {
    "on-site": "onsite",
    "onsite": "onsite",
    "in-person": "onsite",
    "in person": "onsite",
    "remote": "remote",
    "hybrid": "hybrid",
}


def stripHeaderPreamble(text, title, company):
    """Remove the metadata header (title, company, location, salary, posted) from description text.
    Cuts everything before the 'Job description' marker, or the first body sentence if marker missing."""
    if not text:
        return text
    lines = text.split("\n")
    cut_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() in {"job description", "about the job", "description", "position summary", "summary"}:
            cut_idx = i + 1
            break
    if cut_idx is None:
        # Fall back: drop lines until we find a sentence that doesn't look like metadata
        meta_pattern = re.compile(
            r"^("
            r"\$[\d,.\s/-]+(?:/?(?:hr|yr|year|hour|month|k))?"  # salary
            r"|full[-\s]?time|part[-\s]?time|contract|temporary|internship|seasonal|freelance"  # employment
            r"|posted\s.*|today|yesterday|just posted"  # posted
            r"|1-click apply|apply now|apply"  # apply
            r"|.*[•·\|]\s*(remote|hybrid|on-?site|in-?person)"  # location with workplace
            r")",
            re.IGNORECASE,
        )
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if s == (title or "").strip():
                continue
            if s == (company or "").strip():
                continue
            if meta_pattern.match(s):
                continue
            cut_idx = i
            break
    if cut_idx is None or cut_idx == 0:
        return text
    return "\n".join(lines[cut_idx:]).lstrip("\n")


def cleanDescription(text_or_html):
    if not text_or_html:
        return ""
    s = text_or_html
    if "<" in s and ">" in s:
        s = re.sub(r"<(script|style).*?>.*?</\1>", "", s, flags=re.DOTALL | re.IGNORECASE)
        for tag in ["p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]:
            s = re.sub(rf"<{tag}[^>]*>", "\n", s, flags=re.IGNORECASE)
            s = re.sub(rf"</{tag}>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<[^>]+>", "", s)
        try:
            import html as _html
            s = _html.unescape(s)
        except Exception:
            pass

    s = re.sub(r"^[\s*\-_=•·]+", "", s)
    s = re.sub(r"\*\*\s*\*\*", "", s)
    s = re.sub(r"^\s*\*\s*$", "", s, flags=re.MULTILINE)
    s = re.sub(r"\bamp\.{2,}\s*", "", s)
    s = re.sub(r"\.{2,}\s*(\d+\.\s+)", r". \1", s)
    s = re.sub(r"\.{2,}", ".", s)

    redundant = {
        "job description", "about the job", "summary", "position summary",
        "posted today", "posted", "apply", "1-click apply",
        "job seekers", "small & medium businesses", "enterprise businesses",
        "partner with us", "company",
    }
    raw_lines = s.split("\n")
    cleaned_lines = []
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        low = line.lower().strip("* :.-'\"")
        if low in redundant:
            continue
        if re.fullmatch(r"[\*\s_\-=•·'\"\.\d]{1,8}", line):
            continue
        if re.fullmatch(r"[\*\s_\-=•·]{3,}", line):
            continue
        if "breakroom" in low and ("quiz" in low or "powered by" in low):
            continue
        if low.startswith("view more about working here"):
            continue
        # Skip rating/marketing lines
        if "rating" in low and len(line) < 80:
            continue
        if re.match(r"^\d+(st|nd|rd|th)\s+of\s+\d+", low):
            continue
        if line.startswith("- "):
            content = line[2:]
        else:
            content = line
        cleaned_lines.append("- " + content)

    out = "\n".join(cleaned_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def solve_posted_at(relative_text):
    """Convert relative dates like 'Posted 5 hours ago', 'Posted yesterday' to ISO 8601."""
    if not relative_text:
        return None
    if "T" in relative_text and (relative_text.endswith("Z") or "+" in relative_text):
        return relative_text
    text = relative_text.lower()
    now = datetime.utcnow().replace(microsecond=0)
    if "just posted" in text or "today" in text:
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if "yesterday" in text:
        return (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = re.search(r"(\d+)\+?\s*(hour|day|week|month|year|min|hr)s?\s*ago", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("hour") or unit == "hr":
            return (now - timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if unit.startswith("day"):
            return (now - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if unit.startswith("week"):
            return (now - timedelta(weeks=n)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if unit.startswith("month"):
            return (now - timedelta(days=n * 30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if unit.startswith("year"):
            return (now - timedelta(days=n * 365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def get_external_id_from_url(url):
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "jid" in qs and qs["jid"]:
        return qs["jid"][0]
    m = re.search(r"jid=([a-zA-Z0-9]+)", url)
    if m:
        return m.group(1)
    return None


def identify_ats(url):
    if not url:
        return None
    host = url.lower()
    mapping = [
        ("myworkdayjobs.com", "Workday"),
        ("workday", "Workday"),
        ("greenhouse.io", "Greenhouse"),
        ("lever.co", "Lever"),
        ("icims.com", "iCIMS"),
        ("smartrecruiters.com", "SmartRecruiters"),
        ("taleo.net", "Taleo"),
        ("jobvite.com", "Jobvite"),
        ("bamboohr.com", "BambooHR"),
        ("ashbyhq.com", "Ashby"),
        ("teamtailor.com", "Teamtailor"),
        ("recruitee.com", "Recruitee"),
        ("paylocity.com", "Paylocity"),
        ("ultipro.com", "UKG"),
        ("ukg.com", "UKG"),
        ("dayforce.com", "Dayforce"),
        ("ceridian.com", "Dayforce"),
        ("successfactors.com", "SAP SuccessFactors"),
        ("adp.com", "ADP"),
    ]
    for needle, name in mapping:
        if needle in host:
            return name
    return None


def find_job_from_react_fiber():
    """Walk React fiber tree. Prefer the job object with the richest payload
    (has pay, location, buttonConfig) to skip sidebar related jobs."""
    return js(
        "(()=>{"
        "const all=document.querySelectorAll('button, a');"
        "let best=null;let bestScore=0;"
        "for(const el of all){"
        "  const k=Object.keys(el).find(k=>k.startsWith('__reactFiber'));"
        "  if(!k)continue;"
        "  let node=el[k];let depth=0;"
        "  while(node && depth<40){"
        "    const p=node.memoizedProps;"
        "    if(p && p.job && p.job.title && p.job.company && p.job.company.name){"
        "      const score=(p.job.pay?4:0)+(p.job.location?2:0)+(p.job.buttonConfig?3:0)+(p.job.status?1:0);"
        "      if(score>bestScore){bestScore=score;best=p.job;}"
        "    }"
        "    node=node.return;depth++;"
        "  }"
        "}"
        "return best?JSON.stringify(best):null;"
        "})()"
    )


def extract_jsonld_itemlist():
    try:
        raw = js("document.querySelector('script[type=\"application/ld+json\"]')?.innerText")
    except Exception:
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed.get("itemListElement", []) or []
        if isinstance(parsed, list):
            for el in parsed:
                if isinstance(el, dict) and el.get("@type") == "ItemList":
                    return el.get("itemListElement", []) or []
    except Exception:
        return []
    return []


def find_apply_url_in_pane(right_pane_sel):
    """Find the actual Apply redirect URL inside the right pane (excluding share links)."""
    return js(
        "(()=>{const r=document.querySelector(" + json.dumps(right_pane_sel) + ");"
        "if(!r) return null;"
        "for(const a of r.querySelectorAll('a')){"
        "  const h=a.href||'';"
        "  if(!h.toLowerCase().includes('/job-redirect?')) continue;"
        "  const u=h.toLowerCase();"
        "  if(u.includes('sharer.php')||u.includes('intent/tweet')||u.includes('share-offsite')||u.includes('mailto:')||u.includes('/share')) continue;"
        "  return h;"
        "}"
        "return null;})()"
    )


def find_company_url_in_pane(right_pane_sel):
    """Find first /co/<Slug> anchor in the right pane. Strips /Jobs and query params to derive canonical /co/<slug>."""
    return js(
        "(()=>{const r=document.querySelector(" + json.dumps(right_pane_sel) + ");"
        "if(!r) return null;"
        "const seen=new Set();"
        "for(const a of r.querySelectorAll('a')){"
        "  const h=a.href||'';"
        "  if(!h.includes('/co/')) continue;"
        "  let u=h.split('?')[0].split('#')[0];"
        "  u=u.replace(/\\/Jobs$/i,'');"
        "  if(!/^https:\\/\\/www\\.ziprecruiter\\.com\\/co\\/[^/]+$/.test(u)) continue;"
        "  if(seen.has(u)) continue;"
        "  seen.add(u);"
        "  return u;"
        "}"
        "return null;})()"
    )


def find_company_logo_in_pane(right_pane_sel):
    """Find company logo image inside the right pane header."""
    return js(
        "(()=>{const r=document.querySelector(" + json.dumps(right_pane_sel) + ");"
        "if(!r) return null;"
        "for(const img of r.querySelectorAll('img')){"
        "  const s=img.src||'';"
        "  if(s && (s.includes('fotomat/public')||s.includes('company')||s.includes('logo'))) return s;"
        "}"
        "return null;})()"
    )


def extract_company_data_block():
    """Read company-data block from the company profile page."""
    cd_text = js('document.querySelector("[data-testid=\\"company-data\\"]")?.innerText || ""')
    info = {
        "description": None,
        "industries": [],
        "employees_count": None,
        "addr_locality": None,
        "addr_region": None,
        "addr_country": None,
        "website": None,
        "linkedin_url": None,
        "logo_url": None,
    }

    if cd_text:
        parts = cd_text.split("\n")
        desc_lines = []
        for ln in parts:
            if ln.strip().lower().startswith("industry"):
                break
            desc_lines.append(ln.strip())
        info["description"] = " ".join(desc_lines).strip() or None

        ind_match = re.search(r"Industry\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
        if ind_match:
            info["industries"] = [ind_match.group(1).strip()]

        size_match = re.search(r"Company size\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
        if size_match:
            num = re.search(r"(\d[\d,]*)", size_match.group(1))
            if num:
                try:
                    info["employees_count"] = int(num.group(1).replace(",", ""))
                except Exception:
                    pass

        hq_match = re.search(r"Headquarters location\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
        if hq_match:
            hq = hq_match.group(1).strip()
            parts_hq = [p.strip() for p in hq.split(",")]
            if len(parts_hq) >= 3 and parts_hq[-1].upper() in {"US", "USA", "UK", "CA", "AU"}:
                info["addr_country"] = parts_hq[-1]
                info["addr_region"] = parts_hq[-2]
                info["addr_locality"] = parts_hq[-3] if len(parts_hq) >= 3 else parts_hq[0]
            elif len(parts_hq) >= 2:
                info["addr_region"] = parts_hq[-1]
                info["addr_locality"] = parts_hq[0]
            elif parts_hq:
                info["addr_locality"] = parts_hq[0]

    info["website"] = js("""
    (() => {
        const cd = document.querySelector('[data-testid="company-data"]');
        if (!cd) return null;
        const anchors = Array.from(cd.querySelectorAll('a[href^="http"]'));
        const found = anchors.map(a => a.href).find(h => {
            const u = (h || '').toLowerCase();
            return !u.includes('ziprecruiter') && !u.includes('facebook.com') && 
                   !u.includes('twitter.com') && !u.includes('instagram.com') && 
                   !u.includes('linkedin.com') && !u.includes('breakroom.cc') && 
                   !u.includes('ketchcdn.com');
        });
        return found || null;
    })()
    """)
    info["linkedin_url"] = js("""
    (() => {
        const cd = document.querySelector('[data-testid="company-data"]');
        if (!cd) return null;
        const anchors = Array.from(cd.querySelectorAll("a[href*='linkedin.com/company']"));
        const found = anchors.map(a => a.href).find(h => {
            const u = (h || '').toLowerCase();
            return !u.includes('ziprecruiter') && !u.includes('/company/ziprecruiter');
        });
        return found || null;
    })()
    """)
    info["logo_url"] = js("""
    (() => {
        const cd = document.querySelector('[data-testid="company-data"]');
        const img = cd ? cd.querySelector('img') : null;
        if (img && img.src && !img.src.includes('public-nosensitive-ziprecruiter-logos') && !img.src.includes('ziprecruiter.com/assets')) {
            return img.src;
        }
        return null;
    })()
    """)
    return info


def parse_right_pane(right_text, company_name, target_title=None):
    """Parse right-pane text into structured fields. If `target_title` is supplied,
    isolate the section whose title line matches (the right-pane may include multiple
    rendered jobs when navigation is mid-flight)."""
    out = {
        "title": None,
        "company": None,
        "location": None,
        "workplace_type": "onsite",
        "is_remote": False,
        "salary": None,
        "employment_type": None,
        "benefits": [],
        "posted_at_raw": None,
        "description_lines": [],
    }
    lines = [ln.strip() for ln in (right_text or "").split("\n") if ln.strip()]
    if not lines:
        return out

    # If multiple sections exist (delimited by "Job description"), pick the one matching target_title
    sections = []
    current = []
    for ln in lines:
        current.append(ln)
        if ln.lower() == "job description":
            # Section ends at this marker; close section
            sections.append(current)
            current = []
    if current:
        sections.append(current)

    chosen = None
    if target_title:
        for sec in sections:
            if sec and sec[0].strip() == target_title:
                chosen = sec
                break
    if not chosen:
        # Pick the LARGEST section (most content)
        chosen = max(sections, key=len) if sections else sections[0]

    if not chosen:
        return out

    lines = chosen
    out["title"] = lines[0]
    out["company"] = lines[1] if len(lines) > 1 else None

    # Find header block lines (until "Job description" marker)
    header_end = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() == "job description":
            header_end = i
            break
    header = lines[2:header_end] if header_end else lines[2:]
    body = lines[header_end + 1:] if header_end else []

    # Location line usually contains "•"
    location = None
    workplace_type = None
    is_remote = False
    for ln in header:
        if "•" in ln:
            parts = [p.strip() for p in ln.split("•")]
            for p in parts:
                if not p:
                    continue
                low = p.lower().strip()
                if low in {"apply", "1-click apply"}:
                    continue
                if low in WORKPLACE_TOKEN_MAP:
                    workplace_type = WORKPLACE_TOKEN_MAP[low]
                    if workplace_type == "remote":
                        is_remote = True
                    continue
                if not location:
                    location = p
            if location or workplace_type:
                break

    # Adversarial: location must NOT contain company name
    if location and out["company"] and out["company"].lower() in location.lower():
        if "," in location:
            location = location.split(",", 1)[1].strip() or None
        else:
            location = None

    out["location"] = location
    out["workplace_type"] = workplace_type or "onsite"
    out["is_remote"] = is_remote

    # Salary line
    for ln in header:
        m = re.search(
            r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:[Kk])?\s*(?:[-–—]\s*\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:[Kk])?\s*)?(?:\s*\/?\s*(?:hr|hour|year|yr|month|k))?",
            ln,
        )
        if m:
            out["salary"] = m.group(0).strip()
            break

    # Employment type
    for ln in header:
        m = re.search(
            r"\b(Full[-\s]?time|Part[-\s]?time|Contract|Temporary|Internship|Seasonal|Freelance|Commission)\b",
            ln,
            re.IGNORECASE,
        )
        if m:
            out["employment_type"] = m.group(0).replace("  ", " ").strip()
            break

    # Benefits line: comma-separated perks (avoid rating/marketing lines)
    benefit_keywords = {
        "medical", "dental", "vision", "life", "retirement", "pto",
        "paid time off", "401k", "401(k)", "flexible", "parental",
        "wellness", "commuter", "tuition", "discount", "bonus",
    }
    for ln in header:
        # Must contain a comma (real benefits are comma-separated)
        if "," in ln and re.match(r"^[A-Z][\w&]+(?:[,\s][A-Z][\w&\s]+){2,}$", ln):
            perks = [p.strip() for p in ln.split(",")]
            generic = {"apply", "1-click apply", "today", "posted", "new", "share"}
            perks = [p for p in perks if p.lower() not in generic and len(p) > 1]
            filtered = [p for p in perks if any(k in p.lower() for k in benefit_keywords)]
            if filtered:
                out["benefits"] = filtered
                break
            # Fallback: accept all if no keyword matches
            if perks:
                out["benefits"] = perks
                break

    # Posted at
    for ln in header:
        low = ln.lower()
        if "posted" in low or "today" in low or "yesterday" in low or "just posted" in low:
            out["posted_at_raw"] = ln
            break

    out["description_lines"] = body
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 job-scraper.py <url>")
        sys.exit(1)
    input_url = sys.argv[1]

    original_tid = None
    try:
        for t in list_tabs():
            if t.get("activated"):
                original_tid = t["targetId"]
                break
    except Exception:
        pass

    domain_cfg = get_domain_selectors("ziprecruiter.com") if "get_domain_selectors" in globals() else {}
    right_pane_sel = domain_cfg.get("right_pane_selector", '[data-testid="right-pane"]')
    desc_sel = domain_cfg.get("description_selector", '[data-testid="job-details-scroll-container"]')

    tabs = list_tabs()
    was_already_open = any(input_url.split('?')[0] in t.get("url", "") for t in tabs)

    # Step 1: Open the input URL (only if not already open)
    job_tid = new_tab(input_url, activate=False)
    if not was_already_open:
        goto_url(input_url)
        wait_for_load()
        time.sleep(3)
    else:
        time.sleep(0.5)

    # Right-pane text — primary source for everything
    right_text = js(f"document.querySelector({json.dumps(right_pane_sel)})?.innerText || \"\"")

    # Fallback: use job-details-scroll-container text
    if not right_text:
        right_text = js(f"document.querySelector({json.dumps(desc_sel)})?.innerText || \"\"")

    # Title from right-pane (precision zone)
    title = js(
        f"Array.from(document.querySelectorAll({json.dumps(right_pane_sel)} h2, {json.dumps(right_pane_sel)} h1)).map(e=>e.innerText).find(t=>t && t.length<200 && !/job description|job seekers|partner|company|enterprise|small/i.test(t)) || null"
    )
    if not title and right_text:
        # First line is title
        first_line = right_text.split("\n", 1)[0].strip()
        if first_line and len(first_line) < 200 and "job description" not in first_line.lower():
            title = first_line

    # Find company URL and apply URL from right-pane anchors
    company_zr_url = find_company_url_in_pane(right_pane_sel)
    company_logo_pane = find_company_logo_in_pane(right_pane_sel)
    apply_redirect_url = find_apply_url_in_pane(right_pane_sel)

    # Try to enhance with React fiber data (best-effort, may fail)
    single_job_data = {}
    try:
        # Walk fibers on current page first (search page may have it for current selected job)
        fiber_raw = find_job_from_react_fiber()
        if fiber_raw:
            try:
                single_job_data = json.loads(fiber_raw)
            except Exception:
                single_job_data = {}
    except Exception:
        pass

    # Parse right-pane structure — right-pane is the authoritative source for title/description
    company_name_hint = (single_job_data.get("company", {}) or {}).get("name")
    parsed = parse_right_pane(right_text, company_name_hint, target_title=title)

    # ---------- Title ----------
    # Prefer right-pane title (the actual currently selected job), then fiber, then parsed.
    title = (
        title
        or parsed["title"]
        or single_job_data.get("title")
        or ""
    ).strip() or None

    # ---------- Company ----------
    company_obj = single_job_data.get("company", {}) or {}
    # Prefer right-pane parsed company name (matches the visible job), then fiber.
    company_name = (
        parsed["company"]
        or company_obj.get("name")
        or company_obj.get("canonicalDisplayName")
    )
    # If fiber disagrees with right-pane company, prefer right-pane
    if company_name and parsed["company"] and parsed["company"].lower() != company_name.lower():
        company_name = parsed["company"]

    if not company_zr_url and company_obj.get("companyUrl"):
        cu = company_obj["companyUrl"]
        company_zr_url = f"https://www.ziprecruiter.com{cu}" if cu.startswith("/") else cu
    company_logo = company_obj.get("companyLogo", {}).get("logoUrl") if company_obj else None

    # ---------- Posted At ----------
    status = single_job_data.get("status", {}) or {}
    posted_at = status.get("postedAtUtc") or status.get("rollingPostedAtUtc")
    if not posted_at:
        posted_at = solve_posted_at(parsed["posted_at_raw"])

    # ---------- Salary ----------
    salary = parsed.get("salary")
    if not salary:
        pay = single_job_data.get("pay", {}) or {}
        if pay.get("min") is not None and pay.get("max") is not None:
            interval = (pay.get("interval") or "").replace("PAY_INTERVAL_", "").lower()
            mn, mx = pay["min"], pay["max"]
            if interval == "hour":
                salary = f"${mn} - ${mx}/hr"
            elif interval == "year":
                salary = f"${int(mn)} - ${int(mx)}/yr"
            elif interval == "month":
                salary = f"${mn} - ${mx}/mo"
            else:
                salary = f"${mn} - ${mx}"

    # ---------- Location ----------
    loc = single_job_data.get("location", {}) or {}
    location_parts = [loc.get("city"), loc.get("state")]
    location_text = ", ".join(p for p in location_parts if p) or parsed["location"]

    # ---------- Workplace type ----------
    workplace_type, is_remote = ("onsite", False)
    lt_list = single_job_data.get("locationTypes", []) or []
    for lt in lt_list:
        nm = (lt.get("name") or "").upper()
        if "REMOTE" in nm:
            workplace_type, is_remote = "remote", True
            break
        if "HYBRID" in nm:
            workplace_type, is_remote = "hybrid", False
            break
        if "IN_PERSON" in nm or "ONSITE" in nm:
            workplace_type, is_remote = "onsite", False
            break
    if workplace_type == "onsite" and parsed["workplace_type"] != "onsite":
        workplace_type = parsed["workplace_type"]
        is_remote = parsed["is_remote"]

    # ---------- Employment type ----------
    et_list = single_job_data.get("employmentTypes", []) or []
    employment_type = parsed["employment_type"]
    if et_list:
        name = (et_list[0].get("name") or "").replace("EMPLOYMENT_TYPE_NAME_", "").replace("_", "-").title()
        if name == "Full-Time":
            employment_type = "Full-time"
        elif name == "Part-Time":
            employment_type = "Part-time"
        else:
            employment_type = name

    # ---------- Benefits ----------
    benefits_list = single_job_data.get("benefits", []) or []
    benefit_map = {
        "BENEFIT_TYPE_NAME_MEDICAL": "Medical",
        "BENEFIT_TYPE_NAME_DENTAL": "Dental",
        "BENEFIT_TYPE_NAME_VISION": "Vision",
        "BENEFIT_TYPE_NAME_LIFE": "Life Insurance",
        "BENEFIT_TYPE_NAME_PAID_TIME_OFF": "PTO",
        "BENEFIT_TYPE_NAME_RETIREMENT": "Retirement",
        "BENEFIT_TYPE_NAME_401K": "401K",
    }
    perks = []
    for b in benefits_list:
        bn = b.get("name") or ""
        perks.append(benefit_map.get(bn, bn.replace("BENEFIT_TYPE_NAME_", "").replace("_", " ").title()))
    if not perks and parsed["benefits"]:
        perks = parsed["benefits"]
    benefits = {"perks": perks} if perks else {}

    # ---------- Apply URL ----------
    apply_url = None
    btn_cfg = single_job_data.get("buttonConfig") or {}
    if btn_cfg.get("externalApplyUrl"):
        apply_url = btn_cfg["externalApplyUrl"]
    elif single_job_data.get("applyButtonConfig", {}).get("externalApplyUrl"):
        apply_url = single_job_data["applyButtonConfig"]["externalApplyUrl"]
    if not apply_url:
        apply_url = apply_redirect_url

    ats_vendor = identify_ats(apply_url)

    if apply_url and "ziprecruiter.com/job-redirect" in apply_url.lower() and not ats_vendor:
        try:
            tmp_tid = new_tab(apply_url, activate=False)
            goto_url(apply_url)
            time.sleep(4)
            final = page_info().get("url")
            if final and "ziprecruiter.com" not in final.lower():
                apply_url = final
                ats_vendor = identify_ats(apply_url)
            close_tab(tmp_tid)
        except Exception:
            pass

    # ---------- Description ----------
    # Try the dedicated job-details-scroll-container first (cleanest body); fall back to right-pane.
    desc_raw = js(f"document.querySelector({json.dumps(desc_sel)})?.innerText || \"\"")
    if not desc_raw:
        desc_raw = right_text
    # Strip the metadata header preamble so description starts at the actual job body
    desc_body = stripHeaderPreamble(desc_raw, title, company_name)
    desc_text = cleanDescription(desc_body)
    if title:
        dl = desc_text.split("\n")
        while dl and dl[0].strip() in {f"- {title}", title, f"- {title.strip()}"}:
            dl.pop(0)
        desc_text = "\n".join(dl).lstrip("\n")

    # Adversarial: ensure location doesn't contain company name
    if location_text and company_name and company_name.lower() in location_text.lower():
        if "," in location_text:
            tail = location_text.split(",", 1)[1].strip()
            location_text = tail or None

    # ---------- Canonical URL ----------
    # Try JSON-LD match by title (may 404 if jid is stale - that's OK, we still use it as identifier)
    canonical_url = None
    ld_items = extract_jsonld_itemlist()
    if title and ld_items:
        for item in ld_items:
            if isinstance(item, dict) and item.get("name") == title:
                canonical_url = item.get("url")
                break
    if not canonical_url:
        p = urlparse(input_url)
        if "/jobs/" in p.path or "/Job/" in p.path:
            canonical_url = input_url
        elif ld_items:
            canonical_url = ld_items[0].get("url")
        else:
            canonical_url = input_url

    external_id = get_external_id_from_url(canonical_url)
    if not external_id:
        # Fallback: use listing_key or match_id from fiber
        external_id = single_job_data.get("listingKey") or single_job_data.get("matchId")

    # ---------- Company Profile (background) ----------
    company_info = {
        "description": None,
        "industries": [],
        "employees_count": None,
        "addr_locality": None,
        "addr_region": None,
        "addr_country": None,
        "website": None,
        "linkedin_url": None,
        "logo_url": None,
    }
    if company_zr_url:
        try:
            comp_tid = new_tab(company_zr_url, activate=False)
            goto_url(company_zr_url)
            wait_for_load()
            time.sleep(3)
            company_info = extract_company_data_block()
            close_tab(comp_tid)
        except Exception:
            pass

    # ---------- Build Result ----------
    job_obj = {
        "source": "ZipRecruiter",
        "external_id": external_id,
        "url": canonical_url,
        "input_url": input_url,
        "apply_url": apply_url,
        "title": title,
        "employment_type": employment_type,
        "seniority_level": None,
        "location_text": location_text,
        "is_remote": is_remote,
        "description_text": desc_text,
        "salary": salary,
        "posted_at": posted_at,
        "job_function_raw": None,
        "industries_raw": company_info["industries"] or [],
        "benefits": benefits,
        "ats_vendor": ats_vendor,
        "workplace_type": workplace_type,
        "metadata": {
            "split_view": "true" if ld_items else "false",
            "match_id": single_job_data.get("matchId"),
            "listing_key": single_job_data.get("listingKey"),
        },
    }

    final_logo = company_info["logo_url"] or company_logo or company_logo_pane
    if final_logo and ("public-nosensitive-ziprecruiter-logos" in final_logo or "ziprecruiter.com/assets" in final_logo):
        final_logo = None

    organization = {
        "name": company_name,
        "slogan": None,
        "description": company_info["description"],
        "website": company_info["website"],
        "linkedin_url": company_info["linkedin_url"],
        "logo_url": final_logo,
        "employees_count": company_info["employees_count"],
        "industries": company_info["industries"],
        "addr_country": company_info["addr_country"],
        "addr_locality": company_info["addr_locality"],
        "addr_region": company_info["addr_region"],
        "addr_postal_code": loc.get("postalCode") if loc else None,
        "addr_street": None,
        "addr_type": "Headquarters",
        "organization_type": "Company",
        "indeed_url": None,
        "job_board_url": company_zr_url,
        "metadata": {},
    }

    poster = {
        "name": None,
        "title": None,
        "photo_url": None,
        "profile_url": None,
        "poster_type": None,
        "poster_role": [],
        "metadata": {},
    }

    result = {"job": job_obj, "organization": organization, "poster": poster}

    print("=== BEGIN JSON ===")
    print(json.dumps(result, indent=2))
    print("=== END JSON ===")

    try:
        if job_tid and not was_already_open:
            close_tab(job_tid)
        if original_tid:
            switch_tab(original_tid, activate=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    print("/quit")