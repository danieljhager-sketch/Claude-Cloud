# Job Agent

An agent that finds job openings, uses Claude to pick the ones that fit your resume, writes a
cover letter for each, and fills out the application in a real browser. By default it **stops and
asks you before every final Submit**.

It works in three steps, which you can run separately or all at once:

1. **Search**: reads the public job boards of companies you list (Greenhouse, Lever and Ashby, which
   many tech companies use) plus remote jobs from Remotive. Optionally, Claude searches the web
   for more companies hiring for your kind of role. Keyword and location filters drop obvious
   mismatches for free.
2. **Score**: Claude reads each remaining job next to your resume and gives it a 0 to 100 fit score,
   with strengths, gaps, and dealbreakers (for example "needs a security clearance").
3. **Apply**: for the best matches, Claude writes a cover letter, opens the application in a browser,
   fills in every field, attaches your resume, shows you a summary and a screenshot, and waits for
   your "y" before submitting.

## Setup (one time)

You need Python 3.10 or newer (`python3 --version` shows yours) and an
[Anthropic API key](https://console.anthropic.com/settings/keys).

```bash
cd job-agent
python3 -m venv .venv            # a private folder for this project's packages
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium      # the browser the agent drives

export ANTHROPIC_API_KEY=sk-ant-...   # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."

python -m job_agent init         # creates config.yaml
```

Then:

1. Copy your resume into this folder as `resume.pdf` (a `.txt` or `.md` file works too).
2. Open `config.yaml` and fill in your details, the job titles you want, your locations, and the
   companies to check. The comments in the file explain each setting.

Your `config.yaml`, resume, `data/` and `applications/` folders are listed in `.gitignore`, so git
won't commit them.

## Using it

```bash
python -m job_agent search          # find jobs and save them
python -m job_agent score           # Claude rates each new job against your resume
python -m job_agent list            # see them, best first
python -m job_agent show lever:abc  # one job's details and why it got its score

python -m job_agent apply --dry-run # fill out forms but never submit: try this first!
python -m job_agent apply           # apply to your top matches, asking before each submit
python -m job_agent apply --job lever:abc   # apply to one job you picked

python -m job_agent run             # search + score + apply in one go
python -m job_agent mark lever:abc skipped  # never apply to this one
```

While applying, the browser window opens and the terminal shows each step Claude takes. When the
form is ready you'll see:

```
======================================================================
  READY TO SUBMIT. Here is what Claude entered:

  First name: Jane
  ...
  Screenshot of the form: applications/acme-backend-engineer-1a2b3c/before_submit.png
======================================================================
  Submit? [y]es / [n]o / or type what to change:
```

Type `y` to send it, `n` to skip it, or type a correction ("use my cell number 555-0199") and
Claude will fix it and ask again. You can also fix fields yourself in the browser window before you
answer.

Claude may also ask you a question the form needs and your profile doesn't answer (for example
"Expected salary?"). Your answer is saved in `data/answers.json` and reused next time. If a site
shows a CAPTCHA or needs a login, Claude asks you to deal with it in the browser window.

**Tips**

- Each job gets a folder in `applications/` with its cover letter and screenshots. If you edit
  `cover_letter.txt` before applying, your edited version is used.
- Look at `list` and `show` before applying. Use `mark <id> skipped` on anything you don't want.
- Fewer, better applications beat many weak ones. Raise `min_score` if too many poor matches get
  through.

## What it costs

Every Claude call is billed to your API key. Rough costs with the default model
(`claude-opus-5-5`):

| Step | Approximate cost |
|---|---|
| Scoring | 2 to 5 cents per job |
| Cover letter | about 5 cents per job |
| Filling out an application | 50 cents to 2 dollars, depending on the form |
| Web search for companies | about 20 to 50 cents per `search` run |

Your resume is cached between requests, which keeps repeated calls cheap. `max_jobs_to_score` and
`max_applications_per_run` cap each run. You can set a cheaper model in `config.yaml` (for example
`claude-sonnet-5-5`), at some cost in judgment. Check your real spending in the
[Anthropic Console](https://console.anthropic.com).

## Safety and limits

- **Approval before submitting** is on by default (`require_approval: true`). The agent also
  blocks plain clicks on buttons that look like they send the form, so Claude has to use the
  submit step, which asks you, takes screenshots, and records what happened.
- **No made-up answers.** Claude may only use your profile, your resume, and your saved answers. If
  a required question isn't covered, it asks you. Voluntary demographic questions get "decline to
  self-identify" unless you set an answer.
- **No accounts, logins or payments.** Sites that need them (Workday, for example) are marked
  `needs_human` so you can apply yourself.
- **Web pages are treated as data.** Claude is told to ignore any instructions written on a page.
- **Only your files get uploaded**: the resume and cover letter, nothing else from your computer.
- **Not supported:** LinkedIn, Indeed and similar sites. Their terms forbid automated
  applications, and they need a login anyway.
- **What gets sent where:** your resume, profile and the job descriptions go to the Anthropic API.
  The answers on each form go to that employer, as with any application.
- If one of Claude's safety checks declines a request, the API retries it on a fallback model
  instead of failing (the `fallbacks: "default"` setting).
- Claude can still make mistakes on unusual forms. Read the summary and screenshot before you type
  `y`.

## Status of each job

`new` (passed the filters) → `scored` → `applying` → `applied`, or one of: `filtered_out`,
`needs_human` (you need to finish it), `declined` (you said no), `failed` (closed job or broken
page; see `show` for why), `skipped` (you marked it).

## How the code is organized

| File | What it does |
|---|---|
| `job_agent/sources.py` | Reads the job boards and applies the keyword and location filters |
| `job_agent/discover.py` | Claude web-searches for more companies with matching jobs |
| `job_agent/matching.py` | Claude scores jobs and writes cover letters |
| `job_agent/apply.py` | The applying agent: its instructions, its browser tools, the approval step |
| `job_agent/browser.py` | Drives Chromium: lists a page's fields, types, picks options, uploads |
| `job_agent/store.py` | SQLite database (`data/jobs.db`) of every job seen and its status |
| `job_agent/pipeline.py` | Runs search, score and apply in order |
| `job_agent/cli.py` | The `python -m job_agent ...` commands |

## Running the tests

```bash
pip install pytest
python -m pytest
```

The tests use a fake Claude API and a local practice application form, so they're free and don't
need the internet.
