# `{main}` ref tokens — typing a repo's own main branch

Repos configure `main_branch` independently: a workspace default in `.winter/config.toml` plus a per-repo override on each `[[project_repository]]` / `[[standalone_repository]]` entry. A ref-accepting command (`winter ws merge SOURCE_REF`, `winter ws checkout FEATURE_BRANCH`) applies the same literal string to every repo it touches, so a mixed-name env (`main` in one repo, `master` in another) has no single spelling that resolves everywhere — until now.

## The alias set

`{main}`, `{master}`, and `{default}` are interchangeable aliases: each expands to *that repo's own* configured `main_branch` (the per-repo override when set, else the workspace default), resolved independently per repo before any git operation runs. Pick whichever alias reads best — they're identical in behavior.

The token works in any position within the ref: both `origin/{main}` (typical) and a bare `{main}` are valid. A ref with no token is passed to git unchanged, byte-for-byte.

```bash
winter ws merge origin/{main} gamma      # each repo in gamma integrates its own main branch
winter ws checkout alpha {main}          # each repo in alpha resets to its own main branch
```

An unrecognized token (e.g. `origin/{trunk}`) refuses before any git operation, naming the token and this alias set.

**Braces, not angle brackets.** `<main>` is a shell redirection operator and fails to parse in zsh and bash before winter ever receives it (`zsh: parse error near '>'`). `{main}` passes through unquoted, and braces already read as git ref syntax (`@{u}`, `@{2.days.ago}`).

No network — resolution reads configuration only.

## Where it's accepted

- [`winter ws merge SOURCE_REF`](./merge.md)
- [`winter ws checkout ENV FEATURE_BRANCH`](./checkout.md)
