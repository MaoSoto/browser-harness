from helpers import *
import urllib.request
import urllib.parse
import time
import json
import sys
import os
import re
from datetime import datetime, timedelta

def translate_relative_date(relative_text):
    if not relative_text:
        return datetime.utcnow().isoformat() + "Z"
    
    now = datetime.utcnow()
    try:
        relative_text = relative_text.lower()
        match = re.search(r'(\d+)', relative_text)
        val = int(match.group(1)) if match else 1
        
        if "minute" in relative_text or "just now" in relative_text:
            res = now
        elif "hour" in relative_text:
            res = now - timedelta(hours=val)
        elif "day" in relative_text:
            res = now - timedelta(days=val)
        elif "week" in relative_text:
            res = now - timedelta(weeks=val)
        elif "month" in relative_text:
            res = now - timedelta(days=val * 30)
        elif "year" in relative_text:
            res = now - timedelta(days=val * 365)
        elif "yesterday" in relative_text:
            res = now - timedelta(days=1)
        else:
            return now.isoformat() + "Z"
        return res.strftime("%Y-%m-%dT%H:%M:%SZ")
    except:
        return now.isoformat() + "Z"

def identify_ats(url):
    if not url: return "Unknown"
    url = url.lower()
    ats_map = {
        'greenhouse.io': 'Greenhouse',
        'lever.co': 'Lever',
        'workday': 'Workday',
        'myworkdayjobs.com': 'Workday',
        'smartrecruiters.com': 'SmartRecruiters',
        'bamboohr.com': 'BambooHR',
        'ashbyhq.com': 'Ashby',
        'icims.com': 'iCIMS',
        'applytojob.com': 'JazzHR',
        'jobvite.com': 'Jobvite',
        'taleo.net': 'Taleo',
        'brassring.com': 'Kenexa',
        'successfactors.com': 'SuccessFactors',
        'recruitee.com': 'Recruitee',
        'workable.com': 'Workable',
        'breezy.hr': 'Breezy HR',
        'personio': 'Personio',
        'teamtailor.com': 'Teamtailor',
        'alignerr.com': 'Alignerr Custom'
    }
    for key, val in ats_map.items():
        if key in url: return val
    return "Unknown"

