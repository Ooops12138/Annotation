"""Reject direct navigation primitives in generated component fragments."""

import re
from html import unescape
from html.parser import HTMLParser


NAVIGATION_SCRIPT = re.compile(r"\b(?:location|navigation|href|httpEquiv)\b|\b(?:window|self|top|parent)\s*\.\s*open\s*\(", re.I)


class _NavigationParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"meta", "base"} or (tag in {"a", "area"} and "href" in attributes):
            raise ValueError("generated component must not navigate its iframe")
        for key, value in attrs:
            if key.startswith("on") and NAVIGATION_SCRIPT.search(value or ""):
                raise ValueError("generated component event handler must not navigate")

    handle_startendtag = handle_starttag


def validate_component_navigation(html: str, javascript: str) -> None:
    _NavigationParser(convert_charrefs=True).feed(html)
    if NAVIGATION_SCRIPT.search(unescape(javascript)) or re.search(r"<\s*(?:meta|base)\b", javascript, re.I):
        raise ValueError("generated component JavaScript must not navigate")
