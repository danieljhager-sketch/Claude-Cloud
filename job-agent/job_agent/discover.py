"""Claude searches the web for companies that are hiring for your kind of role.

Search is limited to Greenhouse, Lever and Ashby job boards, because those have
free public APIs the search step can then read in full.
"""

from __future__ import annotations

import re

import anthropic

from .claude_client import applicant_profile, base_params, check_not_refused, search_preferences
from .config import Config
from .sources import board_from_url

BOARD_DOMAINS = ["greenhouse.io", "lever.co", "ashbyhq.com"]
MAX_CONTINUATIONS = 5

PROMPT = """Find companies that are hiring right now for roles that fit this applicant.

Use web search to look for open job postings. Search is limited to Greenhouse, Lever and \
Ashby job boards. Run varied searches: different job titles that suit the resume, nearby \
seniority levels, the preferred locations, and remote. Skip postings that are clearly a poor \
fit for the resume.

When you're done, list every relevant posting URL you found, one per line.

<profile>
{profile}
{preferences}
</profile>"""

URL_PATTERN = re.compile(r"https?://[^\s)\]>\"']+")


def boards_in_message(message) -> set[tuple[str, str]]:
    urls: list[str] = []
    for block in message.content:
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            urls.extend(result.url for result in block.content if getattr(result, "url", None))
        elif block.type == "text":
            urls.extend(URL_PATTERN.findall(block.text))
            for citation in getattr(block, "citations", None) or []:
                if getattr(citation, "url", None):
                    urls.append(citation.url)
    return {board for board in map(board_from_url, urls) if board}


def discover_boards(client: anthropic.Anthropic, config: Config, resume: dict) -> set[tuple[str, str]]:
    prompt = PROMPT.format(
        profile=applicant_profile(config), preferences=search_preferences(config)
    )
    messages: list[dict] = [
        {"role": "user", "content": [resume, {"type": "text", "text": prompt}]}
    ]
    tools = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": config.search.max_web_searches,
            "allowed_domains": BOARD_DOMAINS,
        }
    ]
    found: set[tuple[str, str]] = set()
    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.beta.messages.create(
            **base_params(config),
            max_tokens=16000,
            output_config={"effort": "medium"},
            tools=tools,
            messages=messages,
        )
        check_not_refused(response)
        found |= boards_in_message(response)
        if response.stop_reason != "pause_turn":
            break
        # The search ran long and paused. Send the turn back as-is to resume it.
        messages.append({"role": "assistant", "content": response.content})
    return found
