# ZipRecruiter.com Domain Skill Map

## Overview
ZipRecruiter's job-search page (`/jobs-search?...`) is a **split-view layout**:
- **Left column**: Job cards (each is an `<article id="job-card-<token>">`)
- **Right pane** (`[data-testid="right-pane"]`): Full details of the **currently selected** job

The page URL does NOT change when a user clicks a different card - selection is internal state.
The right pane is the precision zone for title, company, location, salary, posted time, and the full description.
The page does NOT ship `application/ld+json` on the detail pane, but the search-results page DOES ship a
JSON-LD `ItemList` containing all 20 visible job canonical URLs (`/c/<Company>/Job/<Title>/-in-<City,ST>?jid=<jid>`).

We match the right-pane title against the JSON-LD `itemListElement[].name` to recover the canonical job URL and jid.

## URL Target Rule
- Input URL pattern: `https://www.ziprecruiter.com/jobs-search?...` (split view).
- Canonical job URL is recovered by matching the right-pane `<h2>` title against JSON-LD `itemListElement[].name` and returning that item's `url`.
- If the input URL already looks like a single-job page (`/jobs/<slug>` or `/c/<Company>/Job/...`), no split-view handling is needed - the right pane is the only relevant content.

Regex for canonical job URL: `^https://www\.ziprecruiter\.com/c/[^/]+/Job/[^/]+/-in-[^?]+\?jid=[a-zA-Z0-9]+`

## Selectors & Data Sources

### Primary Source: DOM (right-pane on detail page, OR single-job page)
- **Job Title**: `[data-testid="right-pane"] h2.font-bold.text-header-md` (first h2 in right-pane). On a single-job page, the first `h2` with class containing `text-header-md`.
- **Company Name (header)**: The first `<p>`/`<a>` after the company link, or `a[data-testid="job-card-company"]` on cards. In the right-pane, extract text immediately after the title from `a[href*="/co/"]`.
- **Company Profile URL**: First `a[href*="/co/"]` whose `href` does NOT contain `/Jobs` (the `/Jobs` variant lists jobs; `/co/<slug>` is canonical).
- **Location + Workplace Type**: Right-pane second line is `City, ST • On-site|Remote|Hybrid`. Extract via `right-pane` text and split on `•`.
- **Salary**: In right-pane, find a `<p>`/`<span>` whose `innerText` matches `\$[\d,.]+\s*(?:-\s*\$[\d,.]+)?\s*(?:\/?(?:hr|yr|hour|year|month|k|K)?)` (typically a sibling of the workplace-type line).
- **Employment Type**: Right-pane text line `Full-time` / `Part-time` / `Contract` / etc.
- **Benefits**: Right-pane text line beginning with `Medical, Dental, ...` listing comma-separated perks.
- **Posted At**: Right-pane text matching `Posted (\d+ )?(hour|day|week|month)s? ago` OR literal `Today`/`Yesterday`/`Just posted`.
- **Description Body**: `[data-testid="job-details-scroll-container"]` text content (contains everything: header + description).
- **Apply URL**: First `a[href*="/job-redirect"]` in the right-pane. Following the redirect reveals the ATS URL.

### Secondary Source: JSON-LD `ItemList` on the search page
- **Canonical Job URL**: Match `[data-testid="right-pane"] h2` text against `itemListElement[].name`, return `.url`.
- **External ID (jid)**: Parse `jid=` query parameter from the matched URL.
- If the input URL is already a single-job page, use the `?jid=` query parameter directly (single-job URLs also use `jid`).

### Apply URL Resolution
- The Apply button's `href` is `https://www.ziprecruiter.com/job-redirect?match_token=...` (base64-encoded target URL).
- The `match_token` is a base64-encoded URL string. Decoding reveals the ATS URL (Workday, Greenhouse, Lever, etc.).
- Following the redirect with the browser (via `goto_url(apply_url)`) settles on the final ATS page.
- Set `ats_vendor` from the final URL hostname:
  - `*.myworkdayjobs.com` -> "Workday"
  - `boards.greenhouse.io` -> "Greenhouse"
  - `jobs.lever.co` -> "Lever"
  - `*.icims.com` -> "iCIMS"
  - `jobs.smartrecruiters.com` -> "SmartRecruiters"
  - Otherwise: `null`.

### Company Profile Page (background)
- Navigate (background tab) to the first `a[href*="/co/"]` URL whose href does NOT contain `/Jobs`.
- Wait for load, then extract:
  - **Website**: First `a[href]` whose text matches a website label OR a `[data-testid="company-data"]` block.
  - **Description**: `[data-testid="company-data"]` paragraph.
  - **Industry**: text after the label `Industry`.
  - **Company size**: text after `Company size` (e.g., "10,000+ Employees").
  - **Headquarters**: text after `Headquarters location` (e.g., "Falls Church, VA, US").
  - **Logo**: First `<img>` inside `[data-testid="company-data"]`.

## Data Transformation
- **Posted At**: Convert `Posted X hours/days/weeks ago`, `Today`, `Yesterday`, `Just posted` to ISO 8601 via `datetime.utcnow() - timedelta(...)`.
- **Workplace Type**:
  - Text contains "Remote" (case-insensitive) and not "Hybrid" -> `remote`, `is_remote = True`.
  - Text contains "Hybrid" -> `hybrid`, `is_remote = False`.
  - Otherwise -> `onsite`, `is_remote = False`.
- **Location Text**: Strip trailing "• On-site" / "• Remote" / "• Hybrid" (the workplace type badge).
  - If a candidate location contains the company name (adversarial check), strip the leading company-name portion.
- **Salary**: Normalize "$X - $Y/hr" / "$XK - $YK/yr" / "Up to $X/hr" patterns. Strip whitespace.
- **Clean Description**: Use `cleanDescription(html)` to strip HTML tags, remove redundant headers ("Job description", "Posted today", etc.), strip leading `****`/`___`/horizontal rule garbage, normalize bullets to "- ".

## Site Quirks
- The right pane has duplicate `Posted today` and original `Posted X ago` strings - prefer the X-ago pattern.
- Apply URL is base64-encoded `match_token` query parameter. Decoding reveals the underlying ATS URL.
- The Job title appears twice in the right pane: once as the page header, once inside the description body (as a section heading) - remove the duplicate from the description body via line deduplication.
- Some jobs use a "1-Click Apply" button text instead of "Apply".

## Dynamic Selectors & Self-Healing Guardrails (CRITICAL FOR AI AGENTS)
When an AI agent is engaged to heal or rewrite this scraper:
1. **Preserve Dynamic Selectors:** Do NOT hardcode brittle selectors directly. Always load `get_domain_selectors("ziprecruiter.com")` from `selector_manager` so selectors remain remotely updateable via `selectors.json` without requiring binary re-releases.
2. **Preserve `sys.path` Anchoring:** Always maintain the path resolver at the very top of `job-scraper.py`:
   ```python
   _HARNESS_ROOT = str(Path(__file__).resolve().parent.parent.parent)
   if _HARNESS_ROOT not in sys.path:
       sys.path.insert(0, _HARNESS_ROOT)
   ```
3. **Preserve Split-View Detection:** The input URL may be a `/jobs-search?` page. Always try the `[data-testid="right-pane"]` selector first, falling back to `document.body` if absent.
4. **Preserve Apply Redirect Decode:** The `match_token` in `job-redirect` URLs is a base64-encoded destination. Always decode it to recover the true ATS URL.