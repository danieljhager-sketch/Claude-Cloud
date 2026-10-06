import json

import pytest

from job_agent.config import Search
from job_agent.sources import (
    Job,
    board_from_url,
    filter_reason,
    html_to_text,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
    parse_remotive,
)

from conftest import FIXTURES


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_greenhouse():
    jobs = parse_greenhouse(load("greenhouse.json"), "acme")
    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "greenhouse:4012345"
    assert job.title == "Software Engineer, Backend"
    assert job.company == "Acme"
    assert job.remote is True
    assert job.apply_url == "https://job-boards.greenhouse.io/acme/jobs/4012345"
    assert "We build rockets & more." in job.description
    assert "- 3+ years Python" in job.description
    assert jobs[1].company == "acme"  # falls back to the board name


def test_lever():
    [job] = parse_lever(load("lever.json"), "widgets")
    assert job.id == "lever:a1b2c3d4-0000-1111-2222-333344445555"
    assert job.location == "Austin, TX"
    assert job.apply_url.endswith("/apply")
    assert job.remote is False
    assert "Requirements" in job.description and "Postgres" in job.description
    assert "We offer equity." in job.description
    assert job.posted_at.startswith("2024-09-30")


def test_ashby_skips_unlisted_jobs():
    [job] = parse_ashby(load("ashby.json"), "gizmo")
    assert job.title == "Python Developer"
    assert job.location == "San Francisco, Remote (US)"
    assert job.remote is True
    assert job.apply_url.endswith("/application")


def test_remotive():
    [job] = parse_remotive(load("remotive.json"))
    assert job.company == "Doodads Inc"
    assert job.location == "USA"
    assert job.remote is True
    assert job.description == "Join us.\nWork remotely."


def test_html_to_text_handles_lists_and_breaks():
    assert html_to_text("<p>One</p><p>Two<br>Three</p><ul><li>A</li></ul>") == "One\nTwo\nThree\n\n- A"
    assert html_to_text("") == ""


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
        ("https://job-boards.greenhouse.io/Acme/jobs/123?gh_src=x", ("greenhouse", "acme")),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=1", ("greenhouse", "acme")),
        ("https://jobs.lever.co/widgets/abc-123/apply", ("lever", "widgets")),
        ("https://jobs.eu.lever.co/widgets", ("lever", "widgets")),
        ("https://jobs.ashbyhq.com/gizmo/7f9e/application", ("ashby", "gizmo")),
        ("https://www.greenhouse.io/blog/hiring", None),
        ("https://jobs.lever.co/", None),
        ("https://example.com/jobs.lever.co/acme", None),
        ("not a url", None),
    ],
)
def test_board_from_url(url, expected):
    assert board_from_url(url) == expected


def make_job(title="Backend Engineer", location="Austin, TX", remote=False):
    return Job("lever", "1", "Acme", title, location, "u", "u", "d", remote=remote)


def test_filters():
    search = Search(roles=["Engineer"], exclude_title_words=["Manager"], locations=["Remote", "Austin"])
    assert filter_reason(make_job(), search) is None
    assert filter_reason(make_job(title="Designer"), search) == "title does not match any target role"
    assert filter_reason(make_job(title="Engineering Manager"), search) == "title contains an excluded word"
    assert filter_reason(make_job(location="Boston, MA"), search) == "location does not match"
    # Remote jobs pass when "Remote" is wanted, even if the location names a city.
    assert filter_reason(make_job(location="Boston, MA", remote=True), search) is None
    assert filter_reason(make_job(), Search(remote_only=True)) == "not remote"
    assert filter_reason(make_job(location="Anywhere"), Search()) is None
