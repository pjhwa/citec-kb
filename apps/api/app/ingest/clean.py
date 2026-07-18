"""Text cleaners for source noise (Jira markup, etc.)."""

from __future__ import annotations

import re

_JIRA_COLOR = re.compile(r"\{color[^}]*\}|\{color\}", re.I)
_JIRA_PANEL = re.compile(r"\{panel[^}]*\}|\{panel\}", re.I)
_JIRA_CODE = re.compile(r"\{code[^}]*\}|\{code\}", re.I)
_JIRA_NOFORMAT = re.compile(r"\{noformat\}", re.I)
_MULTI_NL = re.compile(r"\n{4,}")
_NBSP = re.compile(r"\u00a0|&nbsp;")


def clean_jira_markup(text: str) -> str:
    t = text or ""
    t = _NBSP.sub(" ", t)
    t = _JIRA_COLOR.sub("", t)
    t = _JIRA_PANEL.sub("", t)
    t = _JIRA_CODE.sub("```", t)
    t = _JIRA_NOFORMAT.sub("", t)
    # collapse wiki-style {-}{{-}} noise somewhat
    t = re.sub(r"\{-+\}\{\{-\}\}*", "", t)
    t = _MULTI_NL.sub("\n\n\n", t)
    return t.strip()


def clean_md(text: str) -> str:
    return clean_jira_markup(text)
