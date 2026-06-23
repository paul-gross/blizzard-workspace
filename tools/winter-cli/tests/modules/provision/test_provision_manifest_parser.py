from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeFilesystem
from winter_cli.config.workspace import (
    CONFIG_FILE,
    WINTER_DIR,
    WorkspaceConfigService,
    parse_provision,
)
from winter_cli.core.config_file import ConfigError
from winter_cli.modules.provision.manifest import (
    PROVISION_SUBTARGETS,
    ProvisionHandler,
    ProvisionManifestParser,
    ProvisionScope,
)

WORKSPACE_ROOT = Path("/ws/demo")
SOURCE = "project"


class _StubLocator:
    def __init__(self, root: Path) -> None:
        self._root = root

    def find_workspace_root(self) -> Path:
        return self._root


class _DictConfigFileReader:
    def __init__(self, contents: dict[Path, dict]) -> None:
        self._contents = contents

    def load(self, path: Path) -> dict:
        if path not in self._contents:
            raise FileNotFoundError(path)
        return self._contents[path]


def _config_service(configs: dict[Path, dict]) -> WorkspaceConfigService:
    config_path = WORKSPACE_ROOT / WINTER_DIR / CONFIG_FILE
    fs = FakeFilesystem(files={config_path: ""})
    return WorkspaceConfigService(
        workspace_locator=_StubLocator(WORKSPACE_ROOT),
        fs=fs,
        config_file_reader=_DictConfigFileReader(configs),
    )


# ── ProvisionManifestParser unit tests ───────────────────────────────────────


def test_parse_returns_empty_for_none() -> None:
    parser = ProvisionManifestParser()
    assert parser.parse(None, SOURCE) == []


def test_parse_returns_empty_for_empty_dict() -> None:
    parser = ProvisionManifestParser()
    assert parser.parse({}, SOURCE) == []


def test_parse_valid_workspace_scope() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "resource": [
            {
                "scope": "workspace",
                "apply": "scripts/create-db.sh",
                "destroy": "scripts/drop-db.sh",
                "required_services": ["workspace/postgres"],
            }
        ]
    }
    handlers = parser.parse(raw, SOURCE)
    assert len(handlers) == 1
    h = handlers[0]
    assert h.subtarget == "resource"
    assert h.scope == ProvisionScope.workspace
    assert h.apply == "scripts/create-db.sh"
    assert h.destroy == "scripts/drop-db.sh"
    assert h.reset is None
    assert h.required_services == ("workspace/postgres",)
    assert h.source == SOURCE


def test_parse_valid_feature_environment_scope() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "data": [
            {
                "scope": "feature-environment",
                "apply": "scripts/seed.sh",
                "reset": "scripts/reseed.sh",
                "required_services": ["workspace/postgres"],
            }
        ]
    }
    handlers = parser.parse(raw, SOURCE)
    assert len(handlers) == 1
    h = handlers[0]
    assert h.subtarget == "data"
    assert h.scope == ProvisionScope.feature_environment
    assert h.apply == "scripts/seed.sh"
    assert h.reset == "scripts/reseed.sh"
    assert h.destroy is None
    assert h.required_services == ("workspace/postgres",)


def test_parse_valid_feature_worktree_scope() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "dependency": [
            {
                "scope": "feature-worktree",
                "apply": "scripts/install.sh",
            }
        ]
    }
    handlers = parser.parse(raw, SOURCE)
    assert len(handlers) == 1
    h = handlers[0]
    assert h.subtarget == "dependency"
    assert h.scope == ProvisionScope.feature_worktree
    assert h.apply == "scripts/install.sh"
    assert h.destroy is None
    assert h.reset is None
    assert h.required_services == ()


def test_parse_all_three_subtargets() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "dependency": [{"scope": "feature-worktree", "apply": "scripts/install.sh"}],
        "resource": [{"scope": "workspace", "apply": "scripts/create.sh"}],
        "data": [{"scope": "feature-environment", "apply": "scripts/seed.sh"}],
    }
    handlers = parser.parse(raw, SOURCE)
    assert len(handlers) == 3
    subtargets = [h.subtarget for h in handlers]
    assert "dependency" in subtargets
    assert "resource" in subtargets
    assert "data" in subtargets


def test_parse_required_services_parsed_as_tuple_on_resource() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "resource": [
            {
                "scope": "workspace",
                "apply": "scripts/create.sh",
                "required_services": ["workspace/postgres", "workspace/redis"],
            }
        ]
    }
    handlers = parser.parse(raw, SOURCE)
    assert handlers[0].required_services == ("workspace/postgres", "workspace/redis")
    assert isinstance(handlers[0].required_services, tuple)


def test_parse_required_services_parsed_as_tuple_on_data() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "data": [
            {
                "scope": "feature-environment",
                "apply": "scripts/seed.sh",
                "required_services": ["workspace/postgres"],
            }
        ]
    }
    handlers = parser.parse(raw, SOURCE)
    assert handlers[0].required_services == ("workspace/postgres",)
    assert isinstance(handlers[0].required_services, tuple)


