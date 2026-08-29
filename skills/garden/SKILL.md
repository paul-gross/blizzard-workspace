---
description: Run one gardening pass over a declared axis — resolve its criteria from blizzard-context's garden registry, sweep the scope, record the measurement, and draft the issues and epic the findings earn
argument-hint: "<axis-id> [scope-slug ...]"
allowed-tools: Bash, Read, Grep, Glob, Agent
---

Run one gardening pass: resolve an axis from blizzard's gardening-axes registry, sweep the ground it names against the
criteria it points at, record the measurement, and turn what survives verification into a proposal and the work items
that proposal earns.

Parse `$ARGUMENTS` for the axis id and, optionally, the scope slugs to narrow to. No scope means the axis's whole
ground. An empty `$ARGUMENTS` means ask which axis, listing what the registry declares — never pick one.

## Big picture

The split this skill exists to hold: **the registry brings the facts, this skill brings the method.** What counts as a
weed on a given axis — what it evaluates, which ground it covers, which standards it judges by, what every run measures
— is a fact about blizzard and lives in `blizzard-context:/garden/`. How a pass is conducted, what a finding looks like,
and how findings become filed work is methodology and lives here. Never carry a criterion in this file; read it at run
time (`winter-canon:/facts-vs-methodology.md`).

A pass has no edit authority. It changes nothing it finds — every remedy travels as a filed issue somebody picks up
later. It also files nothing silently: the drafted proposal and issue set go to the operator for approval first.

## 1. Resolve the axis

Read `blizzard-context:/garden/index.md`, then the spoke for the named axis.

If the registry declares no such axis, **stop and say so.** That is a gap in the harness, and reporting it is the run's
entire output — improvising criteria makes the pass a taste engine and its findings unattributable
(`winter-canon:/gardening-axes.md`).

Take four things from the entry and state them back to the operator before sweeping: what it evaluates, the scope slugs
in range, where its criteria live, and what the run must measure.

## 2. Read the criteria before the code

Read every doc the entry's Criteria field points at, in full, first. A pass that reads code before rules finds what it
already believed; a pass that reads rules first finds what the project actually declared. This is also where the run's
rule-id vocabulary comes from — every finding cites the id it violates, and an uncitable observation is not a finding on
this axis.

## 3. Confirm the gates are green

Run the target's own mechanical gates for the scope before judging anything, and treat what they enforce as out of range
for the sweep — the axis's Criteria field owns that exclusion and why it holds.

Green gates also sharpen the run's claim: findings that survive a green tree are, by construction, the debt no tool
catches. A red gate is not a finding either — report it and stop, because a pass cannot tell drift from an unfinished
change on a tree that does not build.

## 4. Gut-check the scope before enumerating it

Ask, honestly, whether the scope can be inventoried within one agent's context, or whether recording what is here would
exhaust it.

When it is the second, **the run stops and enumerates nothing.** Its entire output is a halt report — not a finding,
which cites a rule this one cannot — naming the scope that overflowed and the honest count or estimate behind the call.
A truncated inventory mints a few hundred findings that read as the whole set, and every later run inherits the
confusion.

A run that would find that much has usually met a shift in grading — a standard newly authored or newly sharpened that
the existing code was never written against. Its response is upstream, not a thousand cleanups.

## 5. Sweep

Work mechanically first, then read. For each rule in range:

- **Turn its detection clause into a query where it is greppable.** Where the criteria doc follows the canon rule
  skeleton (`winter-canon:/rule-shape.md`) that clause is its `Detect` slot; where it does not, use whatever the doc
  gives you to recognize a violation by. Most such clauses name a literal — a symbol, a call shape, a CSS class, a
  phrase. Those become `grep`/`Glob` sweeps that enumerate candidates cheaply and completely, which is what makes
  coverage claimable rather than anecdotal.
- **Read every candidate before it becomes a finding.** A grep hit is a candidate; a finding is a hit whose source you
  opened and confirmed. Discard the ones that are conformant on inspection — and record that you checked, because "swept
  and clean" is a result worth reporting.
- **Name the in-tree conformant instance.** Where the repo already does the thing correctly somewhere, that site is the
  standard the divergent one is held to. A finding that can point at a working sibling is actionable; one that can only
  point at a rule is an opinion.

