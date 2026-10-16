# Rankings and on-premises verification — 2026-10-16

These checks cover the new workspace Rankings view and on-premises deployment support on the maintained iMac. This is feature verification for the unreleased changes, separate from the [0.3.1 acceptance record](../2026-10-15/README.md) and the [public-repository pilot](../../benchmarks/public-repositories-2026-10-15/results.md).

| Check | Result and scope |
| --- | --- |
| Full ordinary Python run | 1,073 passed; 46 optional integration cases skipped; one existing Starlette TestClient deprecation warning. |
| Final affected Python checks | 132 passed after the last edits, covering rankings, TUI/API/CLI boundaries, internal providers, service preflight and health. These overlap the full run. |
| Browser tests | 29 passed, including nine Rankings cases and reviewer access isolation. |
| Static checks | Ruff lint/format and strict mypy passed; TypeScript and the production browser build passed. |
| Live local browser | Real saved fixture experiments rendered at 1440×1100 and 390×844; metric toggles, cell selection and evidence panel worked, with no page errors or horizontal page overflow. Desktop and mobile captures were inspected. |
| Terminal | Matrix and drilldown verified at 160×48 and 80×24, including shared ties, missing cells, controls/replays, incomplete evidence and modal-safe shortcuts. Synthetic preview data remains labeled and separate from measured results. |
| Packaging | Wheel built offline from the existing build cache; archive inspection confirmed rankings modules, terminal styles, bundled browser assets and third-party notices. |
| Deployment preflight | 28 portable tests passed for initialized storage, ownership/permissions, immutable release layout, personal authentication, administrator access, policy and template behavior. |
| Documentation | Local links and Git whitespace checks passed; the deployment guide received an independent implementation review. |

The [published browser image](../../assets/rankings.png) shows declared installation controls from an isolated local workspace. Those cells are unranked; the image makes no model-quality claim.

Review corrected a ranking edge case in which incomplete newer verifier-image evidence could leave older results ranked. Earlier evidence remains visible, but its rank is withheld and the newer blocking run is identified. Other cases cover exact configuration/cohort separation, scheduled denominators, unknown accounting, metric eligibility, ties and role boundaries.

The internal HTTPS policy was exercised with mocked transports and DNS responses across single-turn, tools and external-agent harnesses. Checks cover exact origins, approved private ranges, unsafe/mixed DNS answers, pinned destinations, preserved Host/TLS names, verified CA configuration and explicit pricing. No live internal inference endpoint was commissioned.

The bundled Playwright headless shell timed out during image capture. The installed Chrome channel completed the browser journey and captures; terminal SVGs were rasterized locally with Quick Look. The original failed capture attempts remain in the local QA artifacts.

The separate [npm distribution verification](npm.md) records the packaged launcher, disconnected installation, terminal signal checks, and installed browser service.

## Limits

No paid model calls or new public-repository benchmark attempts were made for this change. The Docker runtime remained stopped. The skipped cases require separately enabled Docker/GitHub integration or optional benchmark tooling; they are not counted as passing.

The systemd units were not booted on Linux, and the new deployment guide's Linux installation, firewall policy, private PKI connection and target-host restoration were not exercised end to end. The [on-premises guide](../../on-premises.md) retains these commissioning steps explicitly. Portable tests and a built wheel do not establish deployment certification, high availability or a security audit.

Raw logs, screenshots, local databases and package artifacts remain in the iMac QA workspace. No private task evidence or credentials were added to this record.
