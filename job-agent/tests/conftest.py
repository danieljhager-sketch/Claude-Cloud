"""Shared test setup: a local web server that plays both the job site and the Claude API.

Nothing here touches the internet or spends money. The fake Claude API replays
scripted replies, so the tests exercise the real SDK, tool runner, browser and
database code end to end.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.config import Config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

JOB_PAGE = """<!doctype html><html><head><title>Backend Engineer - Acme</title></head><body>
<h1>Backend Engineer</h1><p>Build our APIs in Python.</p>
<a href="/apply">Apply for this job</a></body></html>"""

THANKS_PAGE = """<!doctype html><html><head><title>Thanks</title></head><body>
<h1>Thank you for applying!</h1><p>We received your application.</p></body></html>"""


class FakeSite(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.submissions: list[dict] = []
        self.api_requests: list[dict] = []
        self.claude = None  # a callable(request_body) -> reply dict, set by each test

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: FakeSite

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body: str, content_type: str = "text/html") -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        pages = {"/job": JOB_PAGE, "/apply": (FIXTURES / "apply.html").read_text()}
        if self.path in pages:
            self._send(200, pages[self.path])
        else:
            self._send(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if self.path == "/submitted":
            raw = self.rfile.read(length)
            header = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
            form = BytesParser(policy=email_policy).parsebytes(header + raw)
            fields = {}
            for part in form.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                fields[name] = filename if filename is not None else part.get_content().strip()
            self.server.submissions.append(fields)
            self._send(200, THANKS_PAGE)
        elif self.path.startswith("/v1/messages"):
            body = json.loads(self.rfile.read(length))
            self.server.api_requests.append({"headers": dict(self.headers), "body": body})
            reply = self.server.claude(body)
            self._send(200, json.dumps(reply), "application/json")
        else:
            self._send(404, "not found")


@pytest.fixture
def site():
    server = FakeSite()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture(scope="session", autouse=True)
def chromium_path():
    """Use an already-installed Chromium when Playwright's own download is missing."""
    preinstalled = "/opt/pw-browsers/chromium"
    if "CHROMIUM_PATH" not in os.environ and os.path.exists(preinstalled):
        os.environ["CHROMIUM_PATH"] = preinstalled


@pytest.fixture
def config(tmp_path) -> Config:
    resume = tmp_path / "resume.txt"
    resume.write_text("Jane Doe\nBackend engineer, 4 years of Python and Postgres at Widgets Inc.\n")
    return Config(
        applicant={
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "phone": "555-0100",
            "work_authorization": "Authorized to work in the US",
            "needs_sponsorship": False,
        },
        resume_file=resume,
        search={"roles": ["Engineer", "Developer"], "locations": ["Remote", "Austin"]},
        apply={"headless": True},
        base_dir=tmp_path,
    )


@pytest.fixture
def can_launch_browser():
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            args = {"headless": True}
            if os.environ.get("CHROMIUM_PATH"):
                args["executable_path"] = os.environ["CHROMIUM_PATH"]
            p.chromium.launch(**args).close()
    except Error as exc:
        pytest.skip(f"Chromium is not available: {exc.message[:100]}")

