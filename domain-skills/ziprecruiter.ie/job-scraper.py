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
from datetime import datetime, timedelta
from urllib.parse import urlparse


def cleanDescription(html_content):
    if not html_content:
        return ""
    html_content = re.sub(r"<(script|style).*?>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    for tag in ["p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]:
        html_content = re.sub(f"<{tag}.*?>", "\n", html_content, flags=re.IGNORECASE)
        html_content = re.sub(f"</{tag}>", "\n", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r"<\s*strong\s*>\s*</\s*strong\s*>", "", html_content, flags=re.IGNORECASE)
    text = re.sub(r"<.*?>", "", html_content)
    import html as _html
    text = _html.unescape(text)
    lines = [ln.strip() for ln in text.split("\n")]
    cleaned = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        line = re.sub(r"^[*\s\-•\t]+", "", line).strip()
        line = re.sub(r"^\*{2,}", "", line).strip()
        if line.lower() in ["about the job", "job description", "summary",
                            "position summary", "about this role", "about this position"]:
            if not cleaned:
                continue
        if line:
            cleaned.append("- " + line)
    final_text = "\n".join(cleaned)
    final_text = re.sub(r"\n{3,}", "\n\n", final_text)
    return final_text.strip()


def solve_posted_at(relative_text):
    if not relative_text:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    text = relative_text.strip()
    if "T" in text and (text.endswith("Z") or "+" in text):
        return text
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text + "T00:00:00Z"
    m = re.match(r"(\d{1,2})\s+(\w+),?\s+(\d{4})", text)
    if m:
        day, mon_name, year = m.group(1), m.group(2)[:3].lower(), m.group(3)
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        mon = months.get(mon_name)
        if mon:
            try:
                return datetime(int(year), mon, int(day)).strftime("%Y-%m-%dT00:00:00Z")
            except Exception:
                pass
    now = datetime.utcnow()
    low = text.lower()
    if "today" in low or "just posted" in low:
        pass
    elif "yesterday" in low:
        now -= timedelta(days=1)
    else:
        m2 = re.search(r"(\d+)\+?", low)
        if m2:
            n = int(m2.group(1))
            if "hour" in low:
                now -= timedelta(hours=n)
            elif "day" in low:
                now -= timedelta(days=n)
            elif "week" in low:
                now -= timedelta(weeks=n)
            elif "month" in low:
                now -= timedelta(days=n * 30)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_external_id(url):
    parsed = urlparse(url)
    m = re.search(r"/jobs/(\d+)", parsed.path)
    if m:
        return m.group(1)
    return None


def detect_ats(apply_url):
    if not apply_url:
        return None
    host = (urlparse(apply_url).hostname or "").lower()
    if "oraclecloud.com" in host or "taleo.net" in host:
        return "Oracle HCM"
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return "Workday"
    if "greenhouse.io" in host or "boards.greenhouse.io" in host:
        return "Greenhouse"
    if "lever.co" in host:
        return "Lever"
    if "icims.com" in host:
        return "iCIMS"
    if "smartrecruiters.com" in host:
        return "SmartRecruiters"
    if "jobvite.com" in host:
        return "Jobvite"
    if "bamboohr.com" in host:
        return "BambooHR"
    if "ashbyhq.com" in host:
        return "Ashby"
    return None


def detect_workplace_type(description_text, location_text, title):
    blob = f"{description_text or ''} {location_text or ''} {title or ''}".lower()
    if re.search(r"\bremote\b", blob) and not re.search(r"\bhybrid\b", blob):
        return "remote", True
    if re.search(r"\bhybrid\b", blob):
        return "hybrid", False
    if re.search(r"\bremote\b", blob):
        return "remote", True
    return "onsite", False


def extract_header_line():
    raw = js("""
(() => {
  const h1 = document.querySelector("h1");
  if (!h1) return null;
  let el = h1;
  for (let i = 0; i < 4; i++) {
    el = el.parentElement;
    if (!el) break;
    const txt = el.innerText;
    if (txt && txt.split("\\n").filter(Boolean).length >= 3) {
      const lines = txt.split("\\n").map(l => l.trim()).filter(Boolean);
      if (lines.some(l => /posted|reference/i.test(l))) {
        return JSON.stringify(lines);
      }
    }
  }
  return null;
})()
""")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def find_apply_url():
    href = js("""
(() => {
  const candidates = Array.from(document.querySelectorAll("a[href]"));
  const applyEl = candidates.find(e => /apply/i.test(e.innerText.trim()));
  return applyEl ? applyEl.href : null;
})()
""")
    return href


