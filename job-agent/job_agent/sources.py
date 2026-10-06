"""Where jobs come from.

Each fetcher reads a free, public job-board API (no login, no API key) and
turns its postings into `Job` objects. The parse_* functions are separate from
the network calls so they can be tested with saved sample responses.

Supported boards:
  - Greenhouse  https://boards-api.greenhouse.io/v1/boards/<company>/jobs
  - Lever       https://api.lever.co/v0/postings/<company>
  - Ashby       https://api.ashbyhq.com/posting-api/job-board/<company>
  - Remotive    https://remotive.com/api/remote-jobs (remote jobs, many companies)
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import requests

USER_AGENT = "job-agent/1.0 (personal job search script)"
TIMEOUT_SECONDS = 30


@dataclass
class Job:
    source: str  # "greenhouse", "lever", "ashby" or "remotive"
    source_id: str  # the board's own id for the posting
    company: str
    title: str
    location: str
    url: str  # page describing the job
    apply_url: str  # page with the application form (often the same)
    description: str  # plain text
    remote: bool = False
    posted_at: str = ""  # ISO 8601 when known
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"


# ---------------------------------------------------------------- helpers


def html_to_text(raw: str) -> str:
    """Turn a job description's HTML into readable plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse sends HTML that is itself escaped
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|ul|ol|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _iso_from_millis(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _looks_remote(*texts: str) -> bool:
    return any(t and "remote" in t.lower() for t in texts)


def fetch_json(url: str, params: dict | None = None):
    response = requests.get(
        url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------- Greenhouse


def parse_greenhouse(data: dict, board: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name", "") or ""
        url = item.get("absolute_url", "")
        jobs.append(
            Job(
                source="greenhouse",
                source_id=str(item["id"]),
                company=item.get("company_name") or board,
                title=item.get("title", "").strip(),
                location=location,
                url=url,
                apply_url=url,  # the form is on the same page
                description=html_to_text(item.get("content", "")),
                remote=_looks_remote(location),
                posted_at=item.get("first_published") or item.get("updated_at") or "",
                extra={"board": board},
            )
        )
    return jobs


def fetch_greenhouse(board: str) -> list[Job]:
    data = fetch_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", {"content": "true"}
    )
    return parse_greenhouse(data, board)


# ---------------------------------------------------------------- Lever


def parse_lever(data: list, company: str) -> list[Job]:
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        location = categories.get("location", "") or ", ".join(
            categories.get("allLocations") or []
        )
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description", ""))]
        for section in item.get("lists") or []:
            parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
        parts.append(item.get("additionalPlain") or html_to_text(item.get("additional", "")))
        workplace = item.get("workplaceType", "")
        jobs.append(
            Job(
                source="lever",
                source_id=item["id"],
                company=company,
                title=item.get("text", "").strip(),
                location=location,
                url=item.get("hostedUrl", ""),
                apply_url=item.get("applyUrl") or item.get("hostedUrl", ""),
                description="\n\n".join(p.strip() for p in parts if p and p.strip()),
                remote=workplace == "remote" or _looks_remote(location),
                posted_at=_iso_from_millis(item.get("createdAt")),
                extra={"team": categories.get("team", ""), "commitment": categories.get("commitment", "")},
            )
        )
    return jobs


def fetch_lever(company: str) -> list[Job]:
    data = fetch_json(f"https://api.lever.co/v0/postings/{company}", {"mode": "json"})
    return parse_lever(data, company)


# ---------------------------------------------------------------- Ashby


def parse_ashby(data: dict, org: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        locations = [item.get("location", "")] + [
            loc.get("location", "") for loc in item.get("secondaryLocations") or []
        ]
        location = ", ".join(loc for loc in locations if loc)
        jobs.append(
            Job(
                source="ashby",
                source_id=item["id"],
                company=org,
                title=item.get("title", "").strip(),
                location=location,
                url=item.get("jobUrl", ""),
                apply_url=item.get("applyUrl") or item.get("jobUrl", ""),
                description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml", "")),
                remote=bool(item.get("isRemote")) or _looks_remote(location, item.get("workplaceType", "")),
                posted_at=item.get("publishedAt", ""),
                extra={"employment_type": item.get("employmentType", "")},
            )
        )
    return jobs


def fetch_ashby(org: str) -> list[Job]:
    data = fetch_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}", {"includeCompensation": "true"}
    )
    return parse_ashby(data, org)


# ---------------------------------------------------------------- Remotive


def parse_remotive(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(
            Job(
                source="remotive",
                source_id=str(item["id"]),
                company=item.get("company_name", ""),
                title=item.get("title", "").strip(),
                location=item.get("candidate_required_location", "") or "Remote",
                url=item.get("url", ""),
                apply_url=item.get("url", ""),  # the page links on to the real form
                description=html_to_text(item.get("description", "")),
                remote=True,
                posted_at=item.get("publication_date", ""),
                extra={"salary": item.get("salary", ""), "job_type": item.get("job_type", "")},
            )
        )
    return jobs


def fetch_remotive(search: str) -> list[Job]:
    # Remotive asks API users to keep requests to a few per day.
    data = fetch_json("https://remotive.com/api/remote-jobs", {"search": search, "limit": 100})
    return parse_remotive(data)


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


# ---------------------------------------------------------------- board URLs

_BOARD_PATTERNS = [
    ("greenhouse", re.compile(r"^(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io$")),
    ("lever", re.compile(r"^jobs(?:\.eu)?\.lever\.co$")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$")),
]
_NOT_A_BOARD = {"embed", "api", "v1", "jobs", "search", ""}


def board_from_url(url: str) -> tuple[str, str] | None:
    """Return (board_type, company) for a Greenhouse, Lever or Ashby URL.

    >>> board_from_url("https://jobs.lever.co/acme/1234-abcd")
    ('lever', 'acme')
    """
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    for board_type, pattern in _BOARD_PATTERNS:
        if not pattern.match(host):
            continue
        if board_type == "greenhouse" and parsed.path.startswith("/embed/"):
            company = (parse_qs(parsed.query).get("for") or [""])[0]
        else:
            company = parsed.path.strip("/").split("/")[0]
        company = company.strip().lower()
        if company in _NOT_A_BOARD or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", company):
            return None
        return board_type, company
    return None


# ---------------------------------------------------------------- filtering


def _contains_any(text: str, words: list[str]) -> bool:
    text = text.lower()
    return any(w.lower() in text for w in words if w)


def filter_reason(job: Job, search) -> str | None:
    """Why this job should be skipped before spending any Claude tokens on it.

    Returns None when the job passes every filter in the `search` config.
    """
    if search.roles and not _contains_any(job.title, search.roles):
        return "title does not match any target role"
    if _contains_any(job.title, search.exclude_title_words):
        return "title contains an excluded word"
    if search.remote_only and not job.remote:
        return "not remote"
    if search.locations:
        wants_remote = any(loc.strip().lower() == "remote" for loc in search.locations)
        # Region limits on remote jobs ("Remote - Canada only") are left for
        # Claude to catch when it scores the job.
        if not (_contains_any(job.location, search.locations) or (job.remote and wants_remote)):
            return "location does not match"
    return None
