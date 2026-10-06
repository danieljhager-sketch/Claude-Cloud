"""A real Chromium browser that Claude drives through a few simple actions.

Claude never sees raw HTML. `snapshot()` lists the page's form fields, buttons
and links, and tags each one with a short ref like "e12". Claude then acts on
those refs: fill e3 with "Jane", choose "Yes" in e7, click e20, and so on.
"""

from __future__ import annotations

import os
import re
import time
from html import escape
from pathlib import Path

from anthropic.lib.tools import ToolError
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, sync_playwright

MAX_SNAPSHOT_CHARS = 30_000

# Buttons that send the application. Claude must use the approval-gated
# submit tool for these instead of a plain click.
SUBMIT_WORDS = re.compile(r"\b(submit|send|complete (my )?application|finish)\b", re.I)
# Buttons that move around a multi-page form without sending it.
NAVIGATION_WORDS = re.compile(
    r"\b(next|continue|back|previous|save|add|upload|attach|remove|delete|edit|cancel)\b", re.I
)
# "Apply" is usually how you *reach* a form, but on a filled-in form it sends it.
APPLY_ONLY = re.compile(r"^\s*apply(\s+now)?\s*$", re.I)

SNAPSHOT_JS = r"""
(prefix) => {
  const MAX_LINKS = 40, MAX_OPTIONS = 30;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const textOf = (el) => clean(el ? (el.innerText || el.textContent) : '');
  const quote = (s) => '"' + s.replace(/"/g, "'") + '"';
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none';
  };
  const byIds = (ids) => clean(ids.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(' '));
  const labelOf = (el) => {
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    const lb = el.getAttribute('aria-labelledby');
    if (lb && byIds(lb)) return byIds(lb);
    if (el.labels && el.labels.length) {
      const t = clean(Array.from(el.labels).map(textOf).join(' '));
      if (t) return t;
    }
    if (el.placeholder) return clean(el.placeholder);
    if (el.title) return clean(el.title);
    let node = el.parentElement;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
      if (node.tagName === 'SELECT') continue;
      const t = textOf(node);
      if (t) return cut(t, 150);
    }
    return clean(el.name || el.id);
  };
  // The question a group of radio buttons / checkboxes answers.
  const questionOf = (el) => {
    const fs = el.closest('fieldset');
    if (fs && fs.querySelector('legend')) return textOf(fs.querySelector('legend'));
    const group = el.closest('[role="radiogroup"], [role="group"]');
    if (group) {
      const t = clean(group.getAttribute('aria-label')) ||
        (group.getAttribute('aria-labelledby') ? byIds(group.getAttribute('aria-labelledby')) : '');
      if (t) return t;
    }
    if (!el.name) return '';
    const sameName = document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`);
    let node = el.parentElement;
    while (node && node.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length < sameName.length) {
      node = node.parentElement;
    }
    if (!node) return '';
    let text = textOf(node);
    sameName.forEach((input) => {
      const l = input.labels && input.labels[0] ? textOf(input.labels[0]) : '';
      if (l) text = text.replace(l, '');
    });
    return cut(clean(text), 200);
  };
  const kindOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox' || t === 'radio' || t === 'file') return t;
      if (role === 'combobox') return 'combobox';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    if (role === 'combobox') return 'combobox';
    if (tag === 'button' || role === 'button') return 'button';
    if (role === 'checkbox' || role === 'switch') return 'checkbox';
    if (role === 'radio') return 'radio';
    if (role === 'option') return 'option';
    if (tag === 'a') return 'link';
    if (el.isContentEditable) return 'textarea';
    if (/^h[1-4]$/.test(tag) || tag === 'legend') return 'heading';
    return null;
  };

  const lines = [];
  let counter = window.__jobAgentCounter || 0;
  let links = 0;
  let lastQuestion = null;
  const selector = 'h1, h2, h3, h4, legend, input, textarea, select, button, a[href], ' +
    '[role="button"], [role="combobox"], [role="checkbox"], [role="switch"], [role="radio"], ' +
    '[role="option"], [contenteditable="true"]';
  for (const el of document.querySelectorAll(selector)) {
    const kind = kindOf(el);
    if (!kind) continue;
    if (kind !== 'file' && !visible(el)) continue;
    if (kind === 'heading') {
      const t = textOf(el);
      if (t) lines.push('## ' + cut(t, 120));
      continue;
    }
    if (kind === 'link' && ++links > MAX_LINKS) continue;
    if (!el.dataset.jobAgentRef) el.dataset.jobAgentRef = prefix + (++counter);
    const ref = el.dataset.jobAgentRef;

    let label = kind === 'button' || kind === 'link' || kind === 'option'
      ? clean(el.innerText || el.value || el.getAttribute('aria-label') || el.title)
      : labelOf(el);
    let line = `[${ref}] ${kind} ${quote(cut(label, 150))}`;

    if (kind === 'radio' || kind === 'checkbox') {
      const q = questionOf(el);
      if (q && q !== lastQuestion) {
        lines.push(`  question: ${quote(q)}`);
        lastQuestion = q;
      }
      const checked = el.checked !== undefined && el.tagName === 'INPUT'
        ? el.checked : el.getAttribute('aria-checked') === 'true';
      line += checked ? ' [checked]' : ' [not checked]';
    } else {
      lastQuestion = null;
    }
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
    if (required) line += ' required';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') line += ' disabled';

    if (kind === 'textbox' || kind === 'textarea') {
      const v = el.isContentEditable ? textOf(el) : (el.value || '');
      line += ` value=${quote(cut(clean(v), 100))}`;
    } else if (kind === 'select') {
      const opts = Array.from(el.options).map((o) => clean(o.text));
      const shown = opts.slice(0, MAX_OPTIONS).join(' | ');
      const more = opts.length > MAX_OPTIONS ? ` | …${opts.length - MAX_OPTIONS} more` : '';
      const sel = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
      line += ` selected=${quote(sel)} options=[${shown}${more}]`;
    } else if (kind === 'combobox') {
      const box = el.closest('[class*="control"], [class*="container"], [class*="select"]') || el.parentElement;
      line += ` shows=${quote(cut(el.value || textOf(box), 80))}`;
    } else if (kind === 'file') {
      const names = el.files ? Array.from(el.files).map((f) => f.name).join(', ') : '';
      line += names ? ` attached=${quote(names)}` : ' (no file attached)';
    } else if (kind === 'link') {
      line += ' -> ' + cut(el.getAttribute('href') || '', 100);
    }
    lines.push(line);
  }
  window.__jobAgentCounter = counter;
  if (links > MAX_LINKS) lines.push(`(${links - MAX_LINKS} more links not shown)`);
  return lines;
}
"""


