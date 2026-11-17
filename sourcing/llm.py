"""Claude API helper. Returns None everywhere until ANTHROPIC_API_KEY is set, so the
plain-code parts of the pipeline (scrapers, dedupe, budget, logos) run without it."""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)


class LLM:
    def __init__(self, model: str):
        from anthropic import Anthropic
        self.client, self.model = Anthropic(), model
        self.searches = 0  # web searches used, reported as cost units in the Run Log

    def ask_json(self, system: str, prompt: str, web_searches: int = 0) -> dict | None:
        tools = ([{"type": "web_search_20250305", "name": "web_search", "max_uses": web_searches}]
                 if web_searches else [])
        try:
            resp = self.client.messages.create(model=self.model, max_tokens=2000, system=system,
                                               messages=[{"role": "user", "content": prompt}], tools=tools)
        except Exception as e:
            log.warning("Claude API call failed: %s", e)
            return None
        usage = getattr(getattr(resp, "usage", None), "server_tool_use", None)
        self.searches += getattr(usage, "web_search_requests", 0) or 0
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            log.warning("Claude returned non-JSON: %s", text[:200])
            return None


def get_llm(model: str) -> LLM | None:
    return LLM(model) if os.environ.get("ANTHROPIC_API_KEY") else None
