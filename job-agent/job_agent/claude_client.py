"""Settings shared by every request this project sends to Claude."""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from .config import Config

# If a request is declined by Claude's safety checks, the API automatically
# retries it on Anthropic's recommended fallback model instead of failing.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeRefused(Exception):
    pass


def make_client() -> anthropic.Anthropic:
    # Reads your key from the ANTHROPIC_API_KEY environment variable.
    return anthropic.Anthropic()


def base_params(config: Config) -> dict:
    return {
        "model": config.claude.model,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def resume_block(path: Path) -> dict:
    """The resume as a message content block.

    It is marked for prompt caching: it is sent with every request, and cached
    input costs about a tenth of the normal price.
    """
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            "title": "Applicant's resume",
            "cache_control": {"type": "ephemeral"},
        }
    return {
        "type": "text",
        "text": f"<resume>\n{path.read_text()}\n</resume>",
        "cache_control": {"type": "ephemeral"},
    }


def applicant_profile(config: Config) -> str:
    a = config.applicant

    def yes_no(value: bool | None) -> str:
        return "not specified" if value is None else ("yes" if value else "no")

    lines = [
        f"Name: {a.full_name}",
        f"First name: {a.first_name}",
        f"Last name: {a.last_name}",
        f"Email: {a.email}",
        f"Phone: {a.phone or 'not specified'}",
        f"Current location: {a.location or 'not specified'}",
        f"LinkedIn: {a.linkedin or 'none'}",
        f"GitHub: {a.github or 'none'}",
        f"Website / portfolio: {a.website or 'none'}",
        f"Work authorization: {a.work_authorization or 'not specified'}",
        f"Needs visa sponsorship: {yes_no(a.needs_sponsorship)}",
        f"Willing to relocate: {yes_no(a.willing_to_relocate)}",
        f"Earliest start date: {a.earliest_start_date or 'not specified'}",
        f"Salary expectation: {a.salary_expectation or 'not specified'}",
    ]
    for question, answer in a.extra_answers.items():
        lines.append(f"{question}: {answer}")
    return "\n".join(lines)


def search_preferences(config: Config) -> str:
    s = config.search
    return "\n".join(
        [
            f"Target roles: {', '.join(s.roles) or 'any'}",
            f"Preferred locations: {', '.join(s.locations) or 'any'}",
            f"Remote only: {'yes' if s.remote_only else 'no'}",
        ]
    )


def text_of(message) -> str:
    return "\n".join(block.text for block in message.content if block.type == "text").strip()


def check_not_refused(message) -> None:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise ClaudeRefused(getattr(details, "explanation", None) or "Claude declined this request.")
