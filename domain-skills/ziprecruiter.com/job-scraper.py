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
import base64
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from selector_manager import get_domain_selectors


def cleanDescription(text_or_html):
    if not text_or_html:
        return ""

    s = text_or_html

    # Strip HTML tags if present
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

    # Strip leading garbage characters
    s = re.sub(r"^[\s*\-_=•·]+", "", s)

    # Drop empty bold/strong markers
    s = re.sub(r"\*\*\s*\*\*", "", s)
    s = re.sub(r"^\s*\*\s*$", "", s, flags=re.MULTILINE)

    # Drop redundant header lines
    redundant = {
        "job description", "about the job", "summary", "position summary",
        "posted today", "posted", "apply", "1-click apply",
    }
    # Split into lines for processing
    raw_lines = s.split("\n")
    cleaned_lines = []
    seen_starts = set()
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        low = line.lower().strip("* :.-")
        if low in redundant:
            continue
        # Skip pure ****/_____/---- lines
        if re.fullmatch(r"[\*\s_\-=•·]{3,}", line):
            continue
        # Skip rating/breakroom marketing blocks
        if "breakroom" in low and ("quiz" in low or "powered by" in low):
            continue
        if line.startswith("- "):
            content = line[2:]
        else:
            content = line
        cleaned_lines.append("- " + content)

    # Collapse 3+ blank lines to a single blank line
    out = "\n".join(cleaned_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def solve_posted_at(relative_text):
    if not relative_text:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if "T" in relative_text and (relative_text.endswith("Z") or "+" in relative_text):
        return relative_text
    now = datetime.utcnow()
    text = relative_text.lower().strip()
    if "just posted" in text or "today" in text or "1-click" in text:
        pass
    elif "yesterday" in text:
        now -= timedelta(days=1)
    else:
        m = re.search(r"(\d+)\+?\s*(hour|day|week|month|year|min|hr)", text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit.startswith("hour") or unit == "hr":
                now -= timedelta(hours=n)
            elif unit.startswith("day"):
                now -= timedelta(days=n)
            elif unit.startswith("week"):
                now -= timedelta(weeks=n)
            elif unit.startswith("month"):
                now -= timedelta(days=n * 30)
            elif unit.startswith("year"):
                now -= timedelta(days=n * 365)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    m = re.search(r"/job/[^/]+/[^/]+/[^/]+/[^/]+/([a-zA-Z0-9]+)", url)
    if m:
        return m.group(1)
    return None


def decode_match_token(token):
    """Decode ZipRecruiter's base64-encoded match_token to recover the ATS URL."""
    if not token:
        return None
    try:
        # Add padding
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        return decoded
    except Exception:
        return None


def identify_ats(url):
    if not url:
        return None
    host = url.lower()
    if "myworkdayjobs.com" in host or "/workday" in host:
        return "Workday"
    if "greenhouse.io" in host:
        return "Greenhouse"
    if "lever.co" in host:
        return "Lever"
    if "icims.com" in host:
        return "iCIMS"
    if "smartrecruiters.com" in host:
        return "SmartRecruiters"
    if "taleo.net" in host:
        return "Taleo"
    if "jobvite.com" in host:
        return "Jobvite"
    if "bamboohr.com" in host:
        return "BambooHR"
    return None


def extract_header_text(right_text):
    """Split the right-pane header block into structured fields."""
    if not right_text:
        return {}
    # Lines after the title
    lines = [ln.strip() for ln in right_text.split("\n") if ln.strip()]
    out["raw_lines"] = lines
    return out


def derive_workplace_type(text):
    if not text:
        return ("unknown", False)
    low = text.lower()
    if "hybrid" in low:
        return ("hybrid", False)
    if "remote" in low:
        return ("remote", True)
    if "on-site" in low or "onsite" in low or "in person" in low:
        return ("onsite", False)
    return ("unknown", False)


def first_apply_redirect_href():
    return js(
        "Array.from(document.querySelectorAll('[data-testid=\"right-pane\"] a[href*=\"job-redirect\"], a[href*=\"job-redirect\"]'))"
        ".map(a => a.href).find(h => h) || null"
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 job-scraper.py <url>")
        sys.exit(1)
    input_url = sys.argv[1]

    original_tid = None
    was_already_open = False
    try:
        tabs = list_tabs()
        for t in tabs:
            if t.get("activated"):
                original_tid = t["targetId"]
            if input_url.split('?')[0] in t.get("url", ""):
                was_already_open = True
    except Exception:
        pass

    job_tid = new_tab(input_url, activate=False)
    if not was_already_open:
        goto_url(input_url)
        wait_for_load()
        time.sleep(2.5)
    else:
        # Give right-pane a brief moment if user just clicked a card
        time.sleep(0.5)

    domain_cfg = get_domain_selectors("ziprecruiter.com") if "get_domain_selectors" in globals() else {}
    right_pane_sel = domain_cfg.get("right_pane_selector", '[data-testid="right-pane"]')
    title_sel = domain_cfg.get("title_selector", 'h2.font-bold.text-header-md')
    desc_sel = domain_cfg.get("description_selector", '[data-testid="job-details-scroll-container"]')

    # Right pane text (precision zone)
    right_text = js(f"document.querySelector({json.dumps(right_pane_sel)})?.innerText || \"\"")
    full_text = js(f"document.querySelector({json.dumps(desc_sel)})?.innerText || \"\"")

    # JSON-LD ItemList from the search page (only present on /jobs-search pages)
    ld_raw = js("document.querySelector('script[type=\"application/ld+json\"]')?.innerText")
    ld_items = []
    if ld_raw:
        try:
            parsed = json.loads(ld_raw)
            if isinstance(parsed, dict):
                ld_items = parsed.get("itemListElement", []) or []
            elif isinstance(parsed, list):
                for el in parsed:
                    if isinstance(el, dict) and el.get("@type") == "ItemList":
                        ld_items = el.get("itemListElement", []) or []
                        break
        except Exception:
            ld_items = []

    # Title
    title = js(f"document.querySelector({json.dumps(right_pane_sel)}) {json.dumps(title_sel)}?.innerText") or js(
        f"document.querySelector({json.dumps(title_sel)})?.innerText"
    )
    if not title:
        title = js("document.title")
        if "|" in title:
            title = title.split("|")[0].strip()
        if "-" in title and "job" in title.lower():
            title = title.split("-")[0].strip()

    # Company name and company profile URL
    company_name = None
    company_zr_url = None
    company_link = js(
        f"(()=>{{const rp=document.querySelector({json.dumps(right_pane_sel)})||document;"
        "const link=rp.querySelector('a[href*=\"/co/\"]');"
        "return link?link.href:null;}})()"
    )
    if company_link:
        parsed = urlparse(company_link)
        # /co/<Slug>... (may have /Jobs suffix or query)
        m = re.match(r"^/co/([^/?#]+)", parsed.path)
        if m:
            slug = m.group(1)
            company_zr_url = f"https://www.ziprecruiter.com/co/{slug}"
            # Find a more human-friendly label nearby
            company_name = js(
                f"(function(){{const a=document.querySelector('a[href*=\"{slug}\"]');"
                "return (a && a.innerText.trim()) || null;}})()"
            )
    # Fallback: pull the company name from header lines (2nd line)
    if not company_name:
        # From right-text: first line is title, second is company
        lines = [ln for ln in (right_text or "").split("\n") if ln.strip()]
        if len(lines) >= 2:
            candidate = lines[1].strip()
            # Avoid capture of "Job description" header
            if candidate.lower() != "job description":
                company_name = candidate

    # Match right-pane title against JSON-LD to recover canonical URL
    canonical_url = None
    if title and ld_items:
        for item in ld_items:
            if isinstance(item, dict) and item.get("name") == title:
                canonical_url = item.get("url")
                break

    if not canonical_url:
        # Maybe we are already on a single-job page or jid is in the URL
        parsed = urlparse(input_url)
        qs = parse_qs(parsed.query)
        if "jid" in qs:
            # Build canonical-style URL from any /c/<Company>/Job/<Title>/ path
            m = re.match(r"^/c/([^/]+)/Job/", parsed.path)
            if m:
                company_slug = m.group(1)
                rest = parsed.path[len(f"/c/{company_slug}/Job"):]
                canonical_url = (
                    f"https://www.ziprecruiter.com/c/{company_slug}{rest}"
                    + (f"?jid={qs['jid'][0]}" if qs.get("jid") else "")
                )
            else:
                # Fall back to the input URL if it has jid
                canonical_url = input_url
        elif "/jobs/" in parsed.path:
            # Single-job page already
            canonical_url = input_url

    if not canonical_url:
        # Last-resort: first item in the LD list (if any)
        if ld_items:
            canonical_url = ld_items[0].get("url")
        else:
            canonical_url = input_url

    external_id = get_external_id_from_url(canonical_url) or get_external_id_from_url(input_url)

    # Location + Workplace type
    location_text = None
    workplace_type = "onsite"
    is_remote = False
    # Find a line containing "•" (bullet separator) - common pattern: "City, ST • On-site"
    lines = [ln.strip() for ln in (right_text or "").split("\n") if ln.strip()]
    for ln in lines:
        if "•" in ln:
            parts = [p.strip() for p in ln.split("•")]
            # Filter out company name part
            loc_candidate = None
            for p in parts:
                if p and p.lower() not in {"apply", "1-click apply", "job description"}:
                    if company_name and company_name.lower() in p.lower():
                        continue
                    loc_candidate = p
                    break
            if loc_candidate:
                # Strip workplace badge
                wp_candidate = None
                wp, rem_flag = derive_workplace_type(loc_candidate)
                if wp != "unknown":
                    # Pull the workplace suffix off
                    wp_tokens = ["• on-site", "• onsite", "• remote", "• hybrid", "on-site", "remote", "hybrid"]
                    cleaned = loc_candidate
                    for tok in wp_tokens:
                        if tok in cleaned.lower():
                            idx = cleaned.lower().find(tok)
                            cleaned = cleaned[:idx].rstrip(" •,")
                            wp_candidate = wp
                            break
                    location_text = cleaned.strip()
                    if wp_candidate:
                        workplace_type = wp_candidate
                        is_remote = wp_candidate == "remote"
                else:
                    location_text = loc_candidate
                break

    # Adversarial: location must NOT contain company name
    if location_text and company_name and company_name.lower() in location_text.lower():
        # Strip the leading portion up to a comma
        if "," in location_text:
            tail = location_text.split(",", 1)[1].strip()
            if tail:
                location_text = tail
            else:
                location_text = None

    # Salary
    salary = None
    # Try right-pane line with $ pattern
    for ln in lines:
        m = re.search(r"\$\s?[\d,.]+\s*(?:[Kk])?\s*(?:[-–—]\s*\$?\s?[\d,.]+\s*(?:[Kk])?)?\s*(?:\/?\s*(?:hr|hour|year|yr|month|mo|annum|annually|k))?", ln)
        if m:
            salary = m.group(0).strip()
            break
    if not salary and full_text:
        m = re.search(
            r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:[Kk])?\s*(?:[-–—]\s*\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:[Kk])?\s*)?(?:\s*\/?\s*(?:hr|hour|year|yr|month|annum|k))?",
            full_text,
        )
        if m:
            salary = m.group(0).strip()

    # Employment type
    employment_type = None
    emp_match = re.search(
        r"\b(Full[-\s]?time|Part[-\s]?time|Contract|Temporary|Internship|Seasonal|Freelance|Commission)\b",
        " ".join(lines),
        re.IGNORECASE,
    )
    if emp_match:
        employment_type = emp_match.group(0).replace("  ", " ").strip()

    # Benefits (comma-separated perks line)
    benefits = {}
    for ln in lines:
        if re.match(r"^[A-Z][\w]+(?:,\s*[A-Z][\w]+){2,}\s*$", ln):
            perks = [p.strip() for p in ln.split(",")]
            # Filter generic non-benefit terms
            generic = {"apply", "1-click apply", "today", "posted"}
            perks = [p for p in perks if p.lower() not in generic and len(p) > 1]
            if perks:
                benefits["perks"] = perks
            break

    # Posted at
    posted_at_raw = None
    for ln in lines:
        low = ln.lower()
        if "posted" in low or "today" in low or "yesterday" in low or "just posted" in low:
            # Prefer "X hours/days/weeks ago"
            m = re.search(r"(\d+\+?\s*(?:hour|day|week|month|year)s?\s*ago)", low)
            if m:
                posted_at_raw = m.group(1)
                break
            if "today" in low and "posted" in low:
                posted_at_raw = "Today"
                break
    posted_at = solve_posted_at(posted_at_raw)

    # Description text
    desc_text = cleanDescription(full_text)
    # Drop the header/title-prefix portion if duplicated at top of description
    if title:
        desc_text_lines = desc_text.split("\n")
        while desc_text_lines and desc_text_lines[0].strip() in {f"- {title}", title, f"- {title.strip()}", title.strip()}:
            desc_text_lines.pop(0)
        desc_text = "\n".join(desc_text_lines).lstrip("\n")

    # Apply URL: follow the redirect via match_token decode first (fast path)
    apply_redirect = first_apply_redirect_href()
    apply_url = apply_redirect
    if apply_redirect:
        parsed = urlparse(apply_redirect)
        qs = parse_qs(parsed.query)
        token = qs.get("match_token", [None])[0]
        decoded = decode_match_token(token)
        if decoded and decoded.startswith("http"):
            apply_url = decoded

    # If still not decoded, follow redirect in a background tab to settle
    ats_vendor = identify_ats(apply_url)
    if apply_redirect and not ats_vendor:
        try:
            tmp_tid = new_tab(apply_redirect, activate=False)
            goto_url(apply_redirect)
            time.sleep(3)
            final = page_info().get("url")
            if final and "ziprecruiter.com" not in final.lower():
                apply_url = final
                ats_vendor = identify_ats(apply_url)
            close_tab(tmp_tid)
        except Exception:
            pass

    # ---------- Company Profile (background) ----------
    company_website = None
    company_logo = None
    company_description = None
    company_industries = []
    employees_count = None
    addr_locality = None
    addr_region = None
    addr_country = None
    company_linkedin_url = None

    if company_zr_url:
        try:
            comp_tid = new_tab(company_zr_url, activate=False)
            goto_url(company_zr_url)
            wait_for_load()
            time.sleep(2.5)

            cd_text = js('document.querySelector("[data-testid=\\"company-data\\"]")?.innerText || ""')
            # Description: first paragraph before "Industry"
            if cd_text:
                parts = cd_text.split("\n")
                desc_lines = []
                for ln in parts:
                    if ln.strip().lower().startswith("industry"):
                        break
                    desc_lines.append(ln.strip())
                company_description = " ".join(desc_lines).strip() or None

            # Industry
            ind_match = re.search(r"Industry\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
            if ind_match:
                company_industries = [ind_match.group(1).strip()]

            # Company size (e.g. "10,000+ Employees")
            size_match = re.search(r"Company size\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
            if size_match:
                size_text = size_match.group(1).strip()
                num = re.search(r"(\d[\d,]*)", size_text)
                if num:
                    try:
                        employees_count = int(num.group(1).replace(",", ""))
                    except Exception:
                        employees_count = None

            # Headquarters
            hq_match = re.search(r"Headquarters location\s*\n\s*([^\n]+)", cd_text, re.IGNORECASE)
            if hq_match:
                hq = hq_match.group(1).strip()
                # Pattern: "City, ST, US" or "City, ST"
                parts = [p.strip() for p in hq.split(",")]
                if len(parts) >= 3 and parts[-1].upper() in {"US", "USA", "UK", "CA", "AU"}:
                    addr_country = parts[-1]
                    addr_region = parts[-2]
                    addr_locality = parts[-3] if len(parts) >= 3 else parts[0]
                elif len(parts) >= 2:
                    addr_region = parts[-1]
                    addr_locality = parts[0]
                elif len(parts) == 1:
                    addr_locality = parts[0]

            # Website: prefer explicit "Website" label then "gdit.com" link
            company_website = js(
                "Array.from(document.querySelectorAll(\"a\")).map(a => a.href).find(h => "
                "h && !h.includes(\\\"ziprecruiter.com\\\") && !h.includes(\\\"facebook.com\\\") && "
                "!h.includes(\\\"twitter.com\\\") && !h.includes(\\\"instagram.com\\\") && "
                "!h.includes(\\\"linkedin.com\\\") && !h.includes(\\\"breakroom.cc\\\") && "
                "!h.includes(\\\"ketchcdn.com\\\") && !h.startsWith(\\\"mailto:\\\") && !h.startsWith(\\\"javascript:\\\"))"
            )

            # LinkedIn
            company_linkedin_url = js(
                "Array.from(document.querySelectorAll(\"a[href*=\\\"linkedin.com/company\\\"]\")).map(a => a.href)[0]"
            )

            # Logo
            company_logo = js(
                "document.querySelector(\"[data-testid=\\\"company-data\\\"] img\")?.src || "
                "document.querySelector(\"img[alt*=\\\"logo\\\"], img[class*=logo]\")?.src"
            )

            close_tab(comp_tid)
        except Exception:
            pass

    # Poster (rarely shown on ZipRecruiter)
    poster = {
        "name": None,
        "title": None,
        "photo_url": None,
        "profile_url": None,
        "poster_type": None,
        "poster_role": [],
        "metadata": {},
    }

    job_obj = {
        "source": "ZipRecruiter",
        "external_id": external_id,
        "url": canonical_url or input_url,
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
        "industries_raw": company_industries,
        "benefits": benefits,
        "ats_vendor": ats_vendor,
        "workplace_type": workplace_type,
        "metadata": {
            "split_view": "true" if ld_items else "false",
            "decoded_via_match_token": "true" if decoded and decoded.startswith("http") else "false",
        },
    }

    organization = {
        "name": company_name,
        "slogan": None,
        "description": company_description,
        "website": company_website,
        "linkedin_url": company_linkedin_url,
        "logo_url": company_logo,
        "employees_count": employees_count,
        "industries": company_industries,
        "addr_country": addr_country,
        "addr_locality": addr_locality,
        "addr_region": addr_region,
        "addr_postal_code": None,
        "addr_street": None,
        "addr_type": "Headquarters",
        "organization_type": "Company",
        "indeed_url": None,
        "metadata": {"ziprecruiter_url": company_zr_url},
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