def extract_job_data(input_url):
    tid = new_tab(input_url, activate=True)
    wait_for_load()
    time.sleep(10)
    
    # Primary Extraction Logic
    logic = r"""
    (() => {
        try {
            const getElText = (selector) => document.querySelector(selector)?.innerText?.trim();
            
            const bodyText = document.body.innerText;
            const bodyLines = bodyText.split('\n').map(l => l.trim()).filter(l => l.length > 0);

            // Title
            let title = document.title.split('|')[0].split(' - ')[0].trim();
            if (title.toLowerCase().includes('job search') || title.toLowerCase().includes('linkedin')) {
                 title = getElText('h1') || getElText('.job-details-jobs-unified-top-card__job-title');
            }
            if (!title || title.length < 5) {
                const h1 = document.querySelector('h1');
                if (h1) title = h1.innerText.trim();
            }

            // Company
            const companyLinkEl = Array.from(document.querySelectorAll('a')).find(a => a.href.includes('/company/'));
            let companyName = companyLinkEl?.innerText?.trim() || getElText('.job-details-jobs-unified-top-card__company-name');
            if (!companyName || companyName.toLowerCase().includes('confidential')) {
                const possible = bodyLines.find(l => l.length > 2 && l.length < 100 && !l.includes('·') && l !== title);
                if (possible) companyName = possible;
            }
            if (!companyName) companyName = "Confidential";

            let companyLinkedinUrl = companyLinkEl?.href?.split('?')[0];
            if (companyLinkedinUrl) {
                companyLinkedinUrl = companyLinkedinUrl.replace(/\/life\/?$/, '').replace(/\/about\/?$/, '').replace(/\/jobs\/?$/, '');
            }

            // Location, Posted At, Applicants
            let locationLine = null;
            let postedAtRaw = null;
            let applicantsCount = null;
            const metaLine = bodyLines.find(l => l.includes('·') || l.includes('•'));
            if (metaLine) {
                const parts = metaLine.split(/[·•]/).map(p => p.trim());
                for (let part of parts) {
                    const isTime = part.match(/\d+ (day|week|month|hour|minute)s? ago/i) || part.toLowerCase().includes('yesterday') || part.toLowerCase().includes('just now');
                    const isApplicants = part.toLowerCase().includes('applicant') || part.toLowerCase().includes('clicked apply');
                    const isCompany = part.toLowerCase() === companyName.toLowerCase();
                    if (isTime) postedAtRaw = part;
                    else if (isApplicants) {
                        const match = part.match(/(\d+)/);
                        if (match) applicantsCount = parseInt(match[1]);
                    }
                    else if (!isApplicants && !isCompany && !locationLine && part.length > 2) locationLine = part;
                }
            }

            // Insights
            const insights = Array.from(document.querySelectorAll('div, span, li')).filter(el => {
                return (el.className && (el.className.includes('_834b0593') || el.className.includes('job-insight'))) && el.innerText.length > 0;
            }).map(el => el.innerText.trim());

            if (insights.length === 0) {
                const keywords = ["Full-time", "Contract", "Part-time", "Remote", "Hybrid", "On-site", "Entry level", "Associate", "Mid-Senior level", "Director"];
                for (let i=0; i < Math.min(bodyLines.length, 50); i++) {
                    for (let kw of keywords) {
                        if (bodyLines[i] === kw) insights.push(bodyLines[i]);
                    }
                }
            }

            let workplaceType = "unknown";
            if (insights.some(i => i.toLowerCase().includes('remote'))) workplaceType = "remote";
            else if (insights.some(i => i.toLowerCase().includes('hybrid'))) workplaceType = "hybrid";
            else if (insights.some(i => i.toLowerCase().includes('on-site') || i.toLowerCase().includes('onsite'))) workplaceType = "onsite";

            let employmentType = insights.find(i => ["Full-time", "Part-time", "Contract", "Temporary", "Internship"].includes(i)) || null;
            let seniorityLevel = insights.find(i => ["Entry level", "Associate", "Mid-Senior level", "Director", "Executive", "Internship"].includes(i)) || null;
            
            // Search for Job Criteria (Job Function, Seniority, etc.)
            const criteriaItems = Array.from(document.querySelectorAll('.description__job-criteria-item, .jobs-description-details__list-item'));
            let jobFunction = null;
            criteriaItems.forEach(item => {
                const text = item.innerText;
                if (text.includes('Seniority level')) seniorityLevel = text.replace('Seniority level', '').trim();
                if (text.includes('Job function')) jobFunction = text.replace('Job function', '').trim();
                if (text.includes('Employment type') && !employmentType) employmentType = text.replace('Employment type', '').trim();
            });

            // If not found in specific elements, search globally for "Job function"
            if (!jobFunction) {
                const jfLine = bodyLines.find(l => l.toLowerCase().includes('job function'));
                if (jfLine) jobFunction = jfLine.split(':').pop().trim();
            }

            // Description
            const cleanDescription = (html) => {
                if (!html) return '';
                let text = html;
                text = text.replace(/<li[^>]*>/g, '\n- ').replace(/<\/li>/g, '');
                text = text.replace(/<(h[1-6]|strong|b)[^>]*>(.*?)<\/(h[1-6]|strong|b)>/g, (match, tag, content) => {
                    const inner = content.replace(/<[^>]*>/g, '').trim();
                    return inner ? `\n\n**${inner}**\n` : '';
                });
                text = text.replace(/<p[^>]*>/g, '\n\n').replace(/<\/p>/g, '');
                text = text.replace(/<br[^>]*>/g, '\n');
                const temp = document.createElement('div');
                temp.innerHTML = text;
                let res = temp.innerText.replace(/^(\s*\**\s*)+/g, '').replace(/About the job/i, '').replace(/\n\s*\n\s*\n/g, '\n\n').trim();
                return res.length > 50 ? res : temp.innerText.trim();
            };

            let descEl = document.querySelector('#job-details') || document.querySelector('.jobs-description') || document.querySelector('.description__text') || document.querySelector('.jobs-box__html-content');
            if (!descEl) {
                 const h2 = Array.from(document.querySelectorAll('h2')).find(h => h.innerText.includes('About the job'));
                 if (h2) {
                     let next = h2.parentElement.nextElementSibling;
                     while (next && next.innerText.length < 50) { next = next.nextElementSibling; }
                     descEl = next || h2.parentElement.parentElement;
                 }
            }
            const description = descEl ? cleanDescription(descEl.innerHTML) : "NOT FOUND";

            // Scoped Salary Extraction (Never search document.body.innerText globally)
            let salary = null;
            // 1. Check Top Card Insight Badges (Must contain $ and a period suffix)
            const topCard = document.querySelector('.job-details-jobs-unified-top-card') || 
                            document.querySelector('.jobs-unified-top-card') || 
                            document.querySelector('[class*="top-card"]');
            if (topCard) {
                const badgeEls = Array.from(topCard.querySelectorAll('li, span, div, button')).filter(el => {
                    const text = el.innerText?.trim() || '';
                    return text.includes('$') && text.length < 60 && !text.includes('\n');
                });
                for (let el of badgeEls) {
                    const t = el.innerText.trim();
                    if (/\$\d+/.test(t) && /(?:yr|hr|year|hour|month|mo|week|annual)/i.test(t)) {
                        salary = t;
                        break;
                    }
                }
            }
            // 2. If not in top-card badges, search ONLY within scoped job description
            if (!salary && description && description !== "NOT FOUND") {
                // Pattern A: "salary range ... is $X - $Y" or "wage range: $X - $Y"
                const prefixMatch = description.match(/(?:salary|compensation|pay|wage)(?:[\w\s,]{0,40}?)(?:is|:|of)?\s*(\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?)/i);
                if (prefixMatch && prefixMatch[1] && prefixMatch[1].length > 2) {
                    salary = prefixMatch[1].trim();
                } else {
                    // Pattern B: Clean standalone dollar range ($100,000 - $155,000 or $90k - $100k)
                    const rangeMatch = description.match(/\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?/i);
                    if (rangeMatch) {
                        salary = rangeMatch[0].trim();
                    } else {
                        // Pattern C: Single rate with period suffix ($50/hr, $150,000 a year)
                        const singleMatch = description.match(/\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:per|\/|a|an)\s*(?:year|yr|hour|hr|month|mo|week|annually)\b/i);
                        if (singleMatch) {
                            salary = singleMatch[0].trim();
                        }
                    }
                }
            }

            // Experience Range heuristic from description
            let experienceRange = null;
            if (description) {
                const expMatch = description.match(/(\d+\+?\s*(?:-\s*\d+\+?)?\s*years?)/i);
                if (expMatch) experienceRange = expMatch[0];
            }

            // Benefits
            const benefits = {};
            if (description) {
                const benefitHeaders = ["Why Join Us", "Benefits", "What We Offer", "Perks", "Why Join"];
                const descLines = description.split('\n');
                let capturing = false;
                for (let line of descLines) {
                    const cleanLine = line.replace(/^\W+/, '').trim();
                    if (benefitHeaders.some(h => line.toLowerCase().includes(h.toLowerCase()))) { capturing = true; continue; }
                    if (capturing) {
                        if (line.startsWith('**') && !benefitHeaders.some(h => line.toLowerCase().includes(h.toLowerCase()))) {
                             if (line.length < 50) capturing = false;
                        } else if (line.startsWith('- ') || (line.length > 0 && line.length < 100)) {
                            const parts = cleanLine.split(':');
                            const key = parts[0].trim();
                            const val = parts.slice(1).join(':').trim() || true;
                            if (key && key.length > 2) benefits[key] = val;
                        }
                    }
                }
            }

            if (!jobFunction) {
                const titleLow = title.toLowerCase();
                if (titleLow.includes('engineer') || titleLow.includes('developer')) jobFunction = "Engineering";
                else if (titleLow.includes('sales') || titleLow.includes('growth') || titleLow.includes('account')) jobFunction = "Sales";
                else if (titleLow.includes('marketing')) jobFunction = "Marketing";
                else if (titleLow.includes('specialist')) jobFunction = "Other";
            }
            if (!seniorityLevel) {
                const titleLow = title.toLowerCase();
                if (titleLow.includes('manager')) seniorityLevel = "Manager";
                else if (titleLow.includes('senior') || titleLow.includes('sr.')) seniorityLevel = "Senior";
                else if (titleLow.includes('director')) seniorityLevel = "Director";
                else if (titleLow.includes('intern')) seniorityLevel = "Internship";
                else if (titleLow.includes('junior') || titleLow.includes('jr.')) seniorityLevel = "Entry level";
            }

            // Poster
            const posterSection = Array.from(document.querySelectorAll('h2, h3, h4, span, strong'))
                .find(h => h.innerText.includes('People you can reach out to') || h.innerText.includes('Meet the hiring team'))?.parentElement?.parentElement;
            const posterLink = posterSection?.querySelector('a[href*="/in/"]');
            const posterName = posterLink?.innerText?.trim()?.split('\n')[0];
            const posterUrl = posterLink?.href?.split('?')[0];

            // Apply URL
            const applyBtn = Array.from(document.querySelectorAll('a, button')).find(el => 
                el.innerText.trim() === 'Apply' || el.innerText.trim() === 'Easy Apply' || el.getAttribute('aria-label')?.includes('Apply')
            );
            let applyUrl = applyBtn?.href || applyBtn?.closest('a')?.href;
            const isEasyApply = applyBtn?.innerText?.trim() === 'Easy Apply';

            return {
                job: {
                    title,
                    description_text: description,
                    location_text: locationLine,
                    posted_at_raw: postedAtRaw,
                    salary,
                    workplace_type: workplaceType,
                    employment_type: employmentType,
                    seniority_level: seniorityLevel,
                    apply_url: applyUrl,
                    is_easy_apply: isEasyApply,
                    external_id: window.location.pathname.split('/').filter(Boolean).pop(),
                    benefits: benefits,
                    job_function: jobFunction,
                    applicants_count: applicantsCount,
                    experience_range: experienceRange
                },
                organization: {
                    name: companyName,
                    linkedin_url: companyLinkedinUrl,
                    logo_url: document.querySelector('img[alt*="logo"]')?.src
                },
                poster: {
                    name: posterName,
                    profile_url: posterUrl
                }
            };
        } catch (e) {
            return { error: e.toString() };
        }
    })()
    """
    
    data = js(logic)
    if not data or 'error' in data:
        return {
            "job": {"title": "Error", "description_text": str(data.get('error') if data else "None"), "apply_url": input_url, "input_url": input_url, "external_id": "error"},
            "organization": {"name": "Error", "website": None, "indeed_url": None, "linkedin_url": None},
            "poster": {}
        }

    # Resolve Apply URL Redirects
    raw_apply_url = data['job'].get('apply_url')
    final_apply_url = raw_apply_url
    if raw_apply_url and 'linkedin.com/safety/go' in raw_apply_url:
        parsed = urllib.parse.urlparse(raw_apply_url)
        params = urllib.parse.parse_qs(parsed.query)
        if 'url' in params:
            final_apply_url = params['url'][0]
    
    # Deep Research: Company Details
    if data.get('organization', {}).get('linkedin_url') and data['organization']['name'] != "Confidential":
        about_url = data['organization']['linkedin_url'].rstrip('/') + '/about/'
        try:
            ctid = new_tab(about_url, activate=False)
            switch_tab(ctid)
            time.sleep(5)
            company_details = js(r"""
            (() => {
                const website = Array.from(document.querySelectorAll('a')).find(a => a.href.includes('http') && !a.href.includes('linkedin.com'))?.href;
                let description = null;
                const overviewH2 = Array.from(document.querySelectorAll('h2')).find(h => h.innerText.includes('Overview'));
                if (overviewH2) description = overviewH2.nextElementSibling?.innerText?.trim();
                const industries = Array.from(document.querySelectorAll('dt')).find(dt => dt.innerText.includes('Industry'))?.nextElementSibling?.innerText?.split(',')?.map(s => s.trim()) || [];
                const employees = Array.from(document.querySelectorAll('dt')).find(dt => dt.innerText.includes('Company size'))?.nextElementSibling?.innerText?.match(/\d+([,\.]\d+)?/)?.[0]?.replace(/[,\.]/g, '');
                const headquarters = Array.from(document.querySelectorAll('dt')).find(dt => dt.innerText.includes('Headquarters'))?.nextElementSibling?.innerText?.trim();
                const slogan = document.querySelector('h4')?.innerText?.trim();
                return { website, description, industries, employees_count: employees ? parseInt(employees) : null, headquarters, slogan };
            })()
            """)
            if company_details: data['organization'].update(company_details)
            close_tab(ctid)
            switch_tab(tid)
        except: pass

    # Deep Research: Poster Details
    if data.get('poster', {}).get('profile_url'):
        try:
            ptid = new_tab(data['poster']['profile_url'], activate=False)
            switch_tab(ptid)
            time.sleep(5)
            poster_details = js(r"""
            (() => {
                const name = document.querySelector('h1')?.innerText?.trim();
                const title = document.querySelector('.text-body-medium.break-words')?.innerText?.trim() || document.querySelector('[data-test-id="headline"]')?.innerText?.trim();
                const photo = document.querySelector('.pv-top-card-profile-picture__image')?.src;
                return { name, title, photo_url: photo };
            })()
            """)
            if poster_details: data['poster'].update(poster_details)
            close_tab(ptid)
            switch_tab(tid)
        except: pass

    # Final assembly matching Schema
    res = {
        "job": {
            "source": "LinkedIn",
            "external_id": data['job'].get('external_id'),
            "url": input_url,
            "input_url": input_url,
            "apply_url": final_apply_url or input_url,
            "title": data['job'].get('title'),
            "employment_type": data['job'].get('employment_type'),
            "seniority_level": data['job'].get('seniority_level'),
            "location_text": data['job'].get('location_text'),
            "is_remote": data['job'].get('workplace_type') == "remote",
            "description_text": data['job'].get('description_text'),
            "salary": data['job'].get('salary'),
            "posted_at": translate_relative_date(data['job'].get('posted_at_raw')),
            "job_function_raw": data['job'].get('job_function'),
            "industries_raw": data['organization'].get('industries', []),
            "benefits": data['job'].get('benefits'),
            "ats_vendor": identify_ats(final_apply_url),
            "workplace_type": data['job'].get('workplace_type') or "unknown",
            "metadata": {
                "posted_at_raw": data['job'].get('posted_at_raw'),
                "is_easy_apply": data['job'].get('is_easy_apply'),
                "applicants_count": data['job'].get('applicants_count'),
                "experience_range": data['job'].get('experience_range')
            }
        },
        "organization": {
            "name": data['organization'].get('name') or "Confidential",
            "slogan": data['organization'].get('slogan'),
            "description": data['organization'].get('description'),
            "website": data['organization'].get('website'),
            "linkedin_url": data['organization'].get('linkedin_url'),
            "indeed_url": None,
            "logo_url": data['organization'].get('logo_url'),
            "employees_count": data['organization'].get('employees_count'),
            "industries": data['organization'].get('industries', []),
            "addr_country": None,
            "addr_locality": data['organization'].get('headquarters', '').split(',')[0].strip() if data['organization'].get('headquarters') else None,
            "addr_region": data['organization'].get('headquarters', '').split(',')[-1].strip() if data['organization'].get('headquarters') and ',' in data['organization'].get('headquarters') else None,
            "addr_postal_code": None,
            "addr_street": None,
            "addr_type": "Headquarters",
            "organization_type": "Company",
            "metadata": { "headquarters_raw": data['organization'].get('headquarters') }
        },
        "poster": {
            "name": data['poster'].get('name'),
            "title": data['poster'].get('title'),
            "photo_url": data['poster'].get('photo_url'),
            "profile_url": data['poster'].get('profile_url'),
            "poster_type": "Internal Recruiter" if data['poster'].get('name') else None,
            "poster_role": ["Recruiter"],
            "metadata": {}
        }
    }
    return res

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    input_url = sys.argv[1]
    
    # Record the originally active tab to restore focus later
    original_tid = None
    try:
        for t in list_tabs():
            if t.get('activated'):
                original_tid = t['targetId']
                break
    except:
        pass

    try:
        data = extract_job_data(input_url)
        print("=== BEGIN JSON ===")
        print(json.dumps(data, indent=2))
        print("=== END JSON ===")
    except Exception as e:
        print("=== BEGIN JSON ===")
        print(json.dumps({"error": str(e)}, indent=2))
        print("=== END JSON ===")
    finally:
        try:
            # Find and close any tab opened for this job URL
            for t in list_tabs():
                if input_url.split('?')[0] in t["url"]:
                    close_tab(t["targetId"])
            if original_tid:
                switch_tab(original_tid, activate=True)
        except:
            pass
    print("/quit")
