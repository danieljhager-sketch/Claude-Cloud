import re

import pytest

from job_agent.browser import BrowserError, BrowserSession, _best_match


def ref(snapshot: str, kind: str, label: str) -> str:
    match = re.search(rf"\[(e\d+)\] {kind} \"{re.escape(label)}", snapshot)
    assert match, f"no {kind} {label!r} in:\n{snapshot}"
    return match.group(1)


@pytest.fixture
def browser(can_launch_browser):
    with BrowserSession(headless=True) as session:
        yield session


def test_snapshot_lists_fields_with_labels(browser, site):
    browser.goto(site.url + "/apply")
    snap = browser.snapshot()
    assert "## Backend Engineer" in snap
    assert re.search(r'\[e\d+\] textbox "First Name \*" required value=""', snap)
    assert re.search(r'\[e\d+\] file "Resume/CV \*" required \(no file attached\)', snap)
    assert re.search(r'select "Are you legally authorized.*" required selected="Select..." options=\[Select... \| Yes \| No\]', snap)
    assert re.search(r'combobox "Will you now or in the future require visa sponsorship\? \*" required', snap)
    assert 'question: "Gender (voluntary)"' in snap
    assert re.search(r'radio "Decline to self-identify" \[not checked\]', snap)
    assert re.search(r'button "Submit application"', snap)
    # Refs stay the same when the page is read again.
    assert browser.snapshot() == snap


def test_filling_every_kind_of_field(browser, site, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    browser.goto(site.url + "/apply")
    snap = browser.snapshot()

    browser.fill(ref(snap, "textbox", "First Name"), "Jane")
    assert browser.choose(ref(snap, "select", "Are you legally"), "yes") == "Selected 'Yes'"
    assert browser.choose(ref(snap, "combobox", "Will you now"), "No") == "Selected 'No'"
    browser.set_checked(ref(snap, "radio", "Decline to self-identify"), True)
    browser.set_checked(ref(snap, "checkbox", "I agree"), True)
    assert browser.upload(ref(snap, "file", "Resume/CV"), resume) == "Attached resume.pdf"

    snap = browser.snapshot()
    assert re.search(r'textbox "First Name \*" required value="Jane"', snap)
    assert 'selected="Yes"' in snap
    assert 'shows="No"' in snap
    assert re.search(r'radio "Decline to self-identify" \[checked\]', snap)
    assert 'attached="resume.pdf"' in snap


def test_wrong_tool_for_field_gives_a_helpful_error(browser, site):
    browser.goto(site.url + "/apply")
    snap = browser.snapshot()
    with pytest.raises(BrowserError, match="not a text box"):
        browser.fill(ref(snap, "select", "Are you legally"), "Yes")
    with pytest.raises(BrowserError, match="No option like 'Maybe'"):
        browser.choose(ref(snap, "select", "Are you legally"), "Maybe")
    with pytest.raises(BrowserError, match="No element e999"):
        browser.click("e999")
    with pytest.raises(BrowserError, match="not a ref"):
        browser.click("#submit")
    with pytest.raises(BrowserError, match="Only http"):
        browser.goto("file:///etc/passwd")


def test_plain_click_cannot_submit(browser, site, tmp_path):
    browser.goto(site.url + "/apply")
    snap = browser.snapshot()
    submit = ref(snap, "button", "Submit application")
    with pytest.raises(BrowserError, match="submit_application"):
        browser.click(submit)
    assert site.submissions == []

    # Required fields are empty, so the browser itself refuses to send the form.
    browser.click(submit, allow_submit=True)
    assert site.submissions == []
    blocked = browser.invalid_fields()
    assert any(re.match(r"\[e\d+\] First Name \*: ", field) for field in blocked)
    assert any("Resume/CV" in field for field in blocked)

    browser.fill(ref(snap, "textbox", "First Name"), "Jane")
    browser.fill(ref(snap, "textbox", "Last Name"), "Doe")
    browser.fill(ref(snap, "textbox", "Email"), "jane@example.com")
    browser.choose(ref(snap, "select", "Are you legally"), "Yes")
    browser.set_checked(ref(snap, "checkbox", "I agree"), True)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    browser.upload(ref(snap, "file", "Resume/CV"), resume)
    assert browser.invalid_fields() == []
    browser.click(submit, allow_submit=True)
    assert len(site.submissions) == 1
    assert site.submissions[0]["first_name"] == "Jane"
    assert site.submissions[0]["resume"] == "resume.pdf"
    assert "Thank you for applying" in browser.visible_text()


def test_any_button_that_sends_a_real_form_needs_approval(browser):
    browser.page.set_content(
        """<form><input name=a><input name=b><input type=file name=c>
        <button>Done</button><button>Next step</button><button type=button>Yes</button></form>"""
    )
    snap = browser.snapshot()
    assert browser.looks_like_submit(ref(snap, "button", "Done"))
    assert not browser.looks_like_submit(ref(snap, "button", "Next step"))
    assert not browser.looks_like_submit(ref(snap, "button", "Yes"))


def test_apply_link_is_clickable_before_any_field_is_filled(browser, site):
    browser.goto(site.url + "/job")
    snap = browser.snapshot()
    result = browser.click(ref(snap, "link", "Apply for this job"))
    assert result.endswith("/apply")


def test_text_to_pdf(browser, tmp_path):
    path = browser.text_to_pdf("Dear Hiring Team,\n\nHello.", tmp_path / "letter.pdf", "Letter")
    assert path.read_bytes().startswith(b"%PDF")


def test_best_match():
    options = ["Select...", "Yes", "No", "Decline to self-identify"]
    assert _best_match("no", options) == "No"
    assert _best_match("decline", options) == "Decline to self-identify"
    assert _best_match("YES", options) == "Yes"
    assert _best_match("maybe", options) is None
