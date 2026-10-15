# One bug fix, from discovery to decision

[Documentation](index.md) · [Try the demo](demo.md) · [First real evaluation](guided-run.md)

This illustrative story is based on the [coding example](../examples/community/coding/README.md). It is not a customer result.

A shop's cart counts each item once and ignores quantity. Two units priced at **$10 each** produce a **$10 total**. The correct total is **$20**.

## 1. Connect the repository

An engineer connects an authorized GitHub repository. Everval finds a merged repair for review; discovery alone does not validate it. Unsupported histories or environments can be rejected.

## 2. Write the problem clearly

A reviewer writes: “The cart total ignores item quantities. Two units priced at $10 each should total $20.”

This describes the expected behavior without copying the historical repair. The original fix and private checks stay with the evaluator.

## 3. Check that the task works

In an isolated runtime, the quantity test must fail on the old code and pass on the repaired code. Existing behavior, such as empty carts and single-item totals, must still work.

Everval repeats these controls. Missing dependencies, a broken setup or a run with no tests do not count as evidence that the task works.

## 4. Freeze several tasks

The team repeats this for other fixes and freezes a benchmark. The cart task goes into development; different, untouched tasks form the final holdout.

The same task does not go into both sets. Development helps choose a candidate; holdout checks that choice independently.

## 5. Let the agents try

The team selects two saved agent configurations, enters actual provider prices and sets a budget.

Each agent receives the problem and the code from before the fix. It cannot see the historical repair or hidden tests. Everval grades its submitted changes separately.

## 6. Read more than the score

The team compares passed tasks, failures, errors, cost and response time. They inspect failed outputs and checks. A missing charge stays unknown; it is not recorded as free.

## 7. Make a reviewed decision

The chosen configuration faces the reserved holdout. A reviewer examines the evidence and records whether to adopt it, reject it or gather more evidence. Everval retains the decision; it does not deploy the change.

## 8. Keep checking

The team can schedule development checks with per-run and cumulative spending caps. Unknown billing stops further automatic admissions. New failures remain available for investigation, and deployment decisions still require review.

## Try the small example

From an installed checkout:

```sh
uv run everval demo --example coding
```

This calls no model. Its teaching fixture checks a canonical source edit, not program behavior. Follow the [first session](quickstart.md) to install and explore the evidence, then the [repository guide](guided-run.md) for the full workflow.
