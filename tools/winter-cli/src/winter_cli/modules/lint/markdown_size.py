"""Effective-size measurement for agent-facing markdown.

Raw byte length overstates what a markdown file actually costs an agent's
context.  The formatter pads table cells with runs of spaces and draws
separator rows out of long dash runs; neither is token-bearing, yet both can
dominate the byte count of a wide table.  Measuring *effective* bytes
normalizes them away first, so two documents compare by the content an agent
reads rather than by how the formatter laid them out.

Two normalizations, applied in order to the decoded text:

* Every run of whitespace — spaces, tabs, newlines, blank lines — collapses to
  a single byte.  349 spaces of table padding cost the same as one space.
* Every run of four or more of the same non-alphanumeric character collapses to
  three, the shortest run markdown still reads as a rule: ``|-------|``
  measures as ``|---|``.

The result is still a byte count, not a token count — a tokenizer-independent
approximation that is cheap to compute and stable across files.
"""

from __future__ import annotations

import re

# Any run of whitespace, including newlines and blank lines.
_WHITESPACE_RUN = re.compile(r"\s+")

# A run of four or more of the same character.  Applied after whitespace
# collapse, so it never sees a whitespace run; alphanumeric runs are kept
# verbatim by the callback below.
_CHAR_RUN = re.compile(r"(.)\1{3,}")

# What a collapsed fill run measures as — three characters, markdown's shortest
# valid rule (`---`, `===`, `___`).
_FILL_RUN_LENGTH = 3


def compact(text: str) -> str:
    """Return *text* with whitespace runs and fill-character runs collapsed.

    The result is a measurement surrogate, not a rendering: line structure is
    deliberately discarded, since a newline and a space cost the same byte.
    """
    return _CHAR_RUN.sub(_collapse_fill_run, _WHITESPACE_RUN.sub(" ", text))


def effective_bytes(text: str) -> int:
    """Return the UTF-8 byte length of *text* after :func:`compact`."""
    return len(compact(text).encode("utf-8"))


def _collapse_fill_run(match: re.Match[str]) -> str:
    """Collapse a repeated-character run unless it is alphanumeric content."""
    char = match.group(1)
    if char.isalnum():
        return match.group(0)
    return char * _FILL_RUN_LENGTH
