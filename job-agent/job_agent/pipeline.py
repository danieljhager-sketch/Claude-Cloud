"""The three stages: search for jobs, score them, apply to the best ones."""

from __future__ import annotations

import hashlib
import re

import anthropic
import requests

from .apply import ApplicationResult, RunContext, run_application
from .browser import BrowserSession
from .claude_client import ClaudeRefused
from .config import Config
from .discover import discover_boards
from .matching import score_job, write_cover_letter
from .sources import FETCHERS, fetch_remotive, filter_reason
from .store import Store

# What each way an application can end means for the job's saved status.
RESULT_TO_STATUS = {
    "submitted": "applied",
    "needs_human": "needs_human",
    "declined": "declined",
    "failed": "failed",
    "dry_run": "scored",  # nothing was sent, so it can still be applied to later
}


# ---------------------------------------------------------------- search


def _fetch_problem(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 404:
        return "no job board with that name (check the name in the company's careers page address)"
    if isinstance(exc, requests.ConnectionError):
        return "couldn't connect (check your internet connection)"
    if isinstance(exc, requests.Timeout):
        return "the job board took too long to answer"
    return str(exc)[:200]


def run_search(config: Config, store: Store, client=None, resume=None) -> None:
    s = config.search
    boards = {("greenhouse", c) for c in s.greenhouse}
    boards |= {("lever", c) for c in s.lever}
    boards |= {("ashby", c) for c in s.ashby}

    if s.discover_with_web_search and client is not None:
        print("🔎 Asking Claude to search the web for companies that are hiring…")
        try:
            found = discover_boards(client, config, resume)
        except (anthropic.APIError, ClaudeRefused) as exc:
            print(f"   Web search failed, continuing without it: {exc}")
            found = set()
        new = [b for b in found if b not in boards and store.add_board(*b, origin="web search")]
        print(f"   Found {len(found)} job boards ({len(new)} new).")
    boards |= set(store.boards())

    jobs = []
    for board_type, company in sorted(boards):
        try:
            board_jobs = FETCHERS[board_type](company)
        except Exception as exc:  # one broken board shouldn't stop the rest
            print(f"   ⚠️  {board_type}/{company}: {_fetch_problem(exc)}")
            continue
        print(f"   {board_type}/{company}: {len(board_jobs)} open jobs")
        jobs.extend(board_jobs)
    for query in s.remotive_search:
        try:
            remote_jobs = fetch_remotive(query)
        except Exception as exc:
            print(f"   ⚠️  remotive '{query}': {_fetch_problem(exc)}")
            continue
        print(f"   remotive '{query}': {len(remote_jobs)} remote jobs")
        jobs.extend(remote_jobs)

    added = kept = 0
    for job in jobs:
        reason = filter_reason(job, s)
        status = "filtered_out" if reason else "new"
        if store.add_job(job, status=status, notes=reason or ""):
            added += 1
            kept += status == "new"
        else:
            # Seen before. If you changed your filters since, re-check it.
            existing = store.get(job.id)
            if existing["status"] in ("new", "filtered_out") and existing["status"] != status:
                store.set_status(job.id, status, reason or "")
    print(f"✅ {len(jobs)} jobs checked, {added} new, {kept} new ones match your filters.")


# ---------------------------------------------------------------- score


def run_scoring(config: Config, store: Store, client: anthropic.Anthropic, resume: dict) -> None:
    jobs = store.jobs_with_status("new", limit=config.matching.max_jobs_to_score)
    if not jobs:
        print("Nothing new to score. Run `search` first.")
        return
    print(f"🧠 Claude is scoring {len(jobs)} job{'s' if len(jobs) != 1 else ''} against your resume…")
    for job in jobs:
        try:
            result = score_job(client, config, resume, job)
        except anthropic.AuthenticationError:
            raise
        except (anthropic.APIError, ClaudeRefused, ValueError) as exc:
            print(f"   ⚠️  {job['company']} – {job['title']}: {exc}")
            continue
        store.set_score(job["id"], result.score, result.model_dump())
        mark = "⭐" if result.score >= config.matching.min_score else "  "
        print(f"   {mark} {result.score:3d}  {job['company']} – {job['title']}: {result.summary}")


# ---------------------------------------------------------------- apply


def job_folder_name(job) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job['company']} {job['title']}".lower()).strip("-")[:60]
    return f"{slug}-{hashlib.sha1(job['id'].encode()).hexdigest()[:6]}"


def run_applications(
    config: Config,
    store: Store,
    client: anthropic.Anthropic,
    resume: dict,
    job_id: str | None = None,
    dry_run: bool = False,
) -> None:
    if job_id:
        job = store.get(job_id)
        if job is None:
            print(f"No job with id {job_id}. Use `list` to see ids.")
            return
        jobs = [job]
    else:
        jobs = store.best_unapplied(config.matching.min_score, config.apply.max_applications_per_run)
    if not jobs:
        print(f"No scored jobs at or above {config.matching.min_score}. Run `search` and `score` first.")
        return

    with BrowserSession(headless=config.apply.headless) as session:
        resume_pdf = config.resume_path
        if resume_pdf.suffix.lower() != ".pdf":
            # Most forms want a PDF, so make one from the text resume.
            resume_pdf = session.text_to_pdf(
                config.resume_path.read_text(), config.data_dir / "resume.pdf", config.applicant.full_name
            )
        for job in jobs:
            _apply_one(config, store, client, resume, session, job, resume_pdf, dry_run)


def _apply_one(config, store, client, resume, session, job, resume_pdf, dry_run) -> None:
    print(f"\n📝 {job['company']} – {job['title']}  (fit score {job['score']})")
    print(f"   {job['apply_url'] or job['url']}")
    folder = config.applications_dir / job_folder_name(job)
    folder.mkdir(parents=True, exist_ok=True)
    files = {"resume": resume_pdf}

    cover_letter = None
    if config.apply.write_cover_letter:
        letter_file = folder / "cover_letter.txt"
        if letter_file.exists():
            cover_letter = letter_file.read_text()  # reuse it, including any edits you made
        else:
            print("   ✍️  Writing a cover letter…")
            try:
                cover_letter = write_cover_letter(client, config, resume, job)
            except (anthropic.APIError, ClaudeRefused, ValueError) as exc:
                print(f"   ⚠️  No cover letter: {exc}")
            else:
                letter_file.write_text(cover_letter)
        if cover_letter:
            files["cover_letter"] = session.text_to_pdf(
                cover_letter, folder / "cover_letter.pdf", f"{config.applicant.full_name} cover letter"
            )

    store.set_status(job["id"], "applying")
    session.new_page()
    ctx = RunContext(
        config=config,
        folder=folder,
        files=files,
        answers_path=config.data_dir / "answers.json",
        dry_run=dry_run,
    )
    try:
        result = run_application(client, session, ctx, job, resume, cover_letter)
    except KeyboardInterrupt:
        store.set_status(job["id"], "scored", "interrupted")
        raise
    except anthropic.AuthenticationError:
        store.set_status(job["id"], "scored")
        raise
    except (anthropic.APIError, ClaudeRefused) as exc:
        result = ApplicationResult("failed", str(exc))

    store.set_status(job["id"], RESULT_TO_STATUS[result.status], result.notes)
    icons = {"submitted": "🎉", "dry_run": "🧪", "declined": "⏭️", "needs_human": "🙋", "failed": "❌"}
    print(f"   {icons[result.status]} {result.status}: {result.notes}")
    print(f"   Files and screenshots: {folder}")
