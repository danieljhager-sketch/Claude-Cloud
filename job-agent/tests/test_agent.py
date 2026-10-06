"""End-to-end tests against a fake Claude API (see conftest.py).

Each test scripts the replies "Claude" sends and checks that the real SDK,
tool runner, browser and database do the right thing with them.
"""

import json
import re

import anthropic
import pytest

from job_agent.apply import RunContext, run_application
from job_agent.browser import BrowserSession
from job_agent.claude_client import FALLBACK_BETA, resume_block
from job_agent.discover import discover_boards
from job_agent.pipeline import run_scoring
from job_agent.sources import Job
from job_agent.store import Store

from helpers import message, tool_use


def client_for(site):
    return anthropic.Anthropic(api_key="test-key", base_url=site.url, max_retries=0)


def last_tool_results(body) -> dict[str, dict]:
    """The tool results in the latest request, keyed by tool_use_id."""
    content = body["messages"][-1]["content"]
    return {block["tool_use_id"]: block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"}


def last_result_text(body) -> str:
    results = list(last_tool_results(body).values())
    content = results[-1]["content"]
    return content if isinstance(content, str) else " ".join(c.get("text", "") for c in content)


def ref(snapshot: str, kind: str, label: str) -> str:
    return re.search(rf"\[(e\d+)\] {kind} \"{re.escape(label)}", snapshot).group(1)


def scripted(*steps):
    """Turn a list of step functions into a fake Claude. Each step gets the request body."""
    queue = list(steps)

    def reply(body):
        return queue.pop(0)(body)

    reply.remaining = queue
    return reply


# ---------------------------------------------------------------- scoring


def test_scoring_saves_claudes_verdict(site, config):
    store = Store(":memory:")
    store.add_job(Job("lever", "1", "Acme", "Backend Engineer", "Remote", "u", "u", "Python, Postgres", remote=True))
    verdict = {
        "score": 88,
        "summary": "Strong Python and Postgres match.",
        "strengths": ["4 years of Python"],
        "gaps": [],
        "dealbreakers": [],
    }
    site.claude = scripted(lambda body: message([{"type": "text", "text": json.dumps(verdict)}], "end_turn"))

    run_scoring(config, store, client_for(site), resume_block(config.resume_path))

    row = store.get("lever:1")
    assert row["status"] == "scored" and row["score"] == 88
    request = site.api_requests[0]
    assert request["body"]["model"] == "claude-opus-5-5"
    assert request["body"]["fallbacks"] == "default"
    assert FALLBACK_BETA in request["headers"]["anthropic-beta"]
    assert request["body"]["output_config"]["effort"] == "low"
    assert request["body"]["output_config"]["format"]["type"] == "json_schema"
    resume = request["body"]["messages"][0]["content"][0]
    assert resume["cache_control"] == {"type": "ephemeral"} and "4 years of Python" in resume["text"]


# ---------------------------------------------------------------- discovery


def test_discovery_reads_board_urls_and_resumes_paused_searches(site, config):
    search_result = {
        "type": "web_search_tool_result",
        "tool_use_id": "srvtoolu_1",
        "content": [
            {"type": "web_search_result", "url": "https://jobs.lever.co/widgets/123", "title": "Backend Engineer",
             "encrypted_content": "abc", "page_age": None},
            {"type": "web_search_result", "url": "https://www.greenhouse.io/about", "title": "About",
             "encrypted_content": "abc", "page_age": None},
        ],
    }
    first = [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "backend engineer"}},
             search_result]
    second = [{"type": "text", "text": "Found:\nhttps://jobs.ashbyhq.com/gizmo/7f9e\nhttps://boards.greenhouse.io/acme/jobs/1"}]
    site.claude = scripted(lambda body: message(first, "pause_turn"), lambda body: message(second, "end_turn"))

    found = discover_boards(client_for(site), config, resume_block(config.resume_path))

    assert found == {("lever", "widgets"), ("ashby", "gizmo"), ("greenhouse", "acme")}
    first_request, second_request = (r["body"] for r in site.api_requests)
    tool = first_request["tools"][0]
    assert tool["type"] == "web_search_20260209"
    assert tool["allowed_domains"] == ["greenhouse.io", "lever.co", "ashbyhq.com"]
    # The paused turn is sent back unchanged so the search can continue.
    assert second_request["messages"][-1]["role"] == "assistant"
    assert second_request["messages"][-1]["content"][0]["type"] == "server_tool_use"


# ---------------------------------------------------------------- applying


@pytest.fixture
def application(site, config, tmp_path, can_launch_browser):
    """Everything run_application needs, with a fake approver you can program."""
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 resume")
    letter_pdf = tmp_path / "cover_letter.pdf"
    letter_pdf.write_bytes(b"%PDF-1.4 letter")
    replies = []
    ctx = RunContext(
        config=config,
        folder=tmp_path / "app",
        files={"resume": resume_pdf, "cover_letter": letter_pdf},
        answers_path=tmp_path / "answers.json",
        interactive=True,
        ask=lambda prompt: replies.pop(0),
    )
    job = {
        "id": "lever:1", "company": "Acme", "title": "Backend Engineer", "location": "Remote",
        "url": site.url + "/job", "apply_url": site.url + "/job", "description": "Build APIs in Python.",
    }
    with BrowserSession(headless=True) as session:
        yield {"ctx": ctx, "job": job, "session": session, "replies": replies, "config": config}


def run(site, app):
    return run_application(
        client_for(site), app["session"], app["ctx"], app["job"],
        resume_block(app["config"].resume_path), "Dear Hiring Team, ...",
    )


