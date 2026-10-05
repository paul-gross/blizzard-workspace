from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeInitReporter
from winter_cli.config.models import AdoptExtensions, ExtensionLoad, WorkspaceConfig
from winter_cli.modules.workspace.extension_agentsmd_service import ExtensionAgentsMdService
from winter_cli.modules.workspace.extension_manifest import (
    AGENTS_WINTER_FILENAME,
    CLAUDEMD_WINTER_FILENAME,
    EXT_MANIFEST,
    ExtensionManifestLoader,
)
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
    )


def _manifest_loader(config_files: dict | None = None) -> ExtensionManifestLoader:
    return ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files or {}))


def _seed_extension_with_index(fs: FakeFilesystem, name: str, *, manifest: bool = True) -> StandaloneRepository:
    """Plant an extension repo with an index.md so the service treats it as eligible.

    A real extension ships a `winter-ext.toml`; pass `manifest=False` for a plain
    repo that carries only context. Pair the manifest-bearing form with
    `_manifests_for` so the loader can read it.
    """
    ext_path = WORKSPACE_ROOT / name
    fs.directories.add(ext_path)
    fs.files[ext_path / "index.md"] = "# index\n"
    if manifest:
        fs.files[ext_path / EXT_MANIFEST] = ""
    return StandaloneRepository(name=name, path=ext_path)


def _manifests_for(*repos: StandaloneRepository) -> dict:
    """Empty manifest contents for each repo, keyed for `_manifest_loader`."""
    return {repo.path / EXT_MANIFEST: {} for repo in repos}


def _seed_standalone_with_manifest(
    fs: FakeFilesystem,
    name: str,
    **manifest_data: object,
) -> tuple[StandaloneRepository, dict]:
    """Plant a standalone extension carrying a winter-ext.toml with the given fields."""
    repo = _seed_extension_with_index(fs, name)
    manifest_path = repo.path / EXT_MANIFEST
    fs.files[manifest_path] = ""
    return repo, {manifest_path: dict(manifest_data)}


def _seed_project_extension(
    fs: FakeFilesystem,
    name: str,
    *,
    entry_point: str = "index.md",
    description: str | None = None,
    extra_manifest: dict | None = None,
) -> tuple[StandaloneRepository, dict]:
    """Plant a project-repo extension under `projects/<name>/` with a winter-ext.toml."""
    ext_path = WORKSPACE_ROOT / "projects" / name
    fs.directories.add(ext_path)
    fs.files[ext_path / entry_point] = "# entry\n"
    manifest_path = ext_path / EXT_MANIFEST
    manifest_data: dict = dict(extra_manifest or {})
    if description is not None:
        manifest_data["description"] = description
    fs.files[manifest_path] = ""
    return StandaloneRepository(name=name, path=ext_path), {manifest_path: manifest_data}


def test_finalize_agentsmd_writes_agents_winter_for_eligible_repos(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    ext_a = _seed_extension_with_index(fs, "ext-a")
    ext_b = _seed_extension_with_index(fs, "ext-b")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(ext_a, ext_b))
    )

    ok = svc.finalize_agentsmd([ext_a, ext_b], init_reporter)
    assert ok is True

    agents_path = WORKSPACE_ROOT / AGENTS_WINTER_FILENAME
    content = fs.files[agents_path]
    assert content.startswith("# Path notation resolution\n")
    assert "- **ext-a**: @ext-a/index.md" in content
    assert "**ext-b**" in content


def test_finalize_agentsmd_does_not_write_claude_winter_shim(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """CLAUDE.winter.md is never written — winter only generates AGENTS.winter.md."""
    fs = FakeFilesystem()
    ext_a = _seed_extension_with_index(fs, "ext-a")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(ext_a))
    )

    ok = svc.finalize_agentsmd([ext_a], init_reporter)
    assert ok is True

    shim_path = WORKSPACE_ROOT / CLAUDEMD_WINTER_FILENAME
    assert shim_path not in fs.files


