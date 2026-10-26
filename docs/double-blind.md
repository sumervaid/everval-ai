# Double-blind studies

[Documentation](index.md) · [Team access and reviews](team-review.md) · [On-premises deployment](on-premises.md)

Compare two model configurations without giving their names to independent reviewers. Collect every assigned judgment, lock the scores, then reveal the identities and results.

The available mode is **trusted-custodian review of completed outputs**. Reviewers and summary readers are blinded through their restricted interfaces. The coordinator, administrators, operators, and filesystem owner remain trusted and can know the original experiment. This is retrospective human review, not confidential model execution or a guarantee that nobody previously saw the answers.

This is a current-source feature. The published npm **0.3.1** package predates it; use the [source installation](quickstart.md#install-from-source).

## A concrete example

Your support team is considering a new model configuration. Both configurations have answered the same saved support questions in a completed experiment.

1. A coordinator checks the task wording, response text, and rubric for clues such as provider names or distinctive system messages.
2. Two or more independent reviewers receive the questions and anonymously labeled response pairs through their personal review accounts.
3. Each reviewer scores the answers and records a preference without seeing model names, reference answers, private checks, or other reviewers' judgments.
4. Once every assigned judgment is recorded, the coordinator locks the study. Further scoring and revisions are rejected.
5. The coordinator records a reason for revealing results. Everval releases the model identities and a report bound to those locked judgments.

This helps a team discover whether people prefer the proposed deployment before a model's name influences their assessment. It does not turn a small or selected task set into a general model ranking. Human judgments remain separate from automated test scores.

![Released fixture study in the Everval browser](assets/double-blind-study.png)

*Actual browser interface with original installation controls and scripted judgments. [Executed checks and limitations](evidence/2026-10-26-double-blind/README.md).*

## Start in the browser

Use the same workspace that contains the completed experiment:

1. Enable [team access](team-review.md#enable-without-losing-administrator-access). Create at least two active identities with the **Reviewer** role, and give each person their own token.
2. Choose reviewers who have not already seen the named results. Operators and administrators cannot be assigned as study reviewers. Changing a person's role cannot undo what they already know.
3. Open **Double-blind evaluations → Create a study**.
4. Choose the completed experiment and exactly two recorded model configurations. Select a saved scoring rubric or create one, and select the reviewers.
5. Choose **Freeze and assign study**. The study freezes its task pairs, response mapping, rubric, and assignments. It makes no additional model calls.
6. Reviewers sign in separately and use **Your review desk**. The coordinator follows completion in the study.
7. Choose **Lock completed judgments**, then supply a reason and choose **Reveal identities and results**.
8. Download the released **HTML report** or **JSON evidence**.

Incomplete response pairs are rejected. The workflow does not quietly drop missing outputs or treat an unanswered review as a score.

## Start in the terminal

```sh
uv run everval blind tui
```

Or press **Ctrl+Y** in the terminal workbench. The dedicated screen shows studies, assigned review coverage, and the separate lock and reveal actions. **New study** walks through the completed experiment, saved rubric, reviewers, and model pair. Create reviewer identities and a rubric through the browser first.

The standalone study screen does not start a worker. It coordinates saved evidence; it does not run inference. Reviewers use their personal browser access. Local terminal access belongs to the trusted workspace owner.

## Use the CLI

Replace the uppercase identifiers with records from your workspace. Repeat `--system` exactly twice and `--reviewer` for every independent reviewer:

```sh
uv run everval blind create \
  --name "Support deployment comparison" \
  --experiment EXPERIMENT_ID \
  --rubric RUBRIC_ID \
  --system BASELINE_SYSTEM_ID \
  --system CANDIDATE_SYSTEM_ID \
  --reviewer REVIEWER_ONE_ID \
  --reviewer REVIEWER_TWO_ID

uv run everval blind list
uv run everval blind show STUDY_ID
```

After all assigned reviews are complete, copy the current `evidence_hash` from `show`:

```sh
uv run everval blind lock STUDY_ID --expected-hash CURRENT_EVIDENCE_HASH
uv run everval blind show STUDY_ID
```

Copy the new hash from the locked study, then release its results:

```sh
uv run everval blind reveal STUDY_ID \
  --expected-hash LOCKED_EVIDENCE_HASH \
  --reason "All independent reviews are complete and locked."

uv run everval blind report STUDY_ID > "$HOME/everval-study-report.json"
```

A changed fingerprint requires a refresh. A missing review blocks locking. Reports remain unavailable until reveal, including through the existing review-queue report endpoints. Lock and reveal are explicit actions; there is no automatic reveal or app operation to reopen a locked study.

Assignments cannot be replaced to remove an unfinished review. Reactivate a disabled reviewer when appropriate so they can finish. Every study sharing the same source experiment must be locked before any can reveal; creating another study does not bypass an abandoned study's coverage requirement. Use a fresh source experiment when the cohort changes.

## What stays concealed

| Person or interface | Before reveal |
| --- | --- |
| Assigned reviewer | Assigned task and anonymously labeled responses, frozen rubric, and their own latest judgment. No model mapping or other reviewers' judgments. |
| Summary reader | Neutral study name, phase, review coverage, and evidence fingerprint. No identified study report or decision brief. |
| Trusted coordinator, administrator, or operator | The study interface withholds its report, but their wider workspace access can expose the source experiment. They are outside the blinding boundary. |
| Local CLI, host owner, backups, or direct database access | Trusted access to deployment data. Filesystem access is not a restricted reviewer session. |
| Hosted model provider | Receives the prompts sent during inference. Creating a later blind study does not change that exposure. |

Task wording, rubric instructions, response style, and a model's own self-identification can reveal its identity. Neutral labels alone cannot prevent those inferences. Everval does not attest reviewers' prior knowledge, independence, or non-collusion.

The workflow records assignments, judgments, transitions, fingerprints, and reveal rationale in the local workspace. Those records help inspect what happened through the application. An administrator who controls the database and source code can alter that environment; the records are not a remotely attested or independently anchored proof.

## How open-source can it be?

All Everval framework code can remain open: the study engine, interface, role checks, randomization logic, locking rules, and report format. Public implementation makes those rules inspectable without making evaluation material public.

Keep deployment data outside the source repository:

- Private tasks, expected answers, hidden tests, and customer inputs.
- Model credentials and access tokens; any locally hosted weights remain subject to their own license.
- Study identity mappings, judgments, and unreleased results.
- Workspace databases, logs, exports, and backups.

An open repository does not require those artifacts to be open. Conversely, keeping a mapping out of Git does not protect it from the workspace administrator or a hosted provider. Use the [on-premises guide](on-premises.md) to establish the actual host and access boundary.

You can publish a protocol, non-sensitive example tasks, code version, and an approved aggregate report. Publishing a report or task set is a separate choice; creating a study does not publish either. Do not reuse publicly released cases as if they were a secret holdout.

## How this differs from confidential evaluation

The [Google DeepMind pilot](https://deepmind.google/blog/piloting-the-worlds-first-double-blind-ai-evaluations/) describes a stronger problem: evaluating private models against private tests while restricting what the model developer and evaluator can learn about one another's material. Its [technical report](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/piloting-the-worlds-first-double-blind-ai-evaluations/double-blind-evaluations-technical-report.pdf) and [Google Cloud Confidential Space overview](https://docs.cloud.google.com/confidential-computing/confidential-space/docs/confidential-space-overview) describe the protected-computing approach.

Everval does not implement that confidential runner. This release has no trusted execution environment, hardware attestation, attestation-bound key release, protected model-weight loading, or verified output/egress policy. A deployment would need a separate verified runner and cooperation from the model owner to make those guarantees. An ordinary hosted API call does not supply them.

A future confidential runner can also have public source, build recipes, attestation policies, and output-release rules. The private inputs would be released only to an approved, attested workload. That is a design direction, not a capability of this release. Hardware, firmware, model licenses, and the participating organizations remain separate trust and licensing dependencies.

## Recurring benchmarks and chat

Use [benchmark chat](chat.md) to run ordinary comparisons on a schedule. Create a new blind study over each completed experiment when you need independent human review. New assignments, human judgments, locking, and reveal are not automated by chat.

If you ask chat for a double-blind, sealed, or confidential benchmark, it leaves that requirement unresolved and points to this dedicated capability. It does not silently start a standard run. To explicitly choose an ordinary evaluation in that conversation, say **use standard unblinded evaluation**. An already running standard benchmark cannot be retroactively converted into a protected study.
