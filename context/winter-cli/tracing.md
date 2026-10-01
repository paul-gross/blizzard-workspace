# Tracing

Opt-in OpenTelemetry tracing for winter commands: each command emits one root span, nested under the caller's trace when
the caller passes one. Tracing is off unless an endpoint is set, and a process with tracing off does no tracing work and
loads no OpenTelemetry code. For the hub and the command surface, see [index.md](./index.md).

## Switching it on

Set `WINTER_OTEL_EXPORTER_OTLP_ENDPOINT` to the base URL of an OTLP/HTTP receiver. Winter posts `http/protobuf` to
`<endpoint>/v1/traces`. The supported topology is a collector on the same host.

| Variable                             | Meaning                                                                                                                                           |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WINTER_OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL of the OTLP/HTTP receiver, e.g. `http://localhost:4318`. Tracing is on when it is set to a non-blank value.                              |
| `WINTER_OTEL_EXPORTER_OTLP_HEADERS`  | Optional request headers in the standard `key1=value1,key2=value2` format.                                                                        |
| `OTEL_SDK_DISABLED`                  | `true` (any case) turns tracing off even with the endpoint set.                                                                                   |
| `TRACEPARENT`                        | The W3C trace context of the caller's span. When set, it is the parent of the command's root span and its sampled flag decides whether to record. |
| `OTEL_RESOURCE_ATTRIBUTES`           | Resource attributes added to every span, so a caller can tag spans (worker or step ids, for example).                                             |
| `OTEL_SERVICE_NAME`                  | The `service.name` of the spans.                                                                                                                  |

The `service.name` resolves in this order: `OTEL_SERVICE_NAME`, then a `service.name` entry in
`OTEL_RESOURCE_ATTRIBUTES`, then `winter`. A caller that spawns winter owns what its own `OTEL_SERVICE_NAME` does to
winter's spans.

Winter's export pipeline is configured by the variables above alone. The generic exporter variables
(`OTEL_EXPORTER_OTLP_*` and `OTEL_PYTHON_EXPORTER_OTLP_*`: endpoint, headers, timeout, compression, certificates,
credential providers) do not switch tracing on and do not reach winter's export request; they stay in the environment
unchanged for child processes. A new root span is always recorded, and a span under a `TRACEPARENT` follows the caller's
sampled flag.

## The span

| Property  | Value                                                                                                                  |
| --------- | ---------------------------------------------------------------------------------------------------------------------- |
| Name      | `winter` followed by the full command path, such as `winter provision`, `winter ws init`, or `winter service up`.      |
| Attribute | `winter.command`, equal to the span name. `winter.env` joins it when the command targets exactly one feature env.      |
| Parent    | The caller's span from `TRACEPARENT`; a new trace root when it is unset.                                               |
| Start     | The moment winter dispatches the command, or the launch instant in `WINTER_LAUNCH_TIME` when the launcher exports one. |
| Success   | A command that exits 0 leaves the status unset.                                                                        |
| Failure   | Any other exit sets error status and an `error.type` attribute holding the exception class name.                       |

## Launcher-gap coverage

The `winter` launcher shim exports `WINTER_LAUNCH_TIME` as its first statement, so the span starts when the shim does
and covers the `mise` and `uv` startup before Python runs, a gap of a few hundred milliseconds. Re-run
`./tools/winter-cli/install.sh` to enable it: the shim in `~/.local/bin` is a copy, and the installer replaces it only
when the source shim's version is newer. Until then the span starts at command dispatch. The same holds on a bash
without `$EPOCHREALTIME` (bash older than 5), where the shim exports nothing.

## The target env: `winter.env`

`winter.env` holds the name of the one feature env a command targets. A command sets it from its own arguments alone:
the env part of each target argument (the part before any `/`) resolves to exactly one feature env.

| Target arguments                                            | `winter.env`                                |
| ----------------------------------------------------------- | ------------------------------------------- |
| One env name or `<env>/<repo>`, such as `ws status alpha`   | `alpha`                                     |
| A name that does not exist yet, such as `ws init new-env`   | `new-env`: a literal name counts as written |
| A glob that matches exactly one existing env, such as `al*` | the matched env                             |
| Two or more envs, or a glob that matches several            | not set                                     |
| No target arguments                                         | not set                                     |
| The `workspace` scope, such as `service up workspace`       | not set: the scope is never an env          |
| `lint` names that are repos, such as `lint winter`          | not set: only discovered envs count         |

The trailing arguments that are not targets stay out of the resolution: the FEATURE_BRANCH of `ws connect`, the REF of
`ws reset`, and the BASE of `ws restack`. A command that targets one env and also `workspace` resolves to the env.

Only the invoked command's own arguments count. Work a command does for other envs while it runs, such as the service
auto-start inside `provision alpha beta`, never sets or changes `winter.env`. Resolving a glob reads the workspace's
existing envs, and only when tracing is on; the lookup never changes what the command prints or how it exits.

## Propagation to child processes

Every child process winter starts through its subprocess runner carries the active span in `TRACEPARENT`, so a nested
`winter` call or an instrumented tool runs under the command's span. The value replaces any `TRACEPARENT` the caller
passed, and `TRACESTATE` accompanies it: the child gets the span's own trace state, or none, never the caller's. Work on
a runner thread that holds no span of its own, such as the service status matrix, carries the command span. Winter's own
fresh containers, such as the dashboard's `ws init`, use the same tracer and the same command span.

With tracing off, a child's environment is exactly what winter passed before tracing existed: the caller's `TRACEPARENT`
and `TRACESTATE`, if any, reach it unchanged.

Services that winter launches start with no `TRACEPARENT` and no `TRACESTATE`, tracing on or off. That covers
`winter service up` and `winter service restart`, and the auto-start of required services inside `winter provision`. A
long-running service therefore never attaches spans to a step that has ended. The provider-side rule is in
[contracts/service-orchestrator.md](./contracts/service-orchestrator.md#trace-context-traceparent).

## Export and exit cost

Winter collects the span in memory and sends it once, in a single request, as the command exits. The exporter timeout is
the cap: **100 ms**. A slow, hanging, or refused endpoint, and an unreachable one given as an IP or a locally resolvable
name (`localhost`, an `/etc/hosts` entry), never holds a command past that cap and never changes its exit code or its
stderr.

A collector on the same host answers well inside the cap. A remote endpoint delivers only while connection setup plus
one request fit inside it, roughly two round trips for plain HTTP and three to four with TLS. Beyond that the spans are
dropped without a trace. A remote hostname whose DNS lookup is slow can hold a command past the cap, because the
exporter timeout does not cover name resolution.

## Content

Spans carry command paths and names only: the command path, the target env's name, and the `error.type` of a failure.
Their resource carries `service.name`, the attributes the caller supplied in `OTEL_RESOURCE_ATTRIBUTES`, and the SDK's
own `telemetry.sdk.*` attributes and per-process `service.instance.id`. The raw argv, environment variable values,
`config.local.toml` values, and command output stay out of every span. Exception messages are never recorded, because
they can embed git's stderr.

## Diagnostics

Failed exports are silent by default; [root-flags.md](./root-flags.md) owns how `--verbose` and `WINTER_LOG_LEVEL`
surface them.
