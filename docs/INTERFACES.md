# Interfaces

The web app, CLI, and HTTP API share the same Python records and evaluation engine. The running service exposes its request schema at `GET /api/schema`. Source contracts live in [models.py](../src/cvals/models.py) and [api.py](../src/cvals/api.py); implementation details may evolve before a stable 1.0 API.

## Transport and access

The default base URL is `http://127.0.0.1:8765`. Responses are JSON except downloads, server-sent events, and successful deletes. Mutation bodies use JSON or the documented multipart upload.

If `CVALS_TOKEN` is configured, API requests require `Authorization: Bearer <token>`. The health endpoint and static app are public. Browser changes must come from the same origin. Trusted-host checks and a 5 MiB request limit also apply. There is no permissive cross-origin API.

Errors use a `detail` field. Domain errors generally return status 400 with a string; missing records return 404; request-model validation returns 422 with validation details. Authentication failure is 401 and oversized input is 413.

Dates use UTC ISO 8601 strings. Unknown costs, usage, durations, scores, and outcomes remain JSON `null`. Rates and scores use 0–1; durations use milliseconds; money uses USD.

## Task records

### Collection

```text
schema_version: 1
id: string                         generated when omitted
name: string
description: string                default ""
domain: coding | documents | data | workflow | mixed
version: string                    default "1.0.0"
created_at: UTC timestamp
scenarios: Scenario[]              1–1,000 items
```

A collection domain must match its scenarios unless it is `mixed`. Scenario IDs are unique within a collection. Changing content under an existing collection ID requires a different version. Experiments preserve their own snapshots.

### Scenario

```text
id, name
domain: coding | documents | data | workflow
description
instruction
inputs: object
expected: object
checks: Check[]                    1–100 items
reference_output: object | null
tags: string[]
provenance: object
```

The provider/exported agent view contains only `id`, `name`, `domain`, `instruction`, and `inputs`. Other fields remain in the trusted evaluation workspace.

A reference output supplies one accepted answer for validation and the reference fixture. `provenance.negative_controls` can contain outputs that the checks should reject. Validation uses an empty output as the default negative control. See [scenario authoring](scenarios.md).

### Check

```text
id, name
kind: equals | contains | numeric | json_schema | citation | forbidden | python_tests
path: string                       default ""; whole output
expected: any JSON value
weight: number                     > 0, default 1
required: boolean                  default true
tolerance: number                  >= 0, default 0
```

Paths use dotted object keys or nonnegative array indexes, such as `items.0.amount`. They are not executable expressions.

| Kind           | Evaluated behavior                                                                                                                                                    |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `equals`       | Canonical JSON equality.                                                                                                                                              |
| `contains`     | String containment, list membership, or object subset membership.                                                                                                     |
| `numeric`      | Finite numeric value within an absolute tolerance. Booleans are not numbers.                                                                                          |
| `json_schema`  | Restricted, offline JSON Schema 2020-12 validation. External references and regular-expression constraints are rejected.                                              |
| `citation`     | Known source identifiers, required sources, and optional verbatim quotations. Semantic entailment is not assessed.                                                    |
| `forbidden`    | Specified values or content are absent at a present output path.                                                                                                      |
| `python_tests` | Submitted file map checked in the configured Linux verifier. Preferred definitions use `entrypoint` and declarative `cases`; unittest source is a compatibility mode. |

A missing output path fails a check. Required checks determine pass/fail; when no checks are marked required, all checks must pass. The weighted check score is reported separately.

Validation returns:

```text
{valid, scenarios: [
  {id, valid, errors, checks, negative_controls, evidence_scope}
]}
```

Each negative-control record contains its index, rejection result, and checks or an evaluation error. This is one observed validation cycle, not a flakiness certificate.

## Systems and submissions

### System

```text
id, name
kind: reference | incomplete | http | replay
model, base_url, api_key_env
temperature: number | null         null omits the parameter
max_tokens: integer                1–32,768
input_cost_per_million: number | null
output_cost_per_million: number | null
pricing_version: string
timeout_seconds: number            1–120
token_limit_field: max_completion_tokens | max_tokens
collection_hash: string            replay only
submissions: object                replay only
provenance: object
```