Fan out across `Agent` calls when the scope is wide, one agent per rule cluster or per scope slug, and merge their
candidate lists before the read-and-confirm step — verification stays here, so no finding enters the report on an
agent's say-so alone.

A finding whose rule belongs to a different axis is recorded and attributed to the axis that owns it, never folded into
the invoked one to keep a run's output whole.

## 6. Shape each finding

One finding is one theme, not one instance. Every finding states:

| Slot        | Content                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------ |
| Rule id     | The id it violates, from the criteria read in step 2                                             |
| Locus       | The scope slug, plus every confirmed site as `file:line`                                         |
| Claim       | What is divergent, in one sentence                                                               |
| Counterpart | The conformant in-tree instance it is held against, where one exists                             |
| Consequence | What the divergence costs — a cost nobody would pay to fix is a finding that should not be filed |

## 7. Record the measurement

Record what the axis declares, findings or none. A converged run's flat trendline is the success state and must be cheap
to produce — a pass that reports nothing still reports its numbers.

No durable store for those numbers is declared yet, so until one is, report them to the operator with the pass result
and say plainly that the run left no trend behind. A measurement that lives only in a transcript is the gap to close,
not a step to skip.

## 8. Propose

Choose one class per proposal, and say which:

| Class       | When                                                                                                    |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| `remediate` | Do the cleanup the findings describe — one proposal per theme per area, sized as work somebody picks up |
| `prevent`   | Fix the source so the inflow stops: the missing standard, the exemplar spreading the pattern            |
| `mechanize` | Move the check into a lint rule or gate so no later run pays a model to notice this class again         |
| `escalate`  | Hand it out of the fleet — past what gardening absorbs, and needing a person to plan it elsewhere       |

Reach for `prevent` first when a sweep returns many findings: filing cleanups while the thing producing them runs
untouched is motion, not progress. A `mechanize` proposal carries its case — which finding class it retires — and when a
check graduates it **moves house rather than being copied**: the axis stops judging what a gate now enforces.

## 9. File the work

A finding never becomes a work item on its own. An accepted `remediate` proposal does, and this is the shape it takes.

**One issue per finding.** Not per instance — a finding is already a theme, and per-instance filing confuses inventory
with work. Where two findings would produce the same edit to the same file, group them into one issue and say so; where
one finding holds two unrelated remedies, split it and say so. The finding count is the starting point, not a quota.

**Five or more issues earn an epic.** File them as children of a parent epic tagged `CR-YYYYMMDD` — the literal prefix
`CR-`, then the pass's date, so `CR-20260829` is the pass run on 29 August 2026. One prefix spans every axis on purpose:
the tag identifies a batch by the run that produced it, so every gardening batch sorts together no matter which axis
earned it, and the axis is already named in the epic's body. The tag is the epic's `epic_tag` and every child's title
prefix. Under five, file them flat: the epic earns its overhead only once there are real children to parent.

**Link the dependencies.** Where one issue must land before another — a split that a relocation should precede, a
restyle that a component split would conflict with — record it as a GitHub issue dependency, not as a sentence in the
body. An ordering only prose knows is an ordering the next agent picks up out of order.

`winter-github:/context/issue-format.md` owns the issue body format and `winter-github:/context/epics.md` owns the epic
mechanics — the `[EPIC]` / `[<TAG>]` prefixes, the `type:epic` label, the metadata keys, and the sub-issue link.
`winter-github:/context/gh-cli.md` owns the `gh` invocations both need, including the child's REST database id and the
`sub_issues` link that consumes it. `workspace:/context/github.md` owns the label set and which repo an issue belongs
to. Read them rather than reproducing what they say.

The one call no owner covers yet is the dependency edge, which takes the same REST database id:

```bash
# record that <blocked-N> cannot start until <blocker-N> lands
gh api -X POST repos/<owner>/<repo>/issues/<blocked-N>/dependencies/blocked_by -F issue_id=<blocker-rest-id>
```

Draft the full set — epic, every child, and the dependency edges — and present it before creating anything. The operator
approves the set once; then file it.

## What a pass never does

- Edit what it finds (Big picture).
- Judge by criteria it brought with it (step 1).
- Enumerate an overgrown scope (step 4).
- File per instance (step 9).
