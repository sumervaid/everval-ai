# Public repository pilot: frozen protocol

This pilot compares two agent workflows on external tasks from SWE-bench Lite. The [frozen selection](selection.json) contains 12 test tasks across Flask, Requests, pytest, and Sphinx, plus three separate development tasks. It records the choice before controls or paid model outcomes. This document specifies the comparison; it reports no evaluation outcomes.

These projects are public benchmark sources. They are not Everval customers or endorsers. A small cohort running on an adapted native ARM environment cannot establish an official SWE-bench score, a representative model ranking, or superiority over unrelated systems.

## Selection and provenance

The source is the test split of [SWE-bench/SWE-bench_Lite](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite/tree/b0dde1093fe417d83b7184254edf8199c1f0dff5), revision `b0dde1093fe417d83b7184254edf8199c1f0dff5`. The pinned Parquet contains 300 rows and has SHA-256 `438e281d80587aa7be470896ce410557002fde02d2ceee3e099331d308f62dd3`.

The repository allowlist was fixed to four popular pure-Python projects to fit a small local runtime. Within each repository, sort by `created_at` descending, then `instance_id` descending. Select the first three as test tasks. Select the fourth task from Requests, pytest, and Sphinx as development tasks. Flask has exactly three tasks in this dataset. The rule is deterministic and uses no random seed, model scores, patch size, or control outcome.

| Repository | Available Lite tasks | Test instance IDs | Development instance ID |
| --- | ---: | --- | --- |
| pallets/flask | 3 | pallets__flask-5063, pallets__flask-4992, pallets__flask-4045 | None |
| psf/requests | 6 | psf__requests-3362, psf__requests-2674, psf__requests-2317 | psf__requests-2148 |
| pytest-dev/pytest | 17 | pytest-dev__pytest-11148, pytest-dev__pytest-11143, pytest-dev__pytest-9359 | pytest-dev__pytest-8906 |
| sphinx-doc/sphinx | 16 | sphinx-doc__sphinx-11445, sphinx-doc__sphinx-10451, sphinx-doc__sphinx-10325 | sphinx-doc__sphinx-8801 |

The exact selection file has SHA-256 `4698b8cd1b4eb0637018690c594c9ab81477920adc7d05fc91dba46f242b64bb`. It includes base commits, environment setup commits, dataset image references, task hashes, evaluation-script hashes, and counts and hashes for both official test lists.

No task is replaced after freezing. Setup failures, failed controls, interrupted attempts, missing results, and model failures remain recorded. The primary test denominator remains 12 per arm. Any result over the runnable subset must also state the original denominator and every exclusion reason. Development outcomes remain separate from the test result.

## Controls and grading

