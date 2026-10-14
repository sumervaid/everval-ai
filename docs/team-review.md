# Team access and assigned reviews

Everval supports optional personal identities for the HTTP API and browser. The terminal, CLI, and direct SQLite access remain trusted filesystem-owner interfaces. Use a protected local machine and SSH tunnels or a correctly configured HTTPS service when people connect remotely.

## Enable without losing administrator access

Start in the existing trusted local mode. On the workspace host:

```sh
uv run everval team bootstrap --name "Workspace administrator"
```

The command creates an administrator, issues a personal token, and enables team authentication in one transaction. It prints the token once. Save it securely and connect the browser with that token. The initial token expires after 90 days.

Team mode takes precedence over the legacy shared workspace token. Existing shared-token installations continue to work until an administrator explicitly bootstraps team mode. Starting the application never enables team mode automatically.

Open **Team access** to add identities and issue tokens. New tokens default to 30 days and can last up to 365 days. Tokens contain a random 256-bit secret; storage retains a random salt and scrypt verifier, never the bearer credential. The browser keeps the active token in session storage and displays newly issued tokens once.

For recovery through trusted local access:

```sh
uv run everval team users
uv run everval team issue-token USER_ID --label "Recovered access" --days 30
uv run everval team revoke-token TOKEN_ID
```

Issuing a replacement does not revoke existing tokens. Revoke compromised credentials separately. The application refuses to revoke the last usable administrator token or disable the last usable administrator identity. Local console access can issue a replacement when all tokens have expired.

## Choose a role

| Role | HTTP access |
| --- | --- |
| Administrator | Evaluation workspace, summaries, reviewers, identities, tokens, and cumulative spending controls. |
| Operator | Evaluation workspace, production cases, queue creation and assignments, decision recording, and summary reports. |
| Reviewer | Their identity and explicitly assigned review items and judgments. |
| Viewer | Summary reports and review decision briefs; no raw task, output, grader, or configuration endpoints. |

Every API route requires an explicit registered capability in team mode. New or unknown routes fail closed until registered. Reviewers cannot use generic collection, experiment, export, event-stream, audit, or diagnostic endpoints to retrieve hidden or unassigned evidence.

Token revocation and identity disabling take effect on subsequent requests. Active experiment event streams also recheck access. API mutations retain the authenticated identity in transactional audit records. The audit chain remains locally controlled; it is not an independently anchored log.

## Create a review queue

1. Finish an experiment that has recorded outputs for the chosen systems.
2. Open **Review desk → Manage queues → Create review queue**.
3. Choose one system for individual assessment or two for a paired comparison.
4. Create or select a rubric. Give each dimension clear guidance for zero, partial credit, and one. Names and versions are immutable; changed criteria require a new version.
5. Select active reviewer identities. Enable blinding when appropriate, then freeze and assign.

The queue freezes the public task, submitted outputs, rubric, and response-to-system mapping. Incomplete pairs are rejected rather than silently removed. Each assigned person sees the task and responses through a restricted endpoint. The server withholds reference answers, private checks, traces, system identifiers, and system metadata. Blind pair order is randomized per task and retained consistently.

Blinding also replaces the operator's queue title with a neutral label in the reviewer interface. Task text, rubric instructions, and response content can still disclose a source. Use neutral instructions and independent reviewer identities. Operators and administrators can access the wider workspace, so their role cannot provide the same information separation as a reviewer role.

## Review and decide

Reviewers open **Your review desk**, inspect the task and supporting inputs, score every response against every dimension, and record a rationale. Paired reviews also require a preference or tie. The authenticated identity supplies authorship; there is no editable reviewer-name field.

Revised judgments append a new record and retain the earlier judgment. Reviewers see their own latest judgment. Changing assignments immediately removes access for people who are no longer assigned.

Operators and viewers can read the decision brief. It shows review coverage, dispositions, pair preferences, rubric, and retained decisions. These descriptive counts do not establish independent model-performance estimates. Human judgments remain separate from automated outcomes.

Decision briefs label installation fixtures and imported submissions explicitly. Reviewing fixtures does not measure model performance, and reviewing the same imported output creates no additional independent model attempt.

An operator records **Adopt**, **Reject**, or **Gather more evidence**, with a rationale. Each decision retains the exact brief fingerprint. If judgments or assignments changed after the brief was displayed, refresh before recording the decision. **Print brief** uses the browser's print dialog for a readable local artifact.

## Spending controls

Administrators can configure workspace and repository daily, monthly, and lifetime caps under **Team access**. Daily and monthly periods use UTC. Committed spending includes unsettled reservations. A blank field removes that limit; zero prevents paid admissions. Per-experiment and optional cumulative schedule budgets still apply.

## Boundaries

This is a single-workspace local identity system. It does not provide SSO, password login, email invitations, tenant isolation, or restricted CLI sessions. Protect the host and workspace backups. Tokens grant the current role of their identity; keep them out of task content, repository files, screenshots, and exported logs.
