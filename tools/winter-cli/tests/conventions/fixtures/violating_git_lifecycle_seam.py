"""Fixture: an extension-consuming call site reading get_standalone_repos() on the
wrong seam — deliberately outside GIT_LIFECYCLE_ALLOWED_FILES."""

from __future__ import annotations


class SomeExtensionConsumingService:
    def __init__(self, repo_factory) -> None:
        self._repo_factory = repo_factory

    def do_extension_thing(self):
        return self._repo_factory.get_standalone_repos()
