"""Protocol/adapter signature-conformance tests.

The DI container wires concrete adapters where services declare Protocol
dependencies. Pyright doesn't catch arity drift across that seam because
the container's `provided.<method>.call(...)` accessor launders the type.
These tests pin each adapter method's signature to the Protocol's, so the
next drift fails the suite at import time of the assertions below.
"""

from __future__ import annotations

import inspect
from typing import Protocol

import pytest

from winter_cli.modules.workspace.internal.read_repo_repository import ReadRepoRepository
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.repo_repository import (
    IReadRepoRepository,
    IWriteRepoRepository,
)


def _protocol_methods(protocol: type) -> list[str]:
    base_attrs = set(dir(Protocol))
    return sorted(
        name
        for name in dir(protocol)
        if name not in base_attrs and not name.startswith("_") and callable(getattr(protocol, name))
    )


@pytest.mark.parametrize(
    ("protocol", "adapter"),
    [
        (IReadRepoRepository, ReadRepoRepository),
        (IWriteRepoRepository, WriteRepoRepository),
    ],
    ids=["read", "write"],
)
def test_adapter_signature_matches_protocol(protocol: type, adapter: type) -> None:
    mismatches: list[str] = []
    for name in _protocol_methods(protocol):
        proto_sig = inspect.signature(getattr(protocol, name))
        adapter_method = getattr(adapter, name, None)
        if adapter_method is None:
            mismatches.append(f"{name}: missing on {adapter.__name__}")
            continue
        adapter_sig = inspect.signature(adapter_method)
        if proto_sig != adapter_sig:
            mismatches.append(f"{name}: protocol {proto_sig} != adapter {adapter_sig}")
    assert not mismatches, "Protocol/adapter signature drift:\n  " + "\n  ".join(mismatches)