`api_key_env` is an uppercase environment-variable name, never a key value. `GET /api/systems` adds `configured`, indicating whether a referenced credential is present. It is not an endpoint connectivity test.

- `reference` replays a scenario's reference output. The installed name is **Reference fixture · control**.
- `incomplete` submits an empty output. The installed name is **Empty submission · control**.
- `http` sends one chat-completions request and expects a JSON-object response.
- `replay` grades outputs supplied by an external harness and preserves their declared metadata.

HTTP base URLs cannot include embedded credentials, query parameters, or fragments. Both token prices must be explicit, including zero for a free local endpoint. A priced endpoint requires a positive budget. A remote endpoint additionally requires service opt-in, HTTPS, a public destination, and at least one nonzero token price.

### External submission import

`POST /api/submissions` accepts:

```json
{
  "name": "External agent evaluation",
  "collection_id": "collection-id",
  "collection_hash": "64-character-sha256-from-task-export",
  "system_name": "My existing harness",
  "provenance": { "harness_revision": "reviewed-version" },
  "submissions": {
    "scenario-id": {
      "output": { "answer": "recorded result" },
      "cost_usd": null,
      "input_tokens": null,
      "output_tokens": null,
      "duration_ms": null,
      "trace": [],
      "provenance": {}
    }
  }
}
```

The hash must match the stored collection. Unknown scenario IDs are rejected; missing submissions remain visible as unsuccessful/incomplete attempts. A submission may carry an `error` instead of an output. Imported cost and duration are supplied evidence, not independently measured provider activity.

Import creates a replay system and a queued experiment with one repetition. Repeating identical imported outputs is not independent execution evidence. See [external submissions](submissions.md).

## Experiments and trials

Creation accepts:

```text
{name, collection_id, system_ids, repetitions: 1, budget_usd: 0}
```

Limits are 20 distinct systems, 30 repetitions, 10,000 total scheduled trials, and a 10 MiB experiment snapshot. Total trials equal scenarios × systems × repetitions.

Experiment responses contain identifiers and names; collection version/hash; system IDs and frozen definitions; repetition and budget settings; timestamps; status; counts; and a report. Detail responses additionally include the frozen `snapshot`, `trials`, and `human_reviews`. Snapshots preserve collection content, system settings, protocol, harness/grader revisions, and the requested sandbox image.

Experiment status is `queued`, `running`, `completed`, `cancelled`, or `failed`. Completion means every trial was graded; it does not mean every trial passed. An experiment with infrastructure or other incomplete attempts becomes `failed` unless cancelled.

Trial records include:

```text
id, experiment_id, scenario_id, scenario_name, domain
system_id, system_name, repetition
status, score, passed
output, checks, error
duration_ms, cost_usd, input_tokens, output_tokens
started_at, finished_at, attempts, reserved_cost_usd
```

Terminal trial states are `completed`, `error`, `cancelled`, `interrupted`, and `budget_exceeded`. A graded incorrect answer has `status: completed` and `passed: false`. Experiment `completed_trials` counts finalized attempts, while report `completed_trials` counts valid graded results.

Each check result includes `id`, `name`, `kind`, `passed`, `score`, `expected`, `actual`, `message`, `required`, and `weight`. Additional provider and verifier evidence is retained on the trial.

## Reports

Reports contain:

- Total, completed, and incomplete trial counts.
- Per-system scheduled/completed/passed/failed/error counts, pass rate, average check score, interval bounds/method, cost completeness, known cost, cost per pass, and latency percentiles.
- Paired differences across shared tasks, paired-task counts, interval bounds/method, and conclusions.
- Domain/system summaries and interpretation warnings.
- Fixture-control and imported-submission labels.

Unknown totals remain null even when some cost is known; `known_cost_usd` records the known portion. Cost per pass is null when the numerator is incomplete or nothing passed. Workspace overview metrics exclude fixture controls.

The [results guide](results.md) explains denominators, assumptions, replay, and reruns. Exports use the same stored evidence.

## Human reviews

`POST /api/experiments/{id}/reviews` accepts:

```text
{trial_id, reviewer, dimension, score, rationale}
```

