# Your first real evaluation

Open `everval tui`, choose **Start guide** on Overview, and give the run a name. **F9 · Guided runs** resumes it after the terminal closes. The guide stores choices in the local workspace and reconstructs completion from saved evidence. A checked step cannot be supplied by a progress flag.

1. **Check local execution.** Inspect Git, the configured local Docker image, storage and worker readiness. Follow the diagnostic's remediation when a prerequisite is missing.
2. **Connect.** Enter an authorized GitHub repository or a local Git path.
3. **Discover and choose.** Discovery retains both candidates and exclusions. Suggestions favor available problem text, changed tests and a bounded change surface. A suggestion is not approval, task validity or evidence of representative coverage.
4. **Review.** Write an independent problem statement, reviewer label and rationale. Describe behavior known before the repair; omit the historical solution and hidden checks. Review labels are self-reported.
5. **Prepare.** Inspect the environment plan before downloading dependencies, or register an already prepared image from Projects. Preparation and test execution run in containers. Unsupported builds fail with a reason.
6. **Validate.** Repeated before/reference controls must establish fail-before/pass-after and baseline preservation. Missing dependencies, zero tests and inconsistent controls remain unmet steps.
7. **Freeze.** Create a release if none exists, then explicitly choose its development, holdout or all partition. Keep holdout independent of development decisions.
8. **Select a live system.** Configure the endpoint, harness, model, credential environment reference and prices in Systems. The guide excludes fixture controls and imported replay outputs from the first real run.
9. **Run and inspect.** Declare a provider budget and repetition count. Closing the terminal does not discard queued work. A queued or failed experiment does not complete this step.

Select any step and press **Enter** or **Open step** to revisit it. Changing the chosen project or candidate clears dependent release/experiment selections. The original evidence remains in the workspace. An unsupported candidate can be replaced without deleting its rejection.

Projects remains available throughout the guide: inspect original candidate material, load older history, prepare a different runtime, or curate additional tasks. The guide describes a focused first run; a useful benchmark requires broader independently reviewed work.

## Benchmark health

Select a project and choose **Benchmark health**, or run:

```sh
everval benchmark-health PROJECT_ID --stale-days 30
```

The report retains denominators: discovered candidates, current admissions, exclusions and the merged-PR count returned by the most recent GitHub discovery. Local Git history has no inferred PR denominator. Coverage counts changed paths and extensions within the discovered ledger, not the entire workload distribution.

Exact duplicate detection groups identical base/reference commit pairs. It does not detect semantically equivalent tasks. Observed flakiness counts explicit repeated-control disagreement; model variation does not imply a flaky benchmark. Staleness reports age, changed candidate metadata and grader revisions. An age flag asks for review; it does not rewrite old frozen releases.

Inspect **Triage**, **Cost / latency** and **Decision brief** on Experiments. From Trial evidence, **Diagnose attempt** inspects the saved evidence; **Regrade saved output** creates linked local grading evidence without another model request. Full records remain available for detailed inspection.

## Automation

The same durable selections are available without the terminal interface:

```sh
everval guides start --name "Repository evaluation"
everval guides list
everval guides select GUIDE_ID project_id PROJECT_ID
everval guides status GUIDE_ID
```

The CLI reports state and evidence. Repository connection, review, environment preparation, validation and release commands continue to use the shared services described in the other guides.