def fill_whole_form(site):
    """Steps that open the job, reach the form and fill every field."""
    state = {}

    def open_job(body):
        return message([{"type": "text", "text": "Opening the job."}, tool_use("goto", url=site.url + "/job")])

    def read_job(body):
        return message([tool_use("read_page")])

    def click_apply(body):
        return message([tool_use("click", ref=ref(last_result_text(body), "link", "Apply for this job"))])

    def read_form(body):
        return message([tool_use("read_page")])

    def fill(body):
        snap = state["snap"] = last_result_text(body)
        return message([
            tool_use("fill_fields", fields=[
                {"ref": ref(snap, "textbox", "First Name"), "value": "Jane"},
                {"ref": ref(snap, "textbox", "Last Name"), "value": "Doe"},
                {"ref": ref(snap, "textbox", "Email"), "value": "jane@example.com"},
                {"ref": ref(snap, "textarea", "Why do you want"), "value": "I like APIs."},
            ]),
            tool_use("choose_option", ref=ref(snap, "select", "Are you legally"), option="Yes"),
            tool_use("choose_option", ref=ref(snap, "combobox", "Will you now"), option="No"),
            tool_use("set_checkbox", ref=ref(snap, "radio", "Decline to self-identify"), checked=True),
            tool_use("set_checkbox", ref=ref(snap, "checkbox", "I agree"), checked=True),
            tool_use("upload_file", ref=ref(snap, "file", "Resume/CV"), document="resume"),
            tool_use("upload_file", ref=ref(snap, "file", "Cover Letter"), document="cover_letter"),
            tool_use("click", ref=ref(snap, "button", "Yes")),
        ])

    def try_plain_click_on_submit(body):
        results = list(last_tool_results(body).values())
        assert not any(r.get("is_error") for r in results), results
        return message([tool_use("click", ref=ref(state["snap"], "button", "Submit application"))])

    def submit(body):
        [result] = last_tool_results(body).values()
        assert result["is_error"] is True and "submit_application" in last_result_text(body)
        return message([tool_use("submit_application", ref=ref(state["snap"], "button", "Submit application"),
                                 answers_summary="First name: Jane\nSponsorship: No")])

    return [open_job, read_job, click_apply, read_form, fill, try_plain_click_on_submit, submit]


def test_agent_fills_and_submits_after_approval(site, application):
    def confirm(body):
        assert "Thank you for applying" in last_result_text(body)
        return message([tool_use("finish", status="submitted", notes="Confirmation page shown.")])

    site.claude = scripted(*fill_whole_form(site), confirm, lambda body: message([{"type": "text", "text": "Applied."}], "end_turn"))
    application["replies"].append("y")

    result = run(site, application)

    assert (result.status, result.notes) == ("submitted", "Confirmation page shown.")
    [sent] = site.submissions
    assert sent["first_name"] == "Jane" and sent["email"] == "jane@example.com"
    assert sent["authorized"] == "Yes" and sent["sponsorship"] == "No" and sent["over18"] == "yes"
    assert sent["gender"] == "decline" and sent["privacy"] == "on"
    assert sent["resume"] == "resume.pdf" and sent["cover_letter"] == "cover_letter.pdf"
    assert (application["ctx"].folder / "before_submit.png").exists()

    first = site.api_requests[0]["body"]
    assert first["fallbacks"] == "default" and first["output_config"]["effort"] == "high"
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Jane Doe" in first["system"][0]["text"]
    assert {t["name"] for t in first["tools"]} >= {"goto", "read_page", "submit_application", "finish"}


def test_nothing_is_sent_when_the_applicant_says_no(site, application):
    def declined(body):
        assert "declined" in last_result_text(body)
        return message([tool_use("finish", status="declined", notes="Applicant said no.")])

    site.claude = scripted(*fill_whole_form(site), declined, lambda body: message([{"type": "text", "text": "OK."}], "end_turn"))
    application["replies"].append("n")

    result = run(site, application)

    assert result.status == "declined"
    assert site.submissions == []


def test_change_requests_go_back_to_claude(site, application):
    def change(body):
        assert "wants changes before submitting: use my cell 555-0199" in last_result_text(body)
        return message([tool_use("finish", status="declined", notes="Stopping for the test.")])

    site.claude = scripted(*fill_whole_form(site), change, lambda body: message([{"type": "text", "text": "OK."}], "end_turn"))
    application["replies"].append("use my cell 555-0199")

    run(site, application)
    assert site.submissions == []


def test_dry_run_never_submits(site, application):
    application["ctx"].dry_run = True

    def finish_anyway(body):
        # Even if Claude claims success afterwards, a dry run stays a dry run.
        return message([tool_use("finish", status="submitted", notes="Done.")])

    site.claude = scripted(
        *fill_whole_form(site), finish_anyway,
        lambda body: message([{"type": "text", "text": "Dry run done."}], "end_turn"),
    )

    result = run(site, application)

    assert result.status == "dry_run"
    assert site.submissions == []
    assert (application["ctx"].folder / "before_submit.png").exists()


def test_answers_from_the_applicant_are_saved_for_next_time(site, application):
    def ask(body):
        return message([tool_use("ask_user", question="Expected salary?")])

    def done(body):
        assert last_result_text(body) == "$120,000"
        return message([tool_use("finish", status="failed", notes="Test stops here.")])

    site.claude = scripted(ask, done, lambda body: message([{"type": "text", "text": "OK."}], "end_turn"))
    application["replies"].append("$120,000")

    run(site, application)

    saved = json.loads(application["ctx"].answers_path.read_text())
    assert saved == {"Expected salary?": "$120,000"}