def test_parse_unknown_top_level_key_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {"deploy": [{"scope": "workspace", "apply": "scripts/deploy.sh"}]}
    with pytest.raises(ConfigError, match="Unknown provision sub-target 'deploy'"):
        parser.parse(raw, SOURCE)


def test_parse_unknown_entry_key_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "dependency": [
            {
                "scope": "feature-worktree",
                "apply": "scripts/install.sh",
                "unknown_key": "bad",
            }
        ]
    }
    with pytest.raises(ConfigError, match="Unknown key"):
        parser.parse(raw, SOURCE)


def test_parse_missing_apply_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {"dependency": [{"scope": "feature-worktree"}]}
    with pytest.raises(ConfigError, match="missing required field 'apply'"):
        parser.parse(raw, SOURCE)


def test_parse_empty_apply_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {"dependency": [{"scope": "feature-worktree", "apply": ""}]}
    with pytest.raises(ConfigError, match="missing required field 'apply'"):
        parser.parse(raw, SOURCE)


def test_parse_bad_scope_value_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {"dependency": [{"scope": "global", "apply": "scripts/install.sh"}]}
    with pytest.raises(ConfigError, match="Invalid scope 'global'"):
        parser.parse(raw, SOURCE)


def test_parse_bad_scope_error_lists_valid_values() -> None:
    parser = ProvisionManifestParser()
    raw = {"dependency": [{"scope": "bad", "apply": "scripts/install.sh"}]}
    with pytest.raises(ConfigError, match="'workspace'"):
        parser.parse(raw, SOURCE)


def test_parse_missing_scope_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {"dependency": [{"apply": "scripts/install.sh"}]}
    with pytest.raises(ConfigError, match="missing required field 'scope'"):
        parser.parse(raw, SOURCE)


def test_parse_required_services_on_dependency_rejected() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "dependency": [
            {
                "scope": "feature-worktree",
                "apply": "scripts/install.sh",
                "required_services": ["workspace/postgres"],
            }
        ]
    }
    with pytest.raises(ConfigError, match="'required_services' is not allowed on provision.dependency"):
        parser.parse(raw, SOURCE)


def test_parse_required_services_must_be_list_of_strings() -> None:
    parser = ProvisionManifestParser()
    raw = {
        "resource": [
            {
                "scope": "workspace",
                "apply": "scripts/create.sh",
                "required_services": "workspace/postgres",  # string, not list
            }
        ]
    }
    with pytest.raises(ConfigError, match="must be a list of strings"):
        parser.parse(raw, SOURCE)


# ── Deferred-parse / workspace config wiring tests ───────────────────────────


def test_malformed_provision_does_not_raise_at_config_load() -> None:
    """A bad scope in [provision] must NOT raise during WorkspaceConfigService.load()."""
    config_path = WORKSPACE_ROOT / WINTER_DIR / CONFIG_FILE
    svc = _config_service(
        {
            config_path: {
                "provision": {
                    "dependency": [
                        {
                            "scope": "totally-invalid-scope",
                            "apply": "scripts/install.sh",
                        }
                    ]
                }
            }
        }
    )
    # Must not raise — deferred parse
    config = svc.load()
    assert isinstance(config.provision_raw, dict)
    assert "dependency" in config.provision_raw


def test_malformed_provision_raises_when_parse_provision_called() -> None:
    """parse_provision() runs the strict parser and raises ConfigError for a bad scope."""
    config_path = WORKSPACE_ROOT / WINTER_DIR / CONFIG_FILE
    svc = _config_service(
        {
            config_path: {
                "provision": {
                    "dependency": [
                        {
                            "scope": "totally-invalid-scope",
                            "apply": "scripts/install.sh",
                        }
                    ]
                }
            }
        }
    )
    config = svc.load()
    with pytest.raises(ConfigError, match="Invalid scope"):
        parse_provision(config, source="project")


def test_parse_provision_returns_handlers_for_valid_config() -> None:
    config_path = WORKSPACE_ROOT / WINTER_DIR / CONFIG_FILE
    svc = _config_service(
        {
            config_path: {
                "provision": {
                    "dependency": [
                        {
                            "scope": "feature-worktree",
                            "apply": "scripts/install.sh",
                        }
                    ]
                }
            }
        }
    )
    config = svc.load()
    handlers = parse_provision(config, source="project")
    assert len(handlers) == 1
    assert handlers[0].subtarget == "dependency"
    assert handlers[0].scope == ProvisionScope.feature_worktree
    assert handlers[0].source == "project"


def test_parse_provision_returns_empty_when_no_provision_key() -> None:
    config_path = WORKSPACE_ROOT / WINTER_DIR / CONFIG_FILE
    svc = _config_service({config_path: {"main_branch": "main"}})
    config = svc.load()
    assert parse_provision(config, source="project") == []
