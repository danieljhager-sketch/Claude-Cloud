"""Command line: python -m job_agent <command>"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import anthropic

from .claude_client import make_client, resume_block
from .config import ConfigError, load_config
from .pipeline import run_applications, run_scoring, run_search
from .store import STATUSES, Store

PROJECT_DIR = Path(__file__).resolve().parent.parent

HELP = """Find jobs that fit your resume and apply to them with Claude.

commands:
  init     create config.yaml to fill in
  search   find open jobs (and save them)
  score    have Claude rate how well each new job fits your resume
  list     show saved jobs, best matches first
  show     show one job's details and score
  apply    apply to the best-scoring jobs (asks before each submit)
  run      search, score and apply in one go
  mark     change a job's status by hand, e.g. to skip it
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m job_agent", description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default="config.yaml", help="path to your config file")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    commands.add_parser("init")
    commands.add_parser("search")
    commands.add_parser("score")
    list_cmd = commands.add_parser("list")
    list_cmd.add_argument("--status", choices=STATUSES)
    list_cmd.add_argument("--all", action="store_true", help="include jobs that failed the filters")
    show_cmd = commands.add_parser("show")
    show_cmd.add_argument("job_id")
    for name in ("apply", "run"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--dry-run", action="store_true", help="fill in forms but never submit")
        if name == "apply":
            cmd.add_argument("--job", dest="job_id", help="apply to this job id only")
    mark_cmd = commands.add_parser("mark")
    mark_cmd.add_argument("job_id")
    mark_cmd.add_argument("status", choices=STATUSES)

    args = parser.parse_args(argv)
    if args.command == "init":
        return init(Path(args.config))

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"❌ {exc}")
        return 1
    store = Store(config.data_dir / "jobs.db")

    try:
        if args.command in ("list", "show", "mark"):
            return {"list": list_jobs, "show": show_job, "mark": mark_job}[args.command](store, args)

        client = make_client()
        resume = resume_block(config.resume_path)
        if args.command in ("search", "run"):
            run_search(config, store, client, resume)
        if args.command in ("score", "run"):
            run_scoring(config, store, client, resume)
        if args.command in ("apply", "run"):
            run_applications(
                config, store, client, resume,
                job_id=getattr(args, "job_id", None), dry_run=args.dry_run,
            )
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except anthropic.AuthenticationError:
        print("❌ Your Anthropic API key was rejected. Check ANTHROPIC_API_KEY.")
        return 1
    except TypeError as exc:
        if "authentication method" not in str(exc):
            raise
        print("❌ No Anthropic API key found. Set it first, e.g.:  export ANTHROPIC_API_KEY=sk-ant-...")
        return 1
    finally:
        store.close()
    return 0


def init(config_path: Path) -> int:
    if config_path.exists():
        print(f"{config_path} already exists. Edit it directly.")
        return 0
    shutil.copy(PROJECT_DIR / "config.example.yaml", config_path)
    print(f"Created {config_path}. Next:")
    print("  1. Put your resume next to it (resume.pdf), or set resume_file to its path.")
    print("  2. Fill in your details, target roles, and companies in the config file.")
    print("  3. Run:  python -m job_agent search")
    return 0


def list_jobs(store: Store, args) -> int:
    jobs = store.all_jobs(args.status)
    if not args.status and not args.all:
        jobs = [j for j in jobs if j["status"] != "filtered_out"]
    if not jobs:
        print("No jobs saved yet. Run `python -m job_agent search`.")
        return 0
    print(f"{'score':>5}  {'status':<12} {'company':<20} {'title':<45} id")
    for job in jobs:
        score = "" if job["score"] is None else str(job["score"])
        print(
            f"{score:>5}  {job['status']:<12} {job['company'][:20]:<20} {job['title'][:45]:<45} {job['id']}"
        )
    counts = ", ".join(f"{n} {status}" for status, n in sorted(store.counts().items()))
    print(f"\n{counts}")
    return 0


def show_job(store: Store, args) -> int:
    job = store.get(args.job_id)
    if job is None:
        print(f"No job with id {args.job_id}.")
        return 1
    print(f"{job['title']} at {job['company']}")
    remote_note = " (remote)" if job["remote"] and "remote" not in job["location"].lower() else ""
    print(f"Location: {job['location']}{remote_note}")
    print(f"Status:   {job['status']}{' – ' + job['notes'] if job['notes'] else ''}")
    print(f"Link:     {job['url']}")
    if job["score_detail"]:
        detail = json.loads(job["score_detail"])
        print(f"\nScore {detail['score']}: {detail['summary']}")
        for heading, key in (("Strengths", "strengths"), ("Gaps", "gaps"), ("Dealbreakers", "dealbreakers")):
            if detail.get(key):
                print(f"{heading}:")
                for item in detail[key]:
                    print(f"  - {item}")
    print(f"\n{job['description'][:3000]}")
    return 0


def mark_job(store: Store, args) -> int:
    if store.get(args.job_id) is None:
        print(f"No job with id {args.job_id}.")
        return 1
    store.set_status(args.job_id, args.status, "set by hand")
    print(f"Marked {args.job_id} as {args.status}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
