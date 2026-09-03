# ziprecruiter.ie — Domain Skill Map

## Site Overview
- **Domain**: `ziprecruiter.ie` (Ireland mirror of ZipRecruiter)
- **Layout**: Direct single-job page (NO split-list view). Each job lives at a unique URL.
- **Underlying ATS**: The apply CTA always deep-links to the actual ATS host (e.g. Oracle HCM Taleo, Workday, Greenhouse, etc.). Apply URL on `*.oraclecloud.com` => ATS = "Oracle HCM".

## Target Rule
- **URL pattern (regex)**:
  ```
  ^https?://(www\.)?ziprecruiter\.ie/jobs/(?P<external_id>\d+)-[a-z0-9\-]+-at-[a-z0-9\-]+/?$
  ```
- **Example**: `https://www.ziprecruiter.ie/jobs/577485059-oracle-technology-sales-executive-ireland-at-namos-solutions`
- **Canonical page** = the page URL itself (no fragment, no `?` selection param).
- **External ID source**: first numeric run in the URL path segment after `/jobs/` (e.g. `577485059`). The "Reference: 218_575530_368" text on the page is the ATS-side ref, not the ZipRecruiter job id.

## Page Layout (DOM Heuristics)
The page is a thin server-rendered HTML wrapper. The job card is rendered by:
  - `<h1>` — title
  - H1's immediate parent contains the bullet-style metadata line:
    `Posted <date> · <Title> · <Company> · <LOCATION>, <COUNTRY> · <EmploymentType>`
  - Body description begins with a `Location:` paragraph followed by `<strong>` section headers ("About This Role", "Responsibilities", "Requirements", etc.)

## Selectors / Extraction Strategy

### 1. JSON-LD (PRIMARY — full payload)
- Script tag: `script[type="application/ld+json"]` → `@type: JobPosting`.
- Fields available:
  - `title`, `datePosted`, `validThrough`, `employmentType`, `directApply`
  - `hiringOrganization.name` (no website, no logo, no sameAs)
  - `jobLocation.address.{addressLocality, addressRegion, addressCountry}` (⚠️ ZipRecruiter mislabels Ireland as a US state "Ohio"; ignore `addressRegion`)
  - `description` (HTML string; full body)
- This is the single best source — use it first.

### 2. DOM fallback / enrichment
| Field | Selector / Heuristic |
|---|---|
| Title | `h1` (also OG `meta[property="og:title"]` → strip "Job at X in Y") |
| Company | H1's parent's text, split by `·`, segment after title; or `meta[name="description"]` → "Apply for NAMOS SOLUTIONS ..." |
| Location (UI text) | Header bullet line, segment after company name; pattern `IRELAND, United Kingdom` |
| Workplace Type | If description starts with `Location: Remote` → `remote`; if contains "Hybrid" → `hybrid`; else `onsite` |
| Posted At | `Posted 14 August, 2026` text near H1 → translate to ISO 8601 |
| Employment Type | Trailing segment after location ("Full Time") |
| Apply URL | First `<a>` whose visible text matches `/apply/i` and has an `href` starting with `http` |
| ATS Vendor | Host of `apply_url`: `oraclecloud.com` → "Oracle HCM", `myworkdayjobs.com` → "Workday", `greenhouse.io` → "Greenhouse", `lever.co` → "Lever", `icims.com` → "iCIMS" |
| Salary | Not present on the page; leave `null` |

### 3. Title cleanup
- Raw `title` from DOM is `"Oracle Technology Sales Executive - Ireland"`.
- OG title has noisy suffix `"... at <Company> in <Location>"` — strip everything from the first occurrence of ` at ` (but ONLY for OG meta fallback; the page H1 is already clean).

### 4. Location text handling
- UI text shows `IRELAND, United Kingdom`.
- Job is actually Remote (per description `Location: Remote, with regular travel required to Oracle's Dublin office.`).
- **Decision**: prefer UI location string but flag workplace_type=remote because the description body explicitly says "Remote".
- **NEGATIVE FILTER**: never let `organization.name` appear inside `location_text`. The ZipRecruiter location is the segment AFTER the company in the bullet line.

### 5. Description cleaning (cleanDescription)
- Source: `ld_json.description` (HTML) OR `body.innerText` fallback.
- Steps:
  1. Replace `</p>`, `</div>`, `<br>`, `</li>`, `</h*>` with `\n`.
  2. Strip remaining tags.
  3. Drop empty `<strong></strong>` artifacts.
  4. Drop redundant headers: `About This Role`, `Job Description`, `Summary`, `Position Summary` (the LD-JSON description already starts with a `Location:` paragraph that we keep).
  5. Strip leading `****`, `---`, `•`, `-`.
  6. Collapse 3+ newlines → 2.
  7. Prefix non-empty lines with `- ` (bullet style).

## Apply URL Heuristic (Critical)
- Search order:
  1. `<a>` whose text contains "Apply" / "Apply Now" → `href`
  2. Fallback: `ld_json.url` (often the same page)
  3. Final fallback: `input_url`
- On this site the apply URL contains a `?utm_source=ziprecruiter` query param → KEEP it (it's the source attribution).

## Company Research (Background tab strategy)
- The page does NOT link to a company profile page on ZipRecruiter (unlike Indeed).
- A Google/Bing lookup is **out of scope** (no network calls allowed in script).
- Therefore: `organization.website`, `organization.linkedin_url`, `organization.logo_url`, `organization.employees_count`, `organization.description`, `organization.industries`, `addr_*` fields → all `null`.
- `organization.name` comes from `hiringOrganization.name` in JSON-LD.
- `organization.indeed_url` → `null` (no Indeed cross-link from this page).

## Poster
- No poster information exposed on the page. Set entire `poster` object fields to `null` / `[]`.

## Output Marker
- Must wrap final JSON in:
  ```
  ###JSON_START###
  { ... }
  ###JSON_END###
  ```
- Plus the harness markers `=== BEGIN JSON ===` / `=== END JSON ===` are added around the JSON for the human-readable stdout channel.

## Validation Checks (Adversarial)
1. **Location Purity** — `location_text` MUST NOT include `organization.name`.
2. **Title vs H1** — `title` must equal the trimmed `h1.innerText`.
3. **Apply URL** — must be absolute and `http(s)://...`.
4. **ATS Vendor** — must match the host of `apply_url`.
5. **Description** — must not contain `****`, must not start with "About the job", must not exceed reasonable length (≤ ~30KB).

## Quirks / Gotchas
- **Mislabeled country**: ZipRecruiter's JSON-LD sets `addressCountry: "United States"` and `addressRegion: "Ohio"` for an Ireland-based listing. Trust the UI bullet line over the LD addressCountry.
- **Employment type casing**: LD returns `"Full Time"` (with space); normalize to `"Full-time"` only when field is empty.
- **External ID**: Use the numeric prefix in the URL path, NOT the `Reference:` field on the page.
- **No salary text**: Many ZipRecruiter postings don't publish salary. Leave `null` rather than fabricating.
- **Apply button variant**: Some postings use a "Apply on ZipRecruiter" button that opens an iframe/redirect to the ATS; the static `<a>` href still points to the ATS host.