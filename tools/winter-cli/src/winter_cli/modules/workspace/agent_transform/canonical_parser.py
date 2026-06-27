"""Parser for the canonical agent frontmatter format.

``CanonicalAgentParser.parse`` accepts the full text of a canonical ``.md`` agent
file (YAML frontmatter delimited by ``---``, followed by the body), validates
required fields, and returns a ``CanonicalAgent``. Raises ``RepoError`` on
malformed or absent frontmatter and on missing required fields.
"""

from __future__ import annotations

import yaml

from winter_cli.modules.workspace.agent_transform.model_tiers import VENDOR_LABELS, ModelTier
from winter_cli.modules.workspace.agent_transform.models import CanonicalAgent
from winter_cli.modules.workspace.models import RepoError

# Vendor label names that may appear as override blocks in the frontmatter.
# Sourced from model_tiers.VENDOR_LABELS — the single vocabulary definition
# shared with MODEL_TIER_IDS keys and CodeAgentVendor.vendor_label.
_VENDOR_LABELS: frozenset[str] = VENDOR_LABELS

# Top-level fields in the canonical format (common + vendor blocks).
_KNOWN_TOP_LEVEL: frozenset[str] = frozenset({"name", "description", "model", "tools"}) | _VENDOR_LABELS


class CanonicalAgentParser:
    """Parses a canonical agent ``.md`` file into a ``CanonicalAgent``.

    Stateless: ``parse`` can be called any number of times.  Raise ``RepoError``
    rather than returning a sentinel so callers don't have to inspect a
    Maybe-type to detect failures.
    """

    def parse(self, text: str) -> CanonicalAgent:
        """Parse ``text`` (full canonical agent file content) and return a ``CanonicalAgent``.

        Raises ``RepoError`` when:
        - The file does not begin with a ``---`` frontmatter block.
        - The frontmatter block is not closed.
        - The frontmatter YAML is syntactically invalid.
        - Required fields (``name``, ``description``) are absent or empty.
        - ``model`` is present but not a recognised ``ModelTier`` value.
        """
        frontmatter_text, body = self._split_frontmatter(text)
        try:
            data = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as exc:
            raise RepoError(f"invalid YAML in canonical agent frontmatter: {exc}") from exc

        if not isinstance(data, dict):
            raise RepoError(
                "canonical agent frontmatter must be a YAML mapping, "
                f"got {type(data).__name__ if data is not None else 'null'}"
            )

        name = self._require_str(data, "name", context="canonical agent")
        description = self._require_str(data, "description", context=f"canonical agent {name!r}")

        model_tier = self._parse_model_tier(name, data.get("model", "sonnet"))
        tools = self._parse_tools(name, data.get("tools"))

        overrides: dict[str, dict] = {}
        for label in _VENDOR_LABELS:
            if label not in data:
                continue
            block = data[label]
            if not isinstance(block, dict):
                raise RepoError(
                    f"canonical agent {name!r}: override block {label!r} must be a YAML mapping, "
                    f"got {type(block).__name__ if block is not None else 'null'}"
                )
            overrides[label] = block

        return CanonicalAgent(
            name=name,
            description=description,
            model_tier=model_tier,
            tools=tools,
            body=body,
            overrides=overrides,
        )

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[str, str]:
        """Split ``text`` into ``(frontmatter_yaml, body)``.

        Expects ``text`` to begin with ``---`` (possibly followed by a newline)
        and to contain a closing ``---`` on its own line. Returns the YAML text
        between the delimiters and the body text after the closing delimiter.
        Raises ``RepoError`` when the structure is not found.
        """
        if not text.startswith("---"):
            raise RepoError("canonical agent file must begin with a YAML frontmatter block (---)")
        lines = text.split("\n")
        closing_idx: int | None = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                closing_idx = i
                break
        if closing_idx is None:
            raise RepoError("canonical agent frontmatter block is not closed (missing closing ---)")
        frontmatter = "\n".join(lines[1:closing_idx])
        # Drop the leading blank line between the closing --- and the body.
        body_lines = lines[closing_idx + 1 :]
        body = "\n".join(body_lines).lstrip("\n")
        return frontmatter, body

    @staticmethod
    def _require_str(data: dict, field: str, *, context: str) -> str:
        """Return ``data[field]`` as a non-empty string or raise ``RepoError``."""
        value = data.get(field)
        if not value:
            raise RepoError(f"{context} frontmatter missing required field: {field!r}")
        if not isinstance(value, str):
            raise RepoError(f"{context} field {field!r} must be a string, got {type(value).__name__}")
        return value

    @staticmethod
    def _parse_model_tier(name: str, model_raw: object) -> ModelTier:
        """Resolve ``model_raw`` to a ``ModelTier`` or raise ``RepoError``."""
        if model_raw is None:
            return ModelTier.sonnet
        if not isinstance(model_raw, str):
            raise RepoError(
                f"canonical agent {name!r}: 'model' must be a string tier alias, got {type(model_raw).__name__}"
            )
        try:
            return ModelTier(model_raw)
        except ValueError:
            valid = ", ".join(repr(t.value) for t in ModelTier)
            raise RepoError(
                f"canonical agent {name!r}: unknown model tier {model_raw!r}; valid values: {valid}"
            ) from None

    @staticmethod
    def _parse_tools(name: str, tools_raw: object) -> list[str] | str | None:
        """Parse ``tools_raw`` into a tool list, ``"*"``, or ``None``."""
        if tools_raw is None:
            return None
        if tools_raw == "*":
            return "*"
        if isinstance(tools_raw, list):
            for item in tools_raw:
                if not isinstance(item, str):
                    raise RepoError(
                        f"canonical agent {name!r}: every item in 'tools' must be a string, got {type(item).__name__}"
                    )
            return list(tools_raw)
        raise RepoError(
            f"canonical agent {name!r}: 'tools' must be a list of strings or '*', got {type(tools_raw).__name__}"
        )