def test_finalize_agentsmd_removes_stale_claude_winter_md(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A pre-existing CLAUDE.winter.md is removed as a migration-cleanup step."""
    fs = FakeFilesystem()
    shim_path = WORKSPACE_ROOT / CLAUDEMD_WINTER_FILENAME
    fs.files[shim_path] = "@AGENTS.winter.md\n"
    ext_a = _seed_extension_with_index(fs, "ext-a")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(ext_a))
    )

    ok = svc.finalize_agentsmd([ext_a], init_reporter)
    assert ok is True

    assert shim_path not in fs.files
    actions = [a[2] for a in init_reporter.actions]
    assert "claude_winter_stale_removed" in actions


def test_finalize_agentsmd_removes_stale_claude_winter_md_when_adoption_disabled(
    init_reporter: FakeInitReporter,
) -> None:
    """Migration cleanup runs even when extension adoption is disabled."""
    config = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.none,
    )
    fs = FakeFilesystem()
    shim_path = WORKSPACE_ROOT / CLAUDEMD_WINTER_FILENAME
    fs.files[shim_path] = "@AGENTS.winter.md\n"
    svc = ExtensionAgentsMdService(config=config, fs=fs, manifest_loader=_manifest_loader())

    ok = svc.finalize_agentsmd([], init_reporter)
    assert ok is True

    assert shim_path not in fs.files
    actions = [a[2] for a in init_reporter.actions]
    assert "claude_winter_stale_removed" in actions


def test_finalize_agentsmd_idempotent_no_reporter_action_on_second_run(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A second run with identical input writes nothing and emits no reporter actions."""
    fs = FakeFilesystem()
    ext_a = _seed_extension_with_index(fs, "ext-a")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(ext_a))
    )

    # First run: writes AGENTS.winter.md.
    reporter_first = FakeInitReporter()
    ok = svc.finalize_agentsmd([ext_a], reporter_first)
    assert ok is True
    assert len(reporter_first.actions) > 0

    # Second run with same input: no diff, so no reporter actions.
    reporter_second = FakeInitReporter()
    ok = svc.finalize_agentsmd([ext_a], reporter_second)
    assert ok is True
    assert reporter_second.actions == []


def test_finalize_agentsmd_delete_when_empty_removes_agents_winter(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """When no eligible extensions remain, AGENTS.winter.md is deleted."""
    fs = FakeFilesystem()
    agents_path = WORKSPACE_ROOT / AGENTS_WINTER_FILENAME
    # Pre-seed the body file from a previous run.
    fs.files[agents_path] = "- **old-ext** at ./old-ext/ — ...\n"

    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader())
    ok = svc.finalize_agentsmd([], init_reporter)
    assert ok is True

    assert agents_path not in fs.files

    actions = [a[2] for a in init_reporter.actions]
    assert "agents_winter_removed" in actions


def test_finalize_agentsmd_skips_repos_without_index_md(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Repos without an entry point at the root are excluded entirely."""
    fs = FakeFilesystem()
    ext_path = WORKSPACE_ROOT / "no-index"
    fs.directories.add(ext_path)
    no_index_repo = StandaloneRepository(name="no-index", path=ext_path)
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader())

    ok = svc.finalize_agentsmd([no_index_repo], init_reporter)
    assert ok is True

    agents_path = WORKSPACE_ROOT / AGENTS_WINTER_FILENAME
    # No eligible extensions and the file didn't exist — nothing was written.
    assert agents_path not in fs.files


# ── entry-point discovery fallback ────────────────────────────────────────


def test_finalize_agentsmd_falls_back_to_agents_md_entry_point(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A standalone with no index.md but an AGENTS.md at its root is still eligible."""
    fs = FakeFilesystem()
    ext_path = WORKSPACE_ROOT / "ext-agents"
    fs.directories.add(ext_path)
    fs.files[ext_path / "AGENTS.md"] = "# agents\n"
    fs.files[ext_path / EXT_MANIFEST] = ""
    repo = StandaloneRepository(name="ext-agents", path=ext_path)
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(repo))
    )

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "@ext-agents/AGENTS.md" in content


