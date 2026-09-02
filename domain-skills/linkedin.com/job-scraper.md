# LinkedIn Job Scraper Map

## Overview
LinkedIn job pages are complex Single Page Applications (SPA). They contain rich information about the job, company, and the hiring team. Selectors are often obfuscated or dynamic, so this map prioritizes text-based heuristics and relative positioning.

## URL Patterns
- Job View: `https://www.linkedin.com/jobs/view/<job_id>/`
- Company About: `https://www.linkedin.com/company/<company_id>/about/`
- Poster Profile: `https://www.linkedin.com/in/<profile_id>/`

## Extraction Strategy

### CRITICAL RULE: SCOPED EXTRACTION

To balance accuracy and robustness, use a **Multi-Scope** approach:
1.  **Precision Scope (Header)**: Used for **Location** and **Salary Badges**. Restrict search to the Top Card/Header area (e.g., `.job-details-jobs-unified-top-card`) to avoid picking up irrelevant text or recommendations.
2.  **Scoped Content Scope (Job Details Container)**: Used for **Job Description**, **Benefits**, and **Description-Embedded Salary**. Search strictly within `#job-details` / `.jobs-description`. NEVER search `document.body.innerText` globally, as that captures unrelated "More jobs" recommendations and ads.

- **Job Title**: 
  - **Primary**: The text that matches `document.title` (stripped of " | LinkedIn").
  - **Secondary**: Find the element with the largest `fontSize` in the top card area.
- **Job Description**: 
  - **Primary**: Look for the exact text "About the job" or "Job description". The content is usually in the immediate following sibling or parent container.
  - **Clean**: Use `cleanDescription` to convert the HTML to structured Markdown.
- **Field Discovery**:
  - **Location & Applicants**: 
    - **Primary**: Find the text line that matches the pattern `City, State · Time · Stats`.
    - **Heuristic**: Split by bullets (`·` or `•`). 
    - **Applicants**: Look for a segment containing "applicant" or "clicked apply". Extract the numeric portion (e.g., "15" from "15 people clicked apply").
  - **Salary**: 
    - **Primary**: Top card insight badge containing `$` and a period/time suffix (`yr`, `hr`, `year`, `hour`, `annually`).
    - **Fallback**: Search within `#job-details` text for explicit salary prefixes (`salary range: $X - $Y`) or clean dollar ranges (`$100,000 - $155,000`).
    - **Negative Rule**: Never scan global `document.body` text; return `null` if not found in the active posting.
  - **Workplace Type**: 
    - Search for "Remote", "Hybrid", or "On-site" in the top card insights.
  - **Employment Type**: 
    - Search for "Full-time", "Contract", "Part-time" in the top card insights or job details.
  - **Job Function & Seniority**:
    - **Primary**: Look for "Job function" or "Seniority level" labels in the job details section (e.g., `.description__job-criteria-item` or `.jobs-description-details__list-item`).
    - **Fallback**: Search for these labels in the entire page text and take the following text.
  - **Experience Range**:
    - **Heuristic**: Search the description text for patterns like `(\d+\+?\s*(?:-\s*\d+\+?)?\s*years?)` (e.g., "3+ years", "5-7 years").
  - **Benefits**:
    - **Primary**: Extract from a section labeled "Benefits", "Why Join Us", or "What we offer" in the description.
    - **Fallback**: Look for items with "benefit" in the text or specific benefit icons.

### Markdown Hygiene Rules

1.  **No Leading Garbage**: Strip any `****` or horizontal rule artifacts from the top of the description.
2.  **No Empty Headers**: Do not create headers like `** **`. If the text is empty, omit the header.
3.  **About the Job**: Always strip the "About the job" or "Job description" header from the content itself to avoid redundancy.
4.  **Normalize Spacing**: Ensure there are never more than two consecutive newlines.
5.  **Clean Lists**: Bullet points should be `- ` followed by a space.

### Organization Information
- **LinkedIn URL**: Extracted from the company name link.
- **Website**: 
  - Navigate to the Company's LinkedIn `/about/` page if available.
  - If Confidential, use `null`.
- **Description**: "Overview" section on the Company `/about/` page.
- **Logo**: `img` with `alt` containing the company name.

### Poster Information
- **Section**: "Meet the hiring team" or "People you can reach out to".
- **Name**: Text of the link containing `/in/`.
- **Profile URL**: The `/in/` link itself.
- **Title**: Text below the name.
- **Strategic Research**: Navigate to the poster's profile to extract full name, title, and photo if missing from the job page.

## Site Quirks
- **Obfuscated Classes**: Classes like `_2969a2c6` are common. Use text content and tag hierarchy.
- **Easy Apply vs. Apply**: "Easy Apply" stays on LinkedIn; "Apply" usually redirects externally.
- **Confidential Jobs**: Company name is "Confidential", logo might be generic.

## Deep Extraction Workflow
1. **Primary Page**: Extract all visible job details and poster/company links.
2. **Poster Profile**: If poster info is found, visit their profile in a background tab to get full details.
3. **Company About**: If company link is found (and not Confidential), visit `/about/` to get website and details.
4. **Apply Resolution**: If external apply, follow the link to identify the `ats_vendor`.
