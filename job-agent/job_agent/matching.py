"""Claude reads each job next to your resume, scores the fit, and writes cover letters."""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field

from .claude_client import (
    applicant_profile,
    base_params,
    check_not_refused,
    search_preferences,
    text_of,
)
from .config import Config

SCORING_SYSTEM = """You screen job postings for one job seeker. You get their resume, \
their profile and preferences, and one job posting.

Judge two things together:
1. Would a hiring manager seriously consider this applicant? Compare the posting's \
requirements (skills, years of experience, seniority, degrees, licenses) with what the \
resume actually shows.
2. Does the job fit what the applicant wants (role, location or remote, work authorization)?

Be honest rather than encouraging: a high score sends an application that is likely to be \
rejected, which wastes the applicant's time.

Scoring guide:
- 85-100: strong match, meets nearly every requirement
- 70-84: good match, worth applying
- 50-69: a stretch
- 0-49: poor fit

Any dealbreaker caps the score at 30. Dealbreakers include: required experience far beyond \
the applicant's; a required degree, license or security clearance the applicant lacks; a \
location or remote-region limit the applicant can't meet; the posting rules out visa \
sponsorship and the applicant needs it."""

COVER_LETTER_SYSTEM = """You write cover letters for one job seeker, in their voice.

Rules:
- Use only facts from the resume and profile. Never invent experience, numbers, employers, \
skills or credentials.
- Make it specific to this company and role: connect two or three of the applicant's real \
accomplishments to the main requirements in the posting.
- Three short paragraphs, under 300 words in total. Plain, confident language. No clichés \
such as "I am writing to express my interest" or "I am a perfect fit".
- Start with "Dear Hiring Team," unless the posting names a person. End with "Sincerely," \
and the applicant's full name.
- Reply with the letter text only: no title, notes or commentary."""


class JobScore(BaseModel):
    score: int = Field(description="0 to 100, following the scoring guide")
    summary: str = Field(description="One sentence explaining the score")
    strengths: list[str] = Field(description="Requirements the applicant clearly meets")
    gaps: list[str] = Field(description="Requirements the applicant does not clearly meet")
    dealbreakers: list[str] = Field(description="Hard blockers; empty if there are none")


def _job_text(job) -> str:
    remote_note = " (remote)" if job["remote"] and "remote" not in job["location"].lower() else ""
    return (
        f"Company: {job['company']}\n"
        f"Title: {job['title']}\n"
        f"Location: {job['location']}{remote_note}\n"
        f"Link: {job['url']}\n\n"
        f"<job_description>\n{job['description']}\n</job_description>"
    )


def score_job(client: anthropic.Anthropic, config: Config, resume: dict, job) -> JobScore:
    response = client.beta.messages.parse(
        **base_params(config),
        max_tokens=16000,
        output_config={"effort": "low"},
        system=SCORING_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    # Same resume block first in every request, so it is cached.
                    resume,
                    {
                        "type": "text",
                        "text": f"<profile>\n{applicant_profile(config)}\n{search_preferences(config)}\n</profile>\n\n"
                        f"Score this job for the applicant:\n\n{_job_text(job)}",
                    },
                ],
            }
        ],
        output_format=JobScore,
    )
    check_not_refused(response)
    result = response.parsed_output
    if result is None:
        raise ValueError(f"Claude did not return a score (stop reason: {response.stop_reason}).")
    result.score = max(0, min(100, result.score))
    return result


def write_cover_letter(client: anthropic.Anthropic, config: Config, resume: dict, job) -> str:
    response = client.beta.messages.create(
        **base_params(config),
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=COVER_LETTER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    resume,
                    {
                        "type": "text",
                        "text": f"<profile>\n{applicant_profile(config)}\n</profile>\n\n"
                        f"Write the cover letter for this job:\n\n{_job_text(job)}",
                    },
                ],
            }
        ],
    )
    check_not_refused(response)
    letter = text_of(response)
    if not letter:
        raise ValueError(f"Claude returned no cover letter (stop reason: {response.stop_reason}).")
    return letter
