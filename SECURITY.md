# Security and trust boundaries

Everval AI is designed for a single local workspace. The default service listens on loopback; an SSH tunnel is the supported remote-access path. A workspace token grants full workspace access, including private task definitions and recorded evidence. It is not a multi-user permission system.

## Material with different trust levels

Solver inputs contain only the scenario instruction and declared inputs. Expected answers, checks, reference outputs, and evaluator provenance remain outside the provider request. Full collection and experiment exports contain private grading material; share agent-view exports when distributing tasks to a solver.

Source imports and repository history are untrusted. Importers parse bounded data and never run repository hooks, dependency installers, builds, or tests on the host. The history compiler accepts a restricted set of literal Python assertions and reports rejected candidates. Extraction does not establish that a task is valid.

## Executing code

Python candidate execution uses a fresh non-root Docker container with no network, no host or Docker-socket mounts, a read-only root filesystem, restricted capabilities, and resource/time/output limits. Expected values for declarative Python cases remain on the host. The optional arbitrary unittest interface has weaker grading integrity because candidate and grader share a process. Read the [sandbox guide](docs/sandbox.md) before choosing either format.

Containers share their Linux VM kernel. This release does not claim resistance to every container escape or provide a hostile multi-tenant execution service. Docker access itself is privileged; run a dedicated local daemon and keep it separate from unrelated workloads.

## Providers and stored evidence

External provider access requires explicit configuration and a positive per-run budget with supplied token prices. Provider keys are read from named environment variables. External destinations require HTTPS and public addresses; literal loopback endpoints support local inference. Proxy forwarding and redirects are disabled. An uncertain billed request stops additional spending instead of being retried automatically.

An explicitly configured external provider receives task inputs. Local artifact storage does not imply local inference. Private outputs, user-supplied traces, and full exports can contain sensitive material; the service cannot infer every secret embedded in task content.

## Reporting an issue

Contact the repository maintainer through your existing private project channel with the affected revision, a minimal reproduction, and expected versus observed behavior. Do not include live credentials, private task data, or exploit details in a public issue. Reports should distinguish local code execution already authorized by the user from a boundary bypass.
