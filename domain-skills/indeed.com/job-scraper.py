from helpers import *
import urllib.request
import time
import sys
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

def cleanDescription(html_content):
    if not html_content: return ""
    # Remove script and style tags
    html_content = re.sub(r'<(script|style).*?>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    
    # Replace common block elements with newlines
    for tag in ['p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr']:
        html_content = re.sub(f'<{tag}.*?>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(f'</{tag}>', '\n', html_content, flags=re.IGNORECASE)
    
    # Remove remaining tags
    text = re.sub(r'<.*?>', '', html_content)
    
    # Unescape HTML entities
    import html
    text = html.unescape(text)
    
    # Clean up whitespace
    lines = [line.strip() for line in text.split('\n')]
    cleaned_lines = []
    for line in lines:
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        
        # Strip artifacts like multiple stars or dashes
        line = re.sub(r'^[* \-•\t]+', '', line).strip()
        line = re.sub(r'^\*{2,}', '', line).strip()
        
        # Remove redundant headers
        if line.lower() in ["about the job", "job description", "summary", "position summary"]:
            continue
            
        if line:
            cleaned_lines.append("- " + line)
            
    final_text = "\n".join(cleaned_lines)
    final_text = re.sub(r'\n{3,}', '\n\n', final_text)
    return final_text.strip()

def solve_posted_at(relative_text):
    if not relative_text: return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    # If it's already ISO-like
    if 'T' in relative_text and (relative_text.endswith('Z') or '+' in relative_text):
        return relative_text

    now = datetime.utcnow()
    # Handle Indeed specific relative strings
    # "Just posted", "Today", "1 day ago", "30+ days ago"
    text = relative_text.lower()
    if "just posted" in text or "today" in text:
        pass
    elif "yesterday" in text:
        now -= timedelta(days=1)
    else:
        match = re.search(r'(\d+)\+?', text)
        if match:
            n = int(match.group(1))
            if "hour" in text: now -= timedelta(hours=n)
            elif "day" in text: now -= timedelta(days=n)
            elif "week" in text: now -= timedelta(weeks=n)
            elif "month" in text: now -= timedelta(days=n*30)
            
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')

def get_external_id(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if 'jk' in qs:
        return qs['jk'][0]
    # Fallback to path
    match = re.search(r'jk=([a-zA-Z0-9]+)', url)
    if match: return match.group(1)
    return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 job-scraper.py <url>")
        sys.exit(1)

    input_url = sys.argv[1]
    
    # 1. Open job page
    # Record the originally active tab to restore focus later
    original_tid = None
    try:
        for t in list_tabs():
            if t.get('activated'):
                original_tid = t['targetId']
                break
    except:
        pass

    job_tid = new_tab(input_url, activate=False)
    goto_url(input_url) # Force navigation to the specific job
    wait_for_load()
    
    # 2. Extract LD-JSON
    ld_json_raw = js('document.querySelector("script[type=\'application/ld+json\']")?.innerText')
    ld_data = {}
    if ld_json_raw:
        try:
            ld_data = json.loads(ld_json_raw)
        except:
            pass
            
    # 3. Extract basic info from LD-JSON or DOM
    title = ld_data.get('title') or js('document.title')
    title = title.split(' - ')[0].strip() if ' - ' in title else title
    
    desc_html = ld_data.get('description') or js('document.querySelector("#jobDescriptionText")?.innerHTML')
    description_text = cleanDescription(desc_html)
    
    company_name = ld_data.get('hiringOrganization', {}).get('name') or js('document.querySelector("div[data-company-name=\'true\']")?.innerText')
    company_indeed_url = ld_data.get('hiringOrganization', {}).get('sameAs')
    
    # Salary extraction
    salary_text = js('document.querySelector("#salaryInfoAndJobType")?.innerText')
    if not salary_text and ld_data.get('baseSalary'):
        bs = ld_data['baseSalary']
        if isinstance(bs, dict) and 'value' in bs:
            val = bs['value']
            if isinstance(val, dict):
                salary_text = f"${val.get('minValue')} - ${val.get('maxValue')} per {bs.get('unitText', 'year')}"
    
    # Location and Remote status
    location_text = js('document.querySelector("div[data-testid=\'inlineHeader-companyLocation\']")?.innerText')
    if not location_text and ld_data.get('jobLocation'):
        addr = ld_data['jobLocation'].get('address', {})
        location_text = f"{addr.get('addressLocality', '')}, {addr.get('addressRegion', '')}"
        
    is_remote = False
    workplace_type = "onsite"
    if ld_data.get('jobLocationType') == "TELECOMMUTE":
        is_remote = True
        workplace_type = "remote"
    elif "remote" in (location_text or "").lower() or "remote" in (title or "").lower():
        is_remote = True
        workplace_type = "remote"
    elif "hybrid" in (description_text or "").lower():
        workplace_type = "hybrid"
 
    posted_at = solve_posted_at(ld_data.get('datePosted') or js('document.querySelector(".date")?.innerText'))
    
    # Apply URL
    apply_url = js('document.querySelector("#applyButtonLinkContainer a")?.href || document.querySelector(".jobsearch-IndeedApplyButton a")?.href')
    if not apply_url:
        # Sometimes it's in a script or we need to derive it
        apply_url = input_url # Default to job page if button not found
 
    external_id = get_external_id(input_url) or ld_data.get('identifier', {}).get('value')
 
    # 4. Deep research for Company
    company_website = None
    company_logo = ld_data.get('hiringOrganization', {}).get('logo')
    company_industries = []
    comp_description = None
    comp_slogan = None
    employees_count = None
    addr_locality = None
    addr_region = None
    
    company_linkedin_url = None
    
    if company_indeed_url:
        # Background research
        comp_tid = new_tab(company_indeed_url, activate=False)
        goto_url(company_indeed_url) # Force navigation to the company page
        wait_for_load()
        comp_data = js('JSON.parse(document.getElementById("comp-initialData")?.textContent || "{}")')
        about_company = comp_data.get('aboutSectionViewModel', {}).get('aboutCompany', {})
        
        company_website = about_company.get('websiteUrl', {}).get('url') or about_company.get('website')
        if not company_website:
            # Look for any external link that looks like a website
            company_website = js('Array.from(document.querySelectorAll("a")).find(el => el.href && !el.href.includes("indeed.com") && (el.innerText.toLowerCase().includes("website") || el.innerText.toLowerCase().includes("http")))?.href')
        
        comp_description = about_company.get('description')
        comp_slogan = about_company.get('slogan') # Slogan might not be in this section, but keeping fallback
        
        employees_text = about_company.get('employeeRange')
        if employees_text:
            # Try to parse integer from range like "ERv1_10000_PLUS"
            # Extract all numbers and pick the largest one (to avoid version numbers like v1)
            nums = [int(n) for n in re.findall(r'\d+', employees_text)]
            if nums:
                employees_count = max(nums)
                if employees_count < 10 and len(nums) > 1:
                    # If max is small (like a version), try to find a larger one
                    potential = [n for n in nums if n > 10]
                    if potential: employees_count = max(potential)
        
        # Try to find LinkedIn URL
        company_linkedin_url = js('Array.from(document.querySelectorAll("a")).find(el => el.href && el.href.includes("linkedin.com/company"))?.href')
 
        hq_address = about_company.get('headquartersLocation', {}).get('address')
        if hq_address:
            # Simple split for city, state
            parts = hq_address.split(',')
            if len(parts) >= 2:
                addr_locality = parts[0].strip()
                addr_region = parts[1].strip()
            else:
                addr_locality = hq_address
                addr_region = None
        else:
            addr_locality = None
            addr_region = None
 
        company_industries = about_company.get('sectorNames', [])
        
        if not company_logo:
            company_logo = js('document.querySelector("img[src*=\'logo\']")?.src')
            
        close_tab(comp_tid)
 
    # 5. Build Final JSON
    result = {
        "job": {
            "source": "Indeed",
            "external_id": external_id,
            "url": input_url,
            "input_url": input_url,
            "apply_url": apply_url,
            "title": title,
            "employment_type": ld_data.get('employmentType', [None])[0] or "Full-time",
            "seniority_level": None, # Indeed usually doesn't have this field explicitly
            "location_text": location_text,
            "is_remote": is_remote,
            "description_text": description_text,
            "salary": salary_text,
            "posted_at": posted_at,
            "job_function_raw": None,
            "industries_raw": ld_data.get('industry', []) if isinstance(ld_data.get('industry'), list) else [ld_data.get('industry')] if ld_data.get('industry') else [],
            "benefits": {},
            "ats_vendor": None, # Could be improved by checking apply_url
            "workplace_type": workplace_type,
            "metadata": {
                "ld_json": True if ld_data else False
            }
        },
        "organization": {
            "name": company_name,
            "slogan": comp_slogan,
            "description": comp_description,
            "website": company_website,
            "indeed_url": company_indeed_url,
            "linkedin_url": company_linkedin_url,
            "logo_url": company_logo,
            "employees_count": employees_count,
            "industries": company_industries,
            "addr_country": "US",
            "addr_locality": addr_locality,
            "addr_region": addr_region,
            "addr_postal_code": None,
            "addr_street": None,
            "addr_type": "Headquarters",
            "organization_type": "Company",
            "metadata": {}
        },
        "poster": None # Indeed rarely has poster info
    }
 
    # Final touch: ATS Identification
    if apply_url:
        if "workday" in apply_url: result["job"]["ats_vendor"] = "Workday"
        elif "greenhouse" in apply_url: result["job"]["ats_vendor"] = "Greenhouse"
        elif "lever" in apply_url: result["job"]["ats_vendor"] = "Lever"
        elif "icims" in apply_url: result["job"]["ats_vendor"] = "iCIMS"
        elif "smartrecruiters" in apply_url: result["job"]["ats_vendor"] = "SmartRecruiters"
 
    print("=== BEGIN JSON ===")
    print(json.dumps(result, indent=2))
    print("=== END JSON ===")
 
    # Cleanup: close our opened tab and restore original focus
    try:
        if job_tid:
            close_tab(job_tid)
        if original_tid:
            switch_tab(original_tid, activate=True)
    except:
        pass

if __name__ == "__main__":
    main()
    print("/quit")