Only finalized trials can be reviewed. Scores lie between 0 and 1. A saved review adds `id`, `experiment_id`, `created_at`, `identity_assurance`, `evidence_hash`, `supersedes`, and `latest`.

A later record for the same trial/reviewer/dimension supersedes the current judgment while retaining history. Reviewer identity assurance is explicitly `self-reported label`. Human judgments never silently replace automated checks or aggregate pass rates.

## Recurring schedules

Creation accepts:

```text
{name, experiment_id, interval_minutes, enabled: true}
```

Intervals range from 5 to 525,600 minutes. There are at most 100 schedules per workspace. A schedule returns its ID, name, source experiment, interval, enabled state, next/last-run timestamps, last experiment ID, last error, and snapshot policy.

`PATCH /api/schedules/{id}` accepts `{enabled: boolean}`. A schedule reruns the original frozen configuration, with the original budget applied separately to each occurrence. There is no cumulative schedule budget. Recorded-submission replay systems are not eligible. A failed admission or uncertain prior billing disables the schedule and records the reason.

## HTTP routes

| Method      | Path                                         | Result                                                                  |
| ----------- | -------------------------------------------- | ----------------------------------------------------------------------- |
| GET         | `/api/health`                                | Service version and worker status.                                      |
| GET         | `/api/schema`                                | OpenAPI request schema.                                                 |
| GET         | `/api/overview`                              | Workspace counts, headline metrics, and recent experiments.             |
| GET, POST   | `/api/collections`                           | List or save a collection.                                              |
| GET, DELETE | `/api/collections/{id}`                      | Read or delete a library collection.                                    |
| GET         | `/api/collections/{id}/agent-view`           | Public task inputs and collection fingerprint.                          |
| POST        | `/api/collections/{id}/validate`             | Reference/control validation evidence.                                  |
| POST        | `/api/starters`                              | Install original starter collections and fixture systems idempotently.  |
| POST        | `/api/import`                                | Import collection JSON body or JSON/YAML multipart `file`.              |
| POST        | `/api/sources`                               | Preview multipart source `file`.                                        |
| POST        | `/api/repositories/inspect`                  | Read local repository metadata from `{path, limit}`.                    |
| POST        | `/api/repositories/mine`                     | Draft collection, candidates, and counts from `{path, limit}`.          |
| GET, POST   | `/api/systems`                               | List or save a system.                                                  |
| DELETE      | `/api/systems/{id}`                          | Delete a library system.                                                |
| POST        | `/api/submissions`                           | Import external outputs and queue grading.                              |
| GET, POST   | `/api/experiments`                           | List or create experiments.                                             |
| GET         | `/api/experiments/{id}`                      | Frozen configuration, trials, report, and reviews.                      |
| POST        | `/api/experiments/{id}/cancel`               | Request cancellation and retain attempts.                               |
| POST        | `/api/experiments/{id}/rerun`                | Queue a new run of the original snapshot.                               |
| GET         | `/api/experiments/{id}/export?format=FORMAT` | Download JSON, CSV, or HTML evidence/report.                            |
| GET         | `/api/experiments/{id}/events?after=0`       | Server-sent events with increasing IDs and a terminal `complete` event. |
| GET, POST   | `/api/experiments/{id}/reviews`              | Review history or append a judgment.                                    |
| GET, POST   | `/api/schedules`                             | List or create recurring evaluations.                                   |
| PATCH       | `/api/schedules/{id}`                        | Enable or pause a schedule.                                             |
| GET         | `/api/settings`                              | Storage and local execution capabilities.                               |

Source inspection and mining require an absolute local repository path and a limit from 1 to 100. Mining does not automatically save the returned collection; review it and use `POST /api/collections`. YAML aliases and anchors are not accepted.

## CLI companion

Run `uv run cvals --help` for current command options. Commands include:

```text
init --starters
doctor
serve
worker
collections list | import | export | validate | tasks
systems list | add-http
run
experiments list | show | cancel | rerun | export
import-results
inspect
mine
replay
reviews list | add
schedules list | create | pause | resume
backup
```

`run` and `experiments rerun` can execute synchronously or enqueue. `replay` regenerates a report without provider calls. The CLI operates directly on the configured local workspace; HTTP bearer authentication applies to API clients, not to an owner’s direct filesystem access.
