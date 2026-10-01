"""Convention test — every command positional declares whether it names feature-env targets.

A positional that names feature envs carries one `EnvTargetDeclaration` as its click parameter
callback: that is how a command's own arguments report `winter.env` on its trace span, and
the only way (handlers and services never report it). A positional that does not name envs
(a repo URL, a branch, an extension name) is declared non-env in `NON_ENV_POSITIONALS`
below. A positional in neither place fails this test, so a new env-target positional cannot
silently leave the span without its env.
"""

from __future__ import annotations

import click

from winter_cli.cli import _cli_group
from winter_cli.modules.workspace.env_target import EnvTargetDeclaration

# Command path -> the positionals of that command that do not name feature envs.
NON_ENV_POSITIONALS: dict[str, frozenset[str]] = {
    "ext new": frozenset({"name"}),
    "ext verify": frozenset({"extensions"}),
    "repo add": frozenset({"url"}),
    "repo remove": frozenset({"target"}),
    "space": frozenset({"kind"}),
    "ws checkout": frozenset({"feature_branch"}),
    "ws merge": frozenset({"source_ref"}),
    "ws update": frozenset({"repos"}),
}


def _positionals(root: click.Command) -> dict[str, list[click.Argument]]:
    """Every leaf command's positionals, keyed by its space-joined command path."""
    found: dict[str, list[click.Argument]] = {}

    def walk(command: click.Command, path: list[str], ctx: click.Context) -> None:
        if isinstance(command, click.Group):
            for name in command.list_commands(ctx):
                child = command.get_command(ctx, name)
                assert child is not None, f"{' '.join([*path, name])} is listed but does not resolve"
                walk(child, [*path, name], ctx)
            return
        arguments = [param for param in command.params if isinstance(param, click.Argument)]
        if arguments:
            found[" ".join(path)] = arguments

    walk(root, [], click.Context(root))
    return found


def undeclared_positionals(root: click.Command, non_env: dict[str, frozenset[str]]) -> list[str]:
    """`<command path>: <positional>` for each positional that neither carries the env-target declaration nor is non-env."""
    violations: list[str] = []
    for path, arguments in _positionals(root).items():
        for argument in arguments:
            if isinstance(argument.callback, EnvTargetDeclaration):
                continue
            if argument.name in non_env.get(path, frozenset()):
                continue
            violations.append(f"{path}: {argument.name}")
    return violations


def test_every_command_positional_is_declared_env_target_or_non_env() -> None:
    violations = undeclared_positionals(_cli_group, NON_ENV_POSITIONALS)

    assert not violations, (
        "Each command positional must carry `callback=EnvTargetDeclaration(...)` when it names feature envs, "
        "or be listed in NON_ENV_POSITIONALS when it does not:\n  " + "\n  ".join(violations)
    )


def test_non_env_declarations_name_real_positionals_that_carry_no_env_declaration() -> None:
    positionals = _positionals(_cli_group)
    problems: list[str] = []
    for path, names in NON_ENV_POSITIONALS.items():
        actual = {argument.name: argument for argument in positionals.get(path, [])}
        for name in sorted(names):
            if name not in actual:
                problems.append(f"{path}: {name} is declared non-env but is not a positional of that command")
            elif isinstance(actual[name].callback, EnvTargetDeclaration):
                problems.append(f"{path}: {name} is declared non-env but carries the env-target declaration")

    assert not problems, "\n".join(problems)


# ── the check itself ─────────────────────────────────────────────────────────


def _tree(*arguments: click.Argument) -> click.Group:
    @click.group("root")
    def root() -> None:
        pass

    command = click.Command("act", params=list(arguments))
    root.add_command(command)
    return root


def test_the_check_flags_an_undeclared_positional() -> None:
    root = _tree(click.Argument(["patterns"], nargs=-1))

    assert undeclared_positionals(root, {}) == ["act: patterns"]


def test_the_check_accepts_a_positional_with_the_declaration() -> None:
    root = _tree(click.Argument(["patterns"], nargs=-1, callback=EnvTargetDeclaration()))

    assert undeclared_positionals(root, {}) == []


def test_the_check_accepts_a_positional_declared_non_env() -> None:
    root = _tree(click.Argument(["branch"]))

    assert undeclared_positionals(root, {"act": frozenset({"branch"})}) == []


def test_the_check_flags_only_the_undeclared_one_of_two_positionals() -> None:
    root = _tree(
        click.Argument(["env"], callback=EnvTargetDeclaration()),
        click.Argument(["branch"]),
    )

    assert undeclared_positionals(root, {}) == ["act: branch"]