class BrowserError(ToolError):
    """An action failed in a way Claude should hear about and can recover from.

    The tool runner sends the message back to Claude as an error result.
    """


class BrowserSession:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self.context = None
        self.page = None
        self._filled_on_url: str | None = None

    # ------------------------------------------------------------ lifecycle

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        launch_args = {"headless": self.headless}
        # Lets you point at a Chromium that is already installed.
        if os.environ.get("CHROMIUM_PATH"):
            launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
        self._browser = self._playwright.chromium.launch(**launch_args)
        self.context = self._browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()

    def new_page(self) -> None:
        """Start fresh for the next application."""
        for page in list(self.context.pages):
            page.close()
        self.page = self.context.new_page()
        self._filled_on_url = None

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except PlaywrightError:
            pass
        self._browser = self._playwright = None

    # ------------------------------------------------------------ reading

    def goto(self, url: str) -> str:
        if not re.match(r"^https?://", url):
            raise BrowserError("Only http:// and https:// addresses can be opened.")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not open {url}: {exc.message}") from exc
        self._settle()
        return f"Opened {self.page.url} (title: {self.page.title()!r})"

    def snapshot(self) -> str:
        """Everything Claude can interact with on the page, one element per line."""
        self._settle(quick=True)
        sections = [f"Page title: {self.page.title()}", f"URL: {self.page.url}"]
        for index, frame in enumerate(self.page.frames):
            prefix = "e" if frame == self.page.main_frame else f"f{index}e"
            try:
                lines = frame.evaluate(SNAPSHOT_JS, prefix)
            except PlaywrightError:
                continue  # frame went away or is not readable
            if not lines:
                continue
            if frame != self.page.main_frame:
                sections.append(f"\n--- embedded frame: {frame.url} ---")
            sections.extend(lines)
        text = "\n".join(sections)
        if len(text) > MAX_SNAPSHOT_CHARS:
            text = text[:MAX_SNAPSHOT_CHARS] + "\n(page listing cut short: it is very long)"
        return text

    def visible_text(self, max_chars: int = 12_000) -> str:
        parts = []
        for frame in self.page.frames:
            try:
                parts.append(frame.locator("body").inner_text(timeout=5_000))
            except PlaywrightError:
                continue
        text = re.sub(r"\n\s*\n+", "\n\n", "\n".join(parts)).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n(text cut short)"
        return text

    def invalid_fields(self) -> list[str]:
        """Fields the browser itself flags as missing or invalid (it won't submit until they're fixed)."""
        found = []
        for frame in self.page.frames:
            try:
                found += frame.evaluate(
                    """() => Array.from(document.querySelectorAll('input, select, textarea'))
                        .filter((el) => el.willValidate && !el.validity.valid)
                        .map((el) => {
                            const label = (el.labels && el.labels[0] ? el.labels[0].innerText : '') ||
                                el.getAttribute('aria-label') || el.name || el.id;
                            const ref = el.dataset.jobAgentRef ? `[${el.dataset.jobAgentRef}] ` : '';
                            return ref + label.replace(/\\s+/g, ' ').trim() + ': ' + el.validationMessage;
                        })"""
                )
            except PlaywrightError:
                continue
        return found

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return path

    # ------------------------------------------------------------ acting

    def element(self, ref: str) -> Locator:
        if not re.fullmatch(r"(f\d+)?e\d+", ref):
            raise BrowserError(f"{ref!r} is not a ref. Use refs like e12 from read_page.")
        for frame in self.page.frames:
            locator = frame.locator(f'[data-job-agent-ref="{ref}"]')
            try:
                if locator.count():
                    return locator.first
            except PlaywrightError:
                continue
        raise BrowserError(f"No element {ref} on this page. Call read_page to get fresh refs.")

    def _info(self, locator: Locator) -> dict:
        return locator.evaluate(
            """(el) => ({
                tag: el.tagName.toLowerCase(),
                type: (el.type || '').toLowerCase(),
                role: (el.getAttribute('role') || '').toLowerCase(),
                text: (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim(),
                editable: el.isContentEditable,
                submitsForm: !!el.form && el.type === 'submit',
                formFields: el.form ? el.form.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea'
                ).length : 0,
            })"""
        )

    def fill(self, ref: str, value: str) -> str:
        locator = self.element(ref)
        info = self._info(locator)
        if info["type"] in ("checkbox", "radio", "file") or info["tag"] == "select" or info["role"] == "combobox":
            raise BrowserError(
                f"{ref} is a {info['role'] or info['type'] or info['tag']}, not a text box. "
                "Use choose_option, set_checkbox or upload_file instead."
            )
        try:
            locator.fill(value, timeout=10_000)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not type into {ref}: {exc.message}") from exc
        self._filled_on_url = self.page.url
        return "ok"

    def choose(self, ref: str, option: str) -> str:
        locator = self.element(ref)
        info = self._info(locator)
        if info["tag"] == "select":
            return self._choose_native(ref, locator, option)
        return self._choose_custom(ref, locator, info, option)

    def _choose_native(self, ref: str, locator: Locator, option: str) -> str:
        labels = [t.strip() for t in locator.locator("option").all_inner_texts()]
        match = _best_match(option, labels)
        if match is None:
            raise BrowserError(f"No option like {option!r} in {ref}. Options: {labels[:60]}")
        locator.select_option(label=match, timeout=10_000)
        self._filled_on_url = self.page.url
        return f"Selected {match!r}"

    def _choose_custom(self, ref: str, locator: Locator, info: dict, option: str) -> str:
        """Dropdowns built from <div>s (React and friends): open, type, pick."""
        try:
            locator.click(timeout=10_000)
            if info["tag"] == "input" and info["type"] not in ("button", "submit"):
                locator.fill(option, timeout=5_000)
            time.sleep(0.6)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not open {ref}: {exc.message}") from exc
        options = self.page.locator('[role="option"]:visible')
        labels = [t.strip() for t in options.all_inner_texts()]
        match = _best_match(option, labels)
        if match is None:
            if info["tag"] == "input":
                locator.fill("")
            self.page.keyboard.press("Escape")
            shown = labels[:60] if labels else "none appeared"
            raise BrowserError(f"No option like {option!r} in {ref}. Options seen: {shown}")
        options.nth(labels.index(match)).click(timeout=10_000)
        self._filled_on_url = self.page.url
        return f"Selected {match!r}"

    def set_checked(self, ref: str, checked: bool) -> str:
        locator = self.element(ref)
        info = self._info(locator)
        try:
            if info["tag"] == "input":
                locator.set_checked(checked, timeout=10_000)
            elif (locator.get_attribute("aria-checked") == "true") != checked:
                locator.click(timeout=10_000)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not change {ref}: {exc.message}") from exc
        self._filled_on_url = self.page.url
        return "ok"

    def upload(self, ref: str, path: Path) -> str:
        locator = self.element(ref)
        if self._info(locator)["type"] != "file":
            raise BrowserError(
                f"{ref} is not a file input. Look in read_page for a 'file' element near the upload button."
            )
        try:
            locator.set_input_files(str(path), timeout=10_000)
        except PlaywrightError as exc:
            raise BrowserError(f"Upload failed: {exc.message}") from exc
        self._filled_on_url = self.page.url
        self._settle(quick=True)
        return f"Attached {path.name}"

    def looks_like_submit(self, ref: str) -> bool:
        info = self._info(self.element(ref))
        text = info["text"]
        if SUBMIT_WORDS.search(text):
            return True
        if NAVIGATION_WORDS.search(text):
            return False
        if info["submitsForm"] and info["formFields"] >= 3:
            return True  # sends a real form, whatever its label says
        return bool(APPLY_ONLY.match(text)) and self._filled_on_url == self.page.url

    def click(self, ref: str, allow_submit: bool = False) -> str:
        locator = self.element(ref)
        if not allow_submit and self.looks_like_submit(ref):
            raise BrowserError(
                f"{ref} looks like the button that sends the application. "
                "Use submit_application for it, once every field is filled."
            )
        pages_before = len(self.context.pages)
        try:
            locator.click(timeout=10_000)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not click {ref}: {exc.message}") from exc
        self._settle()
        if len(self.context.pages) > pages_before:
            self.page = self.context.pages[-1]
            self.page.bring_to_front()
            self._settle()
            return f"Clicked. A new tab opened and is now active: {self.page.url}"
        return f"Clicked. Now at {self.page.url}"

    def _settle(self, quick: bool = False) -> None:
        """Give the page a moment to finish loading after an action."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10_000)
            self.page.wait_for_load_state("networkidle", timeout=1_500 if quick else 5_000)
        except PlaywrightError:
            pass  # some pages never go fully idle; that's fine

    # ------------------------------------------------------------ files

    def text_to_pdf(self, text: str, path: Path, title: str = "") -> Path:
        """Save plain text as a simple PDF, e.g. a cover letter to upload."""
        # Printing to PDF only works in a headless browser, so use a separate one.
        launch_args = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
        printer = self._playwright.chromium.launch(**launch_args)
        try:
            page = printer.new_page()
            paragraphs = "".join(f"<p>{escape(p)}</p>" for p in text.split("\n\n") if p.strip())
            page.set_content(
                "<html><head><meta charset='utf-8'><title>"
                + escape(title)
                + "</title><style>body{font-family:Georgia,serif;font-size:11.5pt;line-height:1.45;"
                "margin:0.9in;} p{margin:0 0 0.8em; white-space:pre-wrap;}</style></head><body>"
                + paragraphs
                + "</body></html>"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(path), format="Letter")
        finally:
            printer.close()
        return path


def _best_match(wanted: str, options: list[str]) -> str | None:
    """Exact match first (ignoring case), then 'starts with', then 'contains'."""
    want = wanted.strip().lower()
    if not want:
        return None
    lowered = [o.lower() for o in options]
    for test in (
        lambda o: o == want,
        lambda o: o.startswith(want),
        lambda o: want in o,
    ):
        for original, low in zip(options, lowered):
            if test(low):
                return original
    return None
