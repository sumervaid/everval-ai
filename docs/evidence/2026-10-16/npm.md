# npm distribution verification — 2026-10-16

This record covers the npm distribution and public release of
[Everval AI 0.3.1](https://www.npmjs.com/package/everval-ai/v/0.3.1).
The registry records publication at 2026-10-16 23:19:32 UTC. These checks are
separate from model evaluation.

| Check | Result |
| --- | --- |
| Launcher tests | 18 passed, including actual terminal input, resize, one interrupt per Ctrl+C, directed termination, and application exit status. |
| Distribution tests | 55 passed, including exact file boundaries, wheel RECORD integrity, source/metadata matching, private-payload rejection, and public/private access controls. |
| Static checks | Ruff lint/format, JavaScript syntax, documentation links, and Git whitespace passed. |
| Browser build | TypeScript and production build passed; current browser assets and dependency notices are bundled in the wheel. |
| npm archive | Exactly nine regular files; no JavaScript runtime dependencies, lifecycle hooks, development tests, workspaces, or credentials. |
| Isolated installation | Exact tarball installed into a fresh prefix using npm offline mode with scripts disabled. |
| Public registry | Anonymous metadata and tarball requests succeeded; latest resolves to 0.3.1. SHA-1, SHA-256, SHA-512 integrity, and byte-for-byte comparison matched the accepted archive. |
| Registry installation | Pinned 0.3.1 installed from npm into a fresh prefix with a fresh cache and scripts disabled. All nine installed files matched the public tarball. Both command aliases, launcher help, and application help passed. Application help reused the previously verified content-addressed Python runtime. |
| Disconnected setup | Application plus 36 locked dependencies installed from a local wheelhouse with hashes enforced, no index access, no source builds, and no Python downloads. Dependency checks and repeated setup passed. |
| Installed application | Launcher help/version, application help, TUI/serve command help, and rankings JSON completed outside the source checkout. |
| Installed demo | Baseline 3/3, deliberate regression 0/3, restored candidate 3/3; saved regrading, comparisons, and audit chain completed. Zero model calls and zero provider spend. |
| Installed terminal | Workbench loaded at 120×40, Ctrl+L opened Rankings, resizing to 80×24 repainted, and Ctrl+Q exited cleanly. Worker disabled; no remaining TUI processes. |
| Installed HTTP service | Loopback health and browser page returned successfully; both referenced production assets were served. A directed SIGTERM completed application shutdown. |

The accepted archive is `everval-ai-0.3.1.tgz`, 445,821 bytes, with SHA-256:

```text
2fe6af3460a5b687c9b4a343b205c372c27d278fa9ee126a08174cf8311bbe69
```

The bundled application wheel has SHA-256:

```text
c89522e461f8ff67da9048602158542023dfaea3a26245afc90783914dabf707
```

The runtime dependency lock has SHA-256:

```text
51b5b83972f9df6245033fb8b256f9d88591de31a94c6d807241a5e8fba4ad63
```

Validation ran on macOS arm64 with Node 26.0.0, npm 11.12.1, Python 3.12.14,
and uv 0.11.23. Linux and other declared Node/Python versions were not exercised
in this installation. The [npm guide](../../npm.md) records the Node process
replacement API and its version-specific failure behavior.

Review found that spawning the application and forwarding terminal interrupts
could deliver Ctrl+C twice. The final launcher replaces Node with Python for
normal commands; the terminal regression and installed-service shutdown checks
passed after that change. Setup uses separate noninteractive child process
groups so each signal is delivered once.

An initial demo harness selected its active workspace as the demo destination;
the application rejected it. The corrected harness used a separate demo
workspace. The final package passed that corrected installation procedure.

The first terminal smoke harness expected mixed-case branding while the actual masthead was uppercase. Its assertion was corrected; the final navigation, resize, and exit checks passed.

These are packaging and installation controls, not model rankings or deployment
certification. No paid provider calls, new repository repair attempts, Docker
runs, hosted CI jobs, or Linux service deployments were performed. Raw logs,
package artifacts, and installation-control workspaces remain on the iMac.

npm initially accepted the upload while processing it; the package became
publicly downloadable afterward. The first registry installation attempt
returned “No matching version”; a fresh-cache retry succeeded. Anonymous
download and installation records were retained alongside the accepted
archive on the iMac. Publication verification made no paid model calls.

Follow [npm installation and release verification](../../npm.md) to reproduce
the packaging path, and [on-premises deployment](../../on-premises.md) for
target-host commissioning.