The harness source is [SWE-bench at 02e7a74](https://github.com/SWE-bench/SWE-bench/tree/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e). The official build assets are [swe-bench-tasks at 3d07b464](https://github.com/SWE-bench/swe-bench-tasks/tree/3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d). Keep their revisions and every local adaptation in runtime evidence.

For each selected task, before admitting paid evaluation:

1. Prepare the repository at the exact base commit in a disposable Docker build environment.
2. Run the official tests on the unfixed base in a fresh grading container. The expected FAIL_TO_PASS cases must fail, and the PASS_TO_PASS cases must preserve their expected passing behavior.
3. Apply the official reference patch to another fresh base and rerun the full official test contract. Every FAIL_TO_PASS and PASS_TO_PASS case must pass.
4. Retain command logs, parsed case outcomes, image identity, durations, and explicit rejection reasons. Missing dependencies, missing cases, zero-test runs, and unsuccessful setup do not satisfy a control.

Each submitted patch is graded in another fresh container using the pinned row's evaluation script and official log parser. Resolution requires all official FAIL_TO_PASS and PASS_TO_PASS cases to pass after a complete grading run. There is no selected-test shortcut or custom success fallback. The required set is the complete official FAIL_TO_PASS and PASS_TO_PASS lists, rather than every test in the repository. Additional cases can appear in the invoked suite; retain their observed status counts and actual suite exit code, and do not describe a resolved task as proof that the entire repository suite passed.

The upstream evaluation script does not independently record every setup command's exit status. The runner checks script completion, recognized setup-error signals, and complete required-test outcomes, and retains the logs for inspection. A successful test subset alone cannot override a detected setup failure; the exported controls disclose this distinction.

Both arms also enforce the same `python-source-v1` patch policy: reject edits to tests, `conftest.py`, build and dependency files, configuration files, Git metadata, binary files and symlinks. Legitimate source modules such as `src/_pytest/config/__init__.py` remain allowed. A reference repair incompatible with this policy excludes the task before paid admission. This restriction hardens the local grader and is another difference from an unmodified leaderboard run.

This runner uses separate evidence directories and fresh grading containers for base controls, reference controls, and each agent attempt; it does not reuse the official evaluation cache. If reproducing through the official harness, use distinct run IDs: its [FAQ](https://www.swebench.com/SWE-bench/faq/) warns that caching is keyed by run and instance identity, rather than patch content.

The agent receives the issue statement and starting source snapshot. Reference patches, test patches, hidden grading material, future commits, and evaluator logs stay outside its environment. Agent and grader containers have no network access or host filesystem mounts. Evaluated repository installation, hooks, builds, and tests never run on the personal host. The host runs trusted, pinned orchestration and grading tools.

## Native ARM runtime

The local Docker allocation is two CPUs and approximately 4 GiB of memory on ARM64. Preparation initially used 2 GiB; the allocation increased before any official repository controls or paid attempts. Both arms retain the same per-container resource limits. The selected official task recipes specify x86 Ubuntu, an x86 Miniconda installer, and architecture-specific Conda builds. Their dataset image tags are provenance references, not immutable native ARM images.

Any native adaptation must retain the original task and test contract, record the Dockerfile and dependency changes, pin the resulting image identity, and pass the controls above. Both comparison arms use that same prepared image. The published evidence must distinguish this adaptation from the official x86 image. The [SWE-bench Docker guide](https://www.swebench.com/SWE-bench/guides/docker_setup/) describes broader resource expectations; this small pilot is intentionally narrower.

The [preparation recipe](../../../scripts/benchmarks/README.md) switches Ubuntu and the same-release Miniconda installer to ARM64, removes x86 build strings and explicit platform-runtime pins, and retains declared Python and Python-library version pins. It uses checksum-verified micromamba 2.9.0-0 with sharded metadata to reduce resolver memory. Copied setup scripts preserve tracked executable bits instead of making every file executable. Evidence records the original and adapted recipes, script hashes, resolved Conda package records, explicit package references, pip packages, and installer hash. A later rebuild can resolve different transitive packages from mutable indexes; the evaluated image IDs remain fixed.

Preparation separates reusable Conda stages from each task's ordered pip installs and repository setup. Identical ordered Conda pins, channels, platform, and resolver settings can share a stage; the selected recipes form four such groups. The pip entries keep their original order and target interpreter.

To support the unchanged evaluator's offline installers, images include a local wheelhouse for build tooling, with exact versions, filenames, sizes, and SHA-256 hashes retained. The evaluator uses this local package source with package-index access disabled. A required backend missing from that source remains a setup failure; passing tests do not override it.

Dependency preparation may download public sources and packages inside Docker. Evaluation itself runs offline. A mutable dataset image tag alone is insufficient runtime provenance.

## Matched comparison

Both arms use the same model, task snapshot, prepared runtime, command interface, and limits. The baseline follows the [mini-SWE-agent textual action loop at a83fcae](https://github.com/SWE-agent/mini-swe-agent/tree/a83fcae82d2a08f0ee0c688f9d137b3566c097f8), using its unmodified `swebench_backticks.yaml` prompts and the common limits below. The Everval variant adds one explicit review checkpoint before final submission. The checkpoint asks the agent to inspect its proposed change and available local evidence; it does not expose evaluator-only material.

| Setting | Both arms |
| --- | --- |
| Provider model | `gpt-6.1-sol` |
| Reasoning effort | `medium` |
| Service tier | `default` |
| Transport | Chat Completions with textual actions |
| Agent step ceiling | 250 |
| Provider cost ceiling | $6 per attempt |
| Model-step admission threshold | 1,800 seconds |
| Command deadline | 60 seconds |
| Conversation input ceiling | 128 KiB of serialized message text |
| Command output ceiling | 1 MiB before upstream observation formatting |
| Response output-token ceiling | 8,192 |
| Repetitions | One attempt per arm per selected task |

Each task is run as a pair. Baseline runs first when the first byte of the SHA-256 of the instance ID is even; Everval runs first when it is odd. This counterbalances order without consulting outcomes.

The upstream agent checks elapsed time before admitting another model request. An in-flight request and its action may finish after the threshold; grading and cleanup extend total elapsed time separately. The HTTP client has operation timeouts, not an absolute request wall-clock deadline.

The review consumes the same step, time, and spending allowance; it receives no additional budget. The exact upstream mini-SWE-agent revision, prompts, implementation hashes, returned provider model, runtime image, and effective settings must be retained with attempts.

The model name is an alias, not a frozen provider snapshot. Record the provider-returned model identifier and run times. Availability of an alias does not establish behavior stability.

Both configurations are declared before outcomes. The three development tasks exercise the process using these same fixed configurations; they are not a license to tune against test failures. A later configuration change requires a separately declared experiment and must retain the earlier evidence.

A meaningful paired report gives each task's outcome for both arms, raw success counts, unresolved and infrastructure counts, paired differences, provider cost, and elapsed time. It must not equate a lower cost from an incomplete attempt with an improvement. Reported attempt duration measures solver elapsed time, including solver setup and cleanup; it excludes grading. Latency depends on this local runtime and recorded scheduling conditions.

## Spending and accounting

The cumulative measured OpenAI evaluation allowance is $200, including the previous campaign's $0.482999. This campaign admits at most 30 attempts: 15 tasks, two arms, and a $6 ceiling per attempt.

The conservative maximum is therefore **$180.482999 including prior spend**. Campaign admission stops at $190, leaving room below the user's $200 limit. These are ceilings and reservations, not observed spend.

The initial accounting configuration uses $2.50 per million input tokens and $10 per million output tokens. Verify the current [official model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [pricing table](https://developers.openai.com/api/docs/pricing) before a fresh paid run, record the configured pricing version, and fail closed when usage cannot be reconciled. Record actual returned usage rather than presenting reservations as billed cost. This provider ledger does not measure unrelated development services, infrastructure, or other accounts.

No paid attempt is admitted before selection, provenance, applicable controls, and budget checks are complete. Failed and interrupted calls retain their usage or unresolved accounting state. Missing or stale accounting cannot be replaced with a fresh zero-cost ledger when attempt evidence exists. Admission and export reconcile retained provider evidence against the ledger; incomplete evidence blocks continuation and cost-completeness claims.

## Interpretation and public evidence

SWE-bench tasks and reference fixes are public. Training-data exposure or prior familiarity cannot be excluded. The most recent tasks from four selected projects are not a random or representative sample of the complete benchmark. One attempt per arm also cannot establish repeatability or a statistically reliable ranking.

The comparison measures one workflow change under matched conditions. It does not reproduce unrelated leaderboard configurations and must not describe a win over them without a separate faithful comparison.

Private artifacts retain downloaded rows, repository snapshots, reference patches, hidden test material, commands, logs, and usage. Public records contain selection IDs, references, hashes, configuration summaries, aggregate results, and sanitized failure explanations. Publication is explicit; raw task data and traces are not automatically uploaded.

The pinned dataset card does not state a dataset license. The MIT license of the harness does not establish redistribution rights for every issue statement or patch. This repository therefore includes task metadata and attribution, rather than copies of dataset contents or source repositories.

## Attribution

The task dataset, task-build recipes, and evaluation contract come from the [SWE-bench project](https://www.swebench.com/) and its [research paper](https://arxiv.org/abs/2310.06770). The baseline agent design comes from [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent). Their code and associated materials retain their own license terms.

The source projects and their pinned notices are [Flask](https://github.com/pallets/flask/blob/182ce3dd15dfa3537391c3efaf9c3ff407d134d4/LICENSE.rst), [Requests](https://github.com/psf/requests/blob/36453b95b13079296776d11b09cab2567ea3e703/LICENSE), [pytest](https://github.com/pytest-dev/pytest/blob/2f7415cfbc4b6ca62f9013f1abd27136f46b9653/LICENSE), and [Sphinx](https://github.com/sphinx-doc/sphinx/blob/71db08c05197545944949d5aa76cd340e7143627/LICENSE). Preserve the notices applicable to every downloaded revision and dependency. Naming these projects identifies benchmark provenance and conveys no affiliation.
