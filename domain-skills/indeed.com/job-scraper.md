# Indeed.com Domain Skill Map

## Overview
Indeed uses a mix of static HTML and client-side hydration. Most key job data is available in a `application/ld+json` script, which is the primary source of truth for the robot. Supplemental data is extracted from the DOM.

## Selectors & Data Sources

### Primary Source: `application/ld+json`
- **Job Title**: `.title`
- **Description**: `.description` (HTML)
- **Company Name**: `.hiringOrganization.name`
- **Company Indeed URL**: `.hiringOrganization.sameAs`
- **Location**: `.jobLocation.address.addressLocality`, `.jobLocation.address.addressRegion`
- **Salary**: `.baseSalary` (structured)
- **Posted At**: `.datePosted` (ISO 8601)
- **Employment Type**: `.employmentType`
- **Workplace Type**: Derived from `.jobLocationType` ("TELECOMMUTE" -> "remote").

### Secondary Source: DOM
- **Salary (fallback)**: `div#salaryInfoAndJobType`
- **Description (fallback)**: `div#jobDescriptionText`
- **Company Website**: Found on the Company Page (see Navigation Strategy).
- **Apply URL**: Extracted from the "Apply Now" button or derived from the page state.

## Navigation Strategy: Company Details
If company details (website, logo, etc.) are missing or need enrichment:
1. Extract the `hiringOrganization.sameAs` URL from the LD-JSON.
2. Open this URL in a background tab (`new_tab(url, activate=False)`).
3. **CRITICAL**: Use `goto_url(url)` immediately after `new_tab` to ensure navigation, as `new_tab` may reuse an existing tab without navigating.
4. Wait for load and extract `window._initialData` or relevant DOM elements.
4. Close the background tab.

## Data Transformation
- **Posted At**: Indeed often provides ISO 8601 in LD-JSON. If only relative text is available (e.g., "3 days ago"), use `datetime.utcnow() - timedelta(days=N)`.
- **Workplace Type**:
  - `TELECOMMUTE` -> `remote`
  - Text "Remote" in location/description -> `remote`
  - Text "Hybrid" in description -> `hybrid`
  - Otherwise default to `onsite` or `unknown`.
- **Clean Description**:
  - Remove "About the job" headers.
  - Strip redundant company info at the bottom if it's generic.
  - Convert to clean Markdown.

## Apply URL Resolution
- If the "Apply on company site" button exists, it usually redirects to an ATS (Workday, Greenhouse, etc.).
- The robot should attempt to find the final destination URL if possible, or at least the Indeed-proxied redirect URL.

## Site Quirks
- Indeed sometimes uses "Simple VJ" (View Job) which has a different layout than the standard one.
- The `application/ld+json` is generally consistent across layouts.
