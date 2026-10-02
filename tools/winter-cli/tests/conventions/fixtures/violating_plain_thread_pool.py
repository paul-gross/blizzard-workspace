"""Fixture: pools built from the stdlib `ThreadPoolExecutor`, bypassing the context-carrying helper."""

from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor


class SomeFanOutService:
    def run_bare(self, tasks):
        with ThreadPoolExecutor(max_workers=4) as pool:
            return [pool.submit(task) for task in tasks]

    def run_qualified(self, tasks):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return [pool.submit(task) for task in tasks]
