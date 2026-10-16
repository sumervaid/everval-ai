# Security and trust boundaries

Everval AI is designed for one local workspace. The default service listens on loopback; an SSH tunnel is the supported remote-access path. A workspace token grants full workspace access, including private task definitions and recorded evidence.

Optional [team mode](docs/team-review.md) adds individual identities and API/browser roles. Terminal and filesystem access remain trusted administrative surfaces. These controls do not provide isolation between separate customer tenants.

## Reporting a vulnerability

Use the existing private project channel through which you received repository access to contact the repository owner. If you have no direct contact, ask the person who provided access to connect you before sharing sensitive details.

Include the affected version or commit, a minimal synthetic reproduction, the boundary you expected to hold, and the observed impact. Reports should distinguish code execution already authorized by the user from a boundary bypass. Do not include live credentials, private task data, or exploit details in an ordinary issue, even while the repository is private.

For a reproducible non-security bug, use the [bug report form](https://github.com/sumervaid/everval-continuous-benchmarking/issues/new?template=bug-report.yml). This document describes the reporting route and deployment boundaries; it does not promise a response time or a supported-version window.

## Material with different trust levels

The public task envelope contains the scenario ID, name, domain, instruction, and declared inputs. Built-in tool execution exposes only the allowed public files. Expected answers, checks, reference outputs, and evaluator provenance remain outside the provider request. Full collection and experiment exports contain private grading material; use agent-view exports when distributing tasks to a solver. Public task inputs can themselves contain sensitive data, so inspect them before sending them to an external system.

Source imports and repository history are untrusted. Importers parse bounded data and never run repository hooks, dependency installers, builds, or tests on the host. The history compiler accepts a restricted set of literal Python assertions and reports rejected candidates. Extraction does not establish that a task is valid.

## Executing code

Built-in Python and JavaScript/TypeScript candidate execution uses fresh non-root Docker containers with no network, no host or Docker-socket mounts, a read-only root filesystem, restricted capabilities, and resource/time/output limits. Expected values for declarative Python cases remain on the host. Arbitrary unittest and prepared repository-test profiles have weaker grading integrity when candidate code can interact with its test process. Read the [sandbox guide](docs/sandbox.md) and [repository runtime guide](docs/javascript-typescript.md) before choosing a format.

Containers share their Linux VM kernel. This release does not claim resistance to every container escape or provide a hostile multi-tenant execution service. Docker access itself is privileged; run a dedicated local daemon and keep it separate from unrelated workloads.

## Providers and stored evidence

External provider access requires explicit configuration and a positive per-run budget with supplied token prices. Provider keys are read from named environment variables. External destinations require HTTPS and public addresses; literal loopback endpoints support local inference. Proxy forwarding and redirects are disabled. An uncertain billed request stops additional spending instead of being retried automatically.

An explicitly configured external provider receives task inputs. Local artifact storage does not imply local inference. Private outputs, user-supplied traces, and full exports can contain sensitive material; the service cannot infer every secret embedded in task content.
