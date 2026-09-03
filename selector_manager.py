"""Dynamic selector manager with local caching and offline fallback.
Enables remote updates of CSS selectors and regex patterns without app binary reinstalls.
"""
import os
import json
import time
import urllib.request

DEFAULT_SELECTORS = {
    "linkedin.com": {
        "target_rule": {
            "type": "spa_param",
            "url_params": ["currentJobId"],
            "path_regex": r"/jobs/view/(\d+)",
            "canonical_template": "https://www.linkedin.com/jobs/view/{id}/"
        },
        "top_card_selectors": [
            ".job-details-jobs-unified-top-card",
            ".jobs-unified-top-card",
            "[class*=\"top-card\"]"
        ],
        "description_selectors": [
            "#job-details",
            ".jobs-description",
            ".description__text",
            ".jobs-box__html-content"
        ],
        "title_selectors": [
            ".job-details-jobs-unified-top-card__job-title",
            ".jobs-unified-top-card__job-title",
            "h1"
        ],
        "company_selectors": [
            ".job-details-jobs-unified-top-card__company-name",
            ".jobs-unified-top-card__company-name",
            "a[href*=\"/company/\"]"
        ],
        "salary_patterns": {
            "badge_time_regex": r"(?:yr|hr|year|hour|month|mo|week|annual)",
            "desc_prefix_regex": r"(?:salary|compensation|pay|wage)(?:[\w\s,]{0,40}?)(?:is|:|of)?\s*(\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?)",
            "desc_range_regex": r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?",
            "desc_single_regex": r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:per|\/|a|an)\s*(?:year|yr|hour|hr|month|mo|week|annually)\b"
        }
    },
    "indeed.com": {
        "target_rule": {
            "type": "spa_param",
            "url_params": ["jk", "vjk"],
            "canonical_template": "https://www.indeed.com/viewjob?jk={id}"
        },
        "salary_badge_selectors": [
            "#salaryInfoAndJobType",
            "div[data-testid='inlineHeader-salary']"
        ],
        "description_selectors": [
            "#jobDescriptionText"
        ],
        "company_selectors": [
            "div[data-company-name='true']",
            "div[data-testid='inlineHeader-companyName']"
        ],
        "location_selectors": [
            "div[data-testid='inlineHeader-companyLocation']"
        ],
        "salary_patterns": {
            "desc_prefix_regex": r"(?:salary|compensation|pay|wage)(?:[\w\s,]{0,40}?)(?:is|:|of)?\s*(\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?)",
            "desc_range_regex": r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:-|–|—|to)\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?(?:\s*(?:per|\/|a|an)?\s*(?:year|yr|hour|hr|month|mo|week|annually))?",
            "desc_single_regex": r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:k|K)?\s*(?:per|\/|a|an)\s*(?:year|yr|hour|hr|month|mo|week|annually)\b"
        }
    }
}

CACHE_URL = "https://careerglide.co/selectors.json"
CACHE_TTL = 3600  # 1 hour cache validity

def _get_cache_path():
    if os.name == 'nt':
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        d = os.path.join(base, "CareerGlide")
    else:
        d = os.path.expanduser("~/.careerglide")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "selectors.json")

def load_selectors(force_refresh=False):
    cache_path = _get_cache_path()

    # Check cache freshness
    if os.path.exists(cache_path) and not force_refresh:
        try:
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < CACHE_TTL:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if isinstance(cached_data, dict) and "domains" in cached_data:
                        return cached_data["domains"]
        except Exception:
            pass

    # Fetch latest configuration from server (fast 2s timeout)
    try:
        req = urllib.request.Request(
            CACHE_URL,
            headers={"User-Agent": "CareerGlide-Scraper/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                if isinstance(data, dict) and "domains" in data:
                    try:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(raw)
                    except Exception:
                        pass
                    return data["domains"]
    except Exception:
        pass

    # Fallback to previously cached copy if network failed
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "domains" in data:
                    return data["domains"]
        except Exception:
            pass

    # Return embedded defaults
    return DEFAULT_SELECTORS

def get_domain_selectors(domain):
    domains = load_selectors()
    return domains.get(domain, DEFAULT_SELECTORS.get(domain, {}))

def get_all_target_rules():
    """Extracts target_rule for each domain from remote/cached selectors."""
    domains = load_selectors()
    rules = {}
    for d, cfg in domains.items():
        if isinstance(cfg, dict) and "target_rule" in cfg:
            rules[d] = cfg["target_rule"]
    return rules