def test_finalize_agentsmd_falls_back_to_context_index_entry_point(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Neither index.md nor AGENTS.md present — falls back to context/index.md."""
    fs = FakeFilesystem()
    ext_path = WORKSPACE_ROOT / "ext-context"
    fs.directories.add(ext_path)
    fs.files[ext_path / "context" / "index.md"] = "# context index\n"
    fs.files[ext_path / EXT_MANIFEST] = ""
    repo = StandaloneRepository(name="ext-context", path=ext_path)
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(repo))
    )

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "@ext-context/context/index.md" in content


# ── project-repo routing rows ──────────────────────────────────────────────


def test_finalize_agentsmd_renders_project_repo_as_routing_row(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A project-repo extension (rooted under projects/) renders a no-`@` routing row."""
    fs = FakeFilesystem()
    repo, config_files = _seed_project_extension(fs, "winter-docs", description="Public docs site generator.")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **winter-docs**: `<env>/winter-docs/index.md` — Public docs site generator." in content
    assert "@" not in content


def test_finalize_agentsmd_project_repo_row_falls_back_to_entry_point_without_description(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """When `description` is absent, the row renders with the entry-point path alone."""
    fs = FakeFilesystem()
    repo, config_files = _seed_project_extension(fs, "winter-docs")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **winter-docs**: `<env>/winter-docs/index.md`" in content
    assert "@" not in content


def test_finalize_agentsmd_mixes_standalone_and_project_repo_extensions(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A workspace with both kinds renders an eager @-import plus a routing row."""
    fs = FakeFilesystem()
    standalone = _seed_extension_with_index(fs, "ext-a")
    project_repo, config_files = _seed_project_extension(fs, "winter-docs", description="Docs generator.")
    svc = ExtensionAgentsMdService(
        config=workspace_config,
        fs=fs,
        manifest_loader=_manifest_loader({**config_files, **_manifests_for(standalone)}),
    )

    ok = svc.finalize_agentsmd([standalone, project_repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "@ext-a/index.md" in content
    assert "<env>/winter-docs/" in content


def test_finalize_agentsmd_no_env_binding_note_when_no_project_rows(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """The `<env>`-binding note is only emitted when at least one routing row renders."""
    fs = FakeFilesystem()
    ext_a = _seed_extension_with_index(fs, "ext-a")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader(_manifests_for(ext_a))
    )

    ok = svc.finalize_agentsmd([ext_a], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "binds to the feature-env directory" not in content


def test_finalize_agentsmd_env_binding_note_present_with_project_rows(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A workspace-root reader has no bound `<env>` — the generated file states the
    binding rule and the `projects/<name>/` fallback once, ahead of the routing rows."""
    fs = FakeFilesystem()
    project_repo, config_files = _seed_project_extension(fs, "winter-docs", description="Docs generator.")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([project_repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "binds to the feature-env directory" in content
    assert "projects/<name>/" in content
    assert "Docs generator." in content


# ── declared load mode ─────────────────────────────────────────────────────


def test_finalize_agentsmd_lazy_standalone_renders_link_instead_of_import(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """`load = "lazy"` swaps a standalone's eager `@`-import for a markdown link."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-lazy", load="lazy")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **[ext-lazy](ext-lazy/index.md)**" in content
    assert "@" not in content


def test_finalize_agentsmd_lazy_standalone_appends_description(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A lazy row carries the manifest description — the trigger for opening the link."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(
        fs, "ext-lazy", load="lazy", description="Conventions for agent-facing markdown."
    )
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **[ext-lazy](ext-lazy/index.md)** — Conventions for agent-facing markdown." in content


def test_finalize_agentsmd_lazy_standalone_without_description_renders_link_alone(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """`description` is optional — the row degrades to the bare link."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-lazy", load="lazy")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **[ext-lazy](ext-lazy/index.md)**\n" in content
    assert "—" not in content


def test_finalize_agentsmd_explicit_eager_standalone_still_imports(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Declaring the standalone default explicitly changes nothing."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-eager", load="eager")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **ext-eager**: @ext-eager/index.md" in content


def test_finalize_agentsmd_standalone_defaults_to_eager_without_declared_load(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A manifest that declares no `load` keeps the pre-existing eager import."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a", description="Some extension.")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **ext-a**: @ext-a/index.md\n" in content
    # An eager import needs no description — the imported file speaks for itself.
    assert "Some extension." not in content


def test_finalize_agentsmd_project_repo_declaring_lazy_renders_path_template(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Declaring the project-repo default explicitly changes nothing."""
    fs = FakeFilesystem()
    repo, config_files = _seed_project_extension(
        fs, "winter-docs", description="Docs generator.", extra_manifest={"load": "lazy"}
    )
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **winter-docs**: `<env>/winter-docs/index.md` — Docs generator." in content
    assert init_reporter.errors == []


def test_finalize_agentsmd_project_repo_declaring_eager_is_reported_and_downgraded(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """An eager import of a project repo would inject the stale master copy — refuse it."""
    fs = FakeFilesystem()
    repo, config_files = _seed_project_extension(fs, "winter-docs", extra_manifest={"load": "eager"})
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **winter-docs**: `<env>/winter-docs/index.md`" in content
    assert "@" not in content
    assert any(name == "winter-docs" and "load" in message for name, message in init_reporter.errors)


def test_finalize_agentsmd_malformed_manifest_reports_and_falls_back_to_kind_default(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """An unreadable `load` value is reported, and the extension still renders."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-broken", load="deferred")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **ext-broken**: @ext-broken/index.md" in content
    assert any(name == "ext-broken" for name, _ in init_reporter.errors)


def test_finalize_agentsmd_groups_eager_lazy_and_project_rows_in_order(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """All three shapes coexist: eager imports, then lazy links, then the `<env>` rows."""
    fs = FakeFilesystem()
    eager = _seed_extension_with_index(fs, "ext-eager")
    lazy, lazy_files = _seed_standalone_with_manifest(fs, "ext-lazy", load="lazy", description="Lazy one.")
    project, project_files = _seed_project_extension(fs, "winter-docs", description="Docs generator.")
    svc = ExtensionAgentsMdService(
        config=workspace_config,
        fs=fs,
        manifest_loader=_manifest_loader({**lazy_files, **project_files, **_manifests_for(eager)}),
    )

    ok = svc.finalize_agentsmd([project, lazy, eager], init_reporter)
    assert ok is True

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    eager_at = content.index("@ext-eager/index.md")
    lazy_at = content.index("[ext-lazy](")
    note_at = content.index("binds to the feature-env directory")
    project_at = content.index("<env>/winter-docs/index.md")
    assert eager_at < lazy_at < note_at < project_at


# ── adopt_extensions gating of manifest-less repos ─────────────────────────


def _config_with(mode: AdoptExtensions) -> WorkspaceConfig:
    return WorkspaceConfig(workspace_root=WORKSPACE_ROOT, service_prefix="t", main_branch="main", adopt_extensions=mode)


def _seed_agents_md_repo(fs: FakeFilesystem, name: str) -> StandaloneRepository:
    """Plant a manifest-less repo that carries only a root AGENTS.md — a cloned-as-data repo."""
    repo_path = WORKSPACE_ROOT / "mirrors" / name
    fs.directories.add(repo_path)
    fs.files[repo_path / "AGENTS.md"] = "# contributor instructions\n"
    return StandaloneRepository(name=name, path=repo_path)


def test_finalize_agentsmd_winter_mode_renders_no_bullet_for_manifest_less_repo(
    init_reporter: FakeInitReporter,
) -> None:
    """Context delivery honors `adopt_extensions = "winter"` the way skill projection does."""
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    assert WORKSPACE_ROOT / AGENTS_WINTER_FILENAME not in fs.files


def test_finalize_agentsmd_all_mode_renders_manifest_less_repo(init_reporter: FakeInitReporter) -> None:
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    svc = ExtensionAgentsMdService(config=_config_with(AdoptExtensions.all), fs=fs, manifest_loader=_manifest_loader())

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **blizzard-workspace**: @mirrors/blizzard-workspace/AGENTS.md" in content


# ── repo-entry `load` ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (ExtensionLoad.eager, "- **blizzard-workspace**: @mirrors/blizzard-workspace/AGENTS.md"),
        (ExtensionLoad.lazy, "- **[blizzard-workspace](mirrors/blizzard-workspace/AGENTS.md)**"),
    ],
)
def test_finalize_agentsmd_explicit_load_opts_a_manifest_less_repo_in_under_winter(
    load: ExtensionLoad, expected: str, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    repo.load = load
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    assert expected in fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]


def test_finalize_agentsmd_load_none_renders_no_bullet_for_a_manifest_bearing_repo(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a")
    repo.load = ExtensionLoad.none
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    assert WORKSPACE_ROOT / AGENTS_WINTER_FILENAME not in fs.files


def test_finalize_agentsmd_load_none_leaves_other_repos_rendering(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    quiet, quiet_files = _seed_standalone_with_manifest(fs, "ext-quiet")
    quiet.load = ExtensionLoad.none
    loud, loud_files = _seed_standalone_with_manifest(fs, "ext-loud")
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader({**quiet_files, **loud_files})
    )

    svc.finalize_agentsmd([quiet, loud], init_reporter)

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "ext-loud" in content
    assert "ext-quiet" not in content


def test_finalize_agentsmd_repo_entry_load_wins_over_the_manifest_load(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a", load="lazy")
    repo.load = ExtensionLoad.eager
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    svc.finalize_agentsmd([repo], init_reporter)

    assert "- **ext-a**: @ext-a/index.md" in fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]


def test_finalize_agentsmd_repo_entry_eager_on_a_project_repo_is_reported_and_downgraded(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    repo, config_files = _seed_project_extension(fs, "winter-docs")
    repo.load = ExtensionLoad.eager
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **winter-docs**: `<env>/winter-docs/index.md`" in content
    assert "@" not in content
    assert any(name == "winter-docs" and "repo entry" in message for name, message in init_reporter.errors)


# ── repo-entry `entry` ─────────────────────────────────────────────────────


def test_finalize_agentsmd_entry_takes_the_first_existing_candidate(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Earlier candidates that are missing are skipped; later ones that also exist are ignored."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a")
    fs.files[repo.path / "docs" / "agents.md"] = "# agents\n"
    fs.files[repo.path / "docs" / "more.md"] = "# more\n"
    repo.entry = ("missing.md", "docs/agents.md", "docs/more.md")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    svc.finalize_agentsmd([repo], init_reporter)

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **ext-a**: @ext-a/docs/agents.md\n" in content
    assert "more.md" not in content


def test_finalize_agentsmd_entry_replaces_the_default_candidates_entirely(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """An existing `index.md` is not a fallback once `entry` is declared — and the miss is reported."""
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a")  # ships index.md
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    ok = svc.finalize_agentsmd([repo], init_reporter)

    assert ok is True
    assert WORKSPACE_ROOT / AGENTS_WINTER_FILENAME not in fs.files


def test_finalize_agentsmd_entry_matching_none_warns_naming_the_paths_tried(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    fs = FakeFilesystem()
    repo, config_files = _seed_standalone_with_manifest(fs, "ext-a")
    repo.entry = ("docs/agents.md", "AGENT.md")
    svc = ExtensionAgentsMdService(config=workspace_config, fs=fs, manifest_loader=_manifest_loader(config_files))

    svc.finalize_agentsmd([repo], init_reporter)

    warnings = [a for a in init_reporter.actions if a[2] == "extension_warning"]
    assert len(warnings) == 1
    assert warnings[0][0] == "ext-a"
    assert "docs/agents.md" in warnings[0][3]
    assert "AGENT.md" in warnings[0][3]
    assert init_reporter.errors == []


def test_finalize_agentsmd_default_candidates_miss_is_silent(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Only a workspace-declared `entry` warns; a repo with no default entry point just renders nothing."""
    fs = FakeFilesystem()
    repo_path = WORKSPACE_ROOT / "bare"
    fs.directories.add(repo_path)
    fs.files[repo_path / EXT_MANIFEST] = ""
    repo = StandaloneRepository(name="bare", path=repo_path)
    svc = ExtensionAgentsMdService(
        config=workspace_config, fs=fs, manifest_loader=_manifest_loader({repo_path / EXT_MANIFEST: {}})
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert [a for a in init_reporter.actions if a[2] == "extension_warning"] == []


def test_finalize_agentsmd_entry_opts_a_manifest_less_repo_in_alongside_load(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    fs.files[repo.path / "docs" / "agents.md"] = "# agents\n"
    repo.load = ExtensionLoad.lazy
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert (
        "- **[blizzard-workspace](mirrors/blizzard-workspace/docs/agents.md)**"
        in fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    )


def test_finalize_agentsmd_entry_alone_opts_a_manifest_less_standalone_in_at_the_eager_default(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    fs.files[repo.path / "docs" / "agents.md"] = "# agents\n"
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert (
        "- **blizzard-workspace**: @mirrors/blizzard-workspace/docs/agents.md\n"
        in fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    )


def test_finalize_agentsmd_entry_alone_matching_none_warns_for_a_manifest_less_standalone(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_agents_md_repo(fs, "blizzard-workspace")
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert WORKSPACE_ROOT / AGENTS_WINTER_FILENAME not in fs.files
    warnings = [a for a in init_reporter.actions if a[2] == "extension_warning"]
    assert len(warnings) == 1
    assert "docs/agents.md" in warnings[0][3]


def _seed_manifest_less_project_repo(fs: FakeFilesystem, name: str) -> StandaloneRepository:
    """Plant a manifest-less project repo under `projects/<name>/` carrying only an AGENTS.md."""
    repo_path = WORKSPACE_ROOT / "projects" / name
    fs.directories.add(repo_path)
    fs.files[repo_path / "AGENTS.md"] = "# contributor instructions\n"
    return StandaloneRepository(name=name, path=repo_path)


def test_finalize_agentsmd_load_lazy_renders_a_routing_row_for_a_manifest_less_project_repo(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_manifest_less_project_repo(fs, "app")
    repo.load = ExtensionLoad.lazy
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    content = fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]
    assert "- **app**: `<env>/app/AGENTS.md`" in content
    assert "@" not in content.replace("@AGENTS", "")
    assert init_reporter.errors == []


def test_finalize_agentsmd_entry_alone_renders_a_lazy_routing_row_for_a_manifest_less_project_repo(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_manifest_less_project_repo(fs, "app")
    fs.files[repo.path / "docs" / "agents.md"] = "# agents\n"
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert "- **app**: `<env>/app/docs/agents.md`" in fs.files[WORKSPACE_ROOT / AGENTS_WINTER_FILENAME]


def test_finalize_agentsmd_entry_matching_none_warns_for_a_manifest_less_project_repo(
    init_reporter: FakeInitReporter,
) -> None:
    fs = FakeFilesystem()
    repo = _seed_manifest_less_project_repo(fs, "app")
    repo.load = ExtensionLoad.lazy
    repo.entry = ("docs/agents.md",)
    svc = ExtensionAgentsMdService(
        config=_config_with(AdoptExtensions.winter), fs=fs, manifest_loader=_manifest_loader()
    )

    svc.finalize_agentsmd([repo], init_reporter)

    assert WORKSPACE_ROOT / AGENTS_WINTER_FILENAME not in fs.files
    warnings = [a for a in init_reporter.actions if a[2] == "extension_warning"]
    assert len(warnings) == 1
    assert "docs/agents.md" in warnings[0][3]