def find_salary(description_text):
    if not description_text:
        return None
    patterns = [
        r"(?:€|£|\$)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:-|–|—|to)\s*[€£\$]?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?",
        r"(?:€|£|\$)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:per|\/|a|an)\s*(?:year|yr|hour|hr|month|mo|week|annually)\b",
    ]
    for p in patterns:
        m = re.search(p, description_text, re.I)
        if m:
            return m.group(0).strip()
    return None


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

    job_tid = new_tab(input_url, activate=False)
    goto_url(input_url)
    wait_for_load()
    time.sleep(1.5)

    ld_json_raw = js(
        "document.querySelector(\"script[type='application/ld+json']\")?.innerText"
    )
    ld_data = {}
    if ld_json_raw:
        try:
            ld_data = json.loads(ld_json_raw)
        except Exception:
            ld_data = {}

    title = ld_data.get("title") or js("document.querySelector(\"h1\")?.innerText") or ""
    title = title.strip()

    desc_html = ld_data.get("description") or js("document.querySelector(\"h1\")?.parentElement?.parentElement?.parentElement?.innerHTML") or ""
    description_text = cleanDescription(desc_html)

    company_name = (ld_data.get("hiringOrganization") or {}).get("name")
    if not company_name:
        meta_desc = js("document.querySelector(\"meta[name='description']\")?.content") or ""
        m = re.match(r"Apply for\s+(.+?)\s+", meta_desc)
        if m:
            company_name = m.group(1).strip().title()
    company_name = (company_name or "").strip()

    header_lines = extract_header_line()
    location_text = None
    posted_text = None
    employment_type_raw = None
    if header_lines:
        for line in header_lines:
            if re.match(r"^posted\b", line, re.I):
                posted_text = line
            elif "Reference:" in line:
                posted_text = posted_text or line
        for line in header_lines:
            if "Posted" in line or "Reference:" in line:
                continue
            if company_name and company_name.lower() in line.lower():
                continue
            if line == title:
                continue
            et_match = re.match(r"^(.*?)\s+(Full\s*Time|Part\s*Time|Contract|Temporary|Permanent)\s*$", line, re.I)
            if et_match:
                location_text = et_match.group(1).strip().rstrip(",")
                employment_type_raw = et_match.group(2).strip()
                if re.match(r"^full\s*time$", employment_type_raw, re.I):
                    employment_type_raw = "Full-time"
                elif re.match(r"^part\s*time$", employment_type_raw, re.I):
                    employment_type_raw = "Part-time"
                break

    if not location_text and ld_data.get("jobLocation"):
        addr = ld_data["jobLocation"].get("address", {}) or {}
        locality = addr.get("addressLocality") or ""
        region = addr.get("addressRegion") or ""
        country = addr.get("addressCountry") or ""
        if locality and locality.lower() != "ireland":
            location_text = ", ".join([x for x in [locality, region, country] if x])
        else:
            location_text = ", ".join([x for x in [locality, country] if x and x.lower() != "united states"])

    if company_name and location_text and company_name.lower() in (location_text or "").lower():
        location_text = None

    workplace_type, is_remote = detect_workplace_type(description_text, location_text, title)

    posted_at = solve_posted_at(ld_data.get("datePosted") or posted_text)

    employment_type = ld_data.get("employmentType") or employment_type_raw
    if isinstance(employment_type, list):
        employment_type = employment_type[0] if employment_type else None
    if employment_type and "full" in employment_type.lower():
        employment_type = "Full-time"

    apply_url = find_apply_url() or input_url
    ats_vendor = detect_ats(apply_url)

    external_id = get_external_id(input_url) or ld_data.get("identifier", {}).get("value")

    salary = find_salary(description_text)

    result = {
        "job": {
            "source": "ZipRecruiter Ireland",
            "external_id": external_id,
            "url": input_url,
            "input_url": input_url,
            "apply_url": apply_url,
            "title": title or None,
            "employment_type": employment_type,
            "seniority_level": None,
            "location_text": location_text,
            "is_remote": is_remote,
            "description_text": description_text,
            "salary": salary,
            "posted_at": posted_at,
            "job_function_raw": None,
            "industries_raw": [],
            "benefits": {},
            "ats_vendor": ats_vendor,
            "workplace_type": workplace_type,
            "metadata": {
                "valid_through": ld_data.get("validThrough"),
                "direct_apply": ld_data.get("directApply"),
                "ld_json": bool(ld_data),
                "reference_id": next((l.split(":", 1)[1].strip() for l in header_lines if l.lower().startswith("reference:")), None),
            },
        },
        "organization": {
            "name": company_name or None,
            "slogan": None,
            "description": None,
            "website": None,
            "linkedin_url": None,
            "logo_url": None,
            "employees_count": None,
            "industries": [],
            "addr_country": None,
            "addr_locality": None,
            "addr_region": None,
            "addr_postal_code": None,
            "addr_street": None,
            "addr_type": None,
            "organization_type": "Company",
            "indeed_url": None,
            "metadata": {},
        },
        "poster": {
            "name": None,
            "title": None,
            "photo_url": None,
            "profile_url": None,
            "poster_type": None,
            "poster_role": [],
            "metadata": {},
        },
    }

    print("=== BEGIN JSON ===")
    print("###JSON_START###")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("###JSON_END###")
    print("=== END JSON ===")

    try:
        if job_tid:
            close_tab(job_tid)
        if original_tid:
            switch_tab(original_tid, activate=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    print("/quit")