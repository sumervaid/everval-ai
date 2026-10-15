# Practical use cases

[Documentation](index.md) · [Worked example](worked-example.md) · [Quickstart](quickstart.md)

Everval is useful when a team can describe what a good answer or repair must do, collect permitted examples, and review the evidence. These are illustrative workflows using shipped capabilities, not customer stories or measured business results.

## Catch regressions in a coding agent

**Who operates it:** an evaluation engineer or developer-tools maintainer.

**Decision:** does a new model, prompt or agent revision still handle the engineering work we care about?

Connect your own authorized repository. Review historical fixes, write independent problem statements, and validate that the old code fails the relevant checks while the repair passes and selected existing behavior survives. Freeze several tasks; keep development work separate from the final holdout.

Run the agent on the starting code and issue statements. It receives neither the historical repair nor the hidden checks. Inspect failures and incomplete attempts before accepting a change.

Python and JavaScript/TypeScript profiles support specific runtime and dependency contracts. Unsupported histories, broken setup and empty test runs remain exclusions. An accepted subset does not represent the whole repository automatically.

**Start:** [Guided repository evaluation](guided-run.md) · [Existing agent adapter](agent-adapters.md)

## Test document, data and workflow outputs

**Who operates it:** an applied-AI engineer with a domain reviewer.

**Decision:** does the application follow the rules its users depend on?

Create tasks from permitted examples: answer a refund question using the current policy and a valid quotation; calculate revenue after excluding cancelled orders and refunds; propose a workflow's final state without changing protected fields. A reviewer defines expected behavior independently, supplies known-good and deliberately wrong controls, and validates the task before admission.

Connect the application through an adapter, or import outputs it has already produced. Compare changes on the same frozen examples.

Citation checks verify source membership and literal quotations, not general semantic correctness. Workflow tasks evaluate supplied state and outputs; they do not independently inspect a live business application.

**Start:** [Runnable examples](../examples/community/README.md) · [Scenario format](scenarios.md) · [Reviewed production cases](production-cases.md)

## Choose a model within a budget

**Who operates it:** a technical lead or platform engineer.

**Decision:** which configuration meets our quality and response-time requirements at an acceptable recorded cost?

Save explicit model, instruction or tool-budget candidates. Configure actual provider rates and bounded execution limits. Compare them on one development cohort, retaining errors and incomplete attempts.

Read success rate, cost per passing attempt, provider time and total attempt duration together. A constrained study can select an eligible candidate and check it once against a reserved, unused holdout. The output is a proposal for review.

Unknown usage remains unknown. Recorded cost uses supplied usage and configured rates; it is not an invoice or a promise of future savings. A small difference on a narrow task set may not support a choice.

**Start:** [Cost and latency](cost-latency.md) · [Budget planning](budget-planning.md) · [Configuration studies](research-loop.md)

## Give decision owners evidence they can review

**Who operates it:** an evaluation operator, assigned reviewers and a technical decision owner.

**Decision:** adopt the change, reject it, or gather more evidence?

Finish an experiment, freeze a review rubric, and assign browser reviews. Blinded comparisons withhold system identities and private evaluator material through the reviewer endpoint. Reviewers score answers and explain their judgments; operators inspect coverage and record a decision against the current brief.

Automated scores and human judgments remain separate. Viewer accounts can inspect summaries without generic access to raw tasks or graders.

Answer content can still reveal its source. Browser roles do not restrict trusted terminal or filesystem access, and this release is a single-workspace system.

**Start:** [Team review](team-review.md) · [Responsibilities and handoffs](enterprise-workflows.md) · [Decision policies](policies.md)

## Keep checking after a change

**Who operates it:** the platform owner responsible for the evaluation worker.

**Decision:** does a new revision need a fuller evaluation or investigation before adoption?

Enable a reviewed schedule or Autopilot policy. Reuse frozen development tasks, set per-run and cumulative budgets, and choose periodic or supported change-linked checks. Autopilot can screen a smaller cohort and escalate according to the configured policy. Failed attempts retain evidence for investigation.

The worker must remain running. Unknown billing holds the affected automatic admissions and preserves unsettled reservations. Pausing future admissions does not cancel queued work. A source fingerprint does not prove which revision is deployed inside an external service, and Everval does not deploy a winner.

**Start:** [Autopilot](autopilot.md) · [Change-linked evaluations](change-evaluation.md) · [Operations](operations.md)

## Read examples and results in context

The bundled examples are original installation controls. Public repositories used in benchmarks are external subjects. Their inclusion does not imply a customer relationship, partnership or maintainer endorsement. Read each result's task selection, execution settings and limits before applying it to your own work.

The [public repository pilot protocol](benchmarks/public-repositories-2026-10-15/methodology.md) specifies a matched comparison on Flask, Requests, pytest and Sphinx. It includes the frozen task selection, baseline, grading contract and spending limits; it does not yet report model outcomes.

For a simple explanation of the whole process, follow [one shopping-cart bug from discovery to decision](worked-example.md).
