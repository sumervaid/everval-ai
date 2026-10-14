# Inspect legacy Python history drafts

The historical compiler turns a restricted class of local Git changes into
reviewable coding scenarios. It reads commit metadata, source blobs, and Python
syntax. It does not check out files, run hooks, import repository modules,
install dependencies, or execute repository tests.

For admitted experiments, use the [historical benchmark release workflow](benchmarks.md).
It adds GitHub projects, reviewed wording, repeated historical and regression
controls, prepared pytest profiles, and immutable releases.

The commands below remain available for inspecting conservative extraction drafts.
Saving or diagnostically validating a draft does not admit it to experiments.

## Inspect, extract, then validate

Use an absolute path to a local working repository:

```sh
uv run everval inspect /absolute/path/to/repository --limit 20
uv run everval mine /absolute/path/to/repository --limit 20
```

The first command previews recent commit metadata. The second returns:

- `collection`: an extracted collection, or `null` if no candidates qualify.
- `candidates`: the complete candidate ledger with acceptance or rejection reasons.
- `counts`: inspected, accepted, rejected, and scenario counts.
- `executed_repository_code: false`: extraction performed no repository execution.

By default, extraction does not save a collection. Add `--save` after reviewing
the candidate scope:

```sh
uv run everval mine /absolute/path/to/repository --limit 20 --save
```

Copy the returned `collection.id`, inspect its instructions and controls in
the app, and validate it using the configured [coding sandbox](sandbox.md):

```sh
uv run everval collections validate COLLECTION_ID
```

**Accepted means extraction succeeded.** Diagnostic collection validation observes
the reference and base but does not create a release attestation. Historical
drafts cannot run as experiments, imported submissions, or scheduled reruns until
the project workflow produces a matching reviewed release. An unchanged
behavior test may extract successfully but fail the negative-control gate.
A missing dependency, zero-case run, or unavailable runtime does not establish
task validity.

The HTTP equivalent is `POST /api/repositories/mine` with
`{"path":"/absolute/path/to/repository","limit":20}`. It returns the same
report without saving the collection. Normal workspace authentication and
same-origin restrictions apply.

## Supported history and assertions

A candidate needs exactly one parent and must change both Python source and
Python test files. The compiler recognizes test files named `test_*.py` or
under a `test`/`tests` directory. It reads the reference commit's supported
tests and creates cases from literal equality assertions.

For example, with a top-level `arithmetic.py` module:

```python
from arithmetic import total


def test_numeric_total():
    assert total([2, 4]) == 6
    assert total([]) == 0
```

A direct unittest form also works:

```python
import unittest
import arithmetic


class TotalTests(unittest.TestCase):
    def test_numeric_total(self):
        self.assertEqual(arithmetic.total([2, 4]), 6)
```

Imports must resolve directly to a source file in both snapshots. The target
function must be a top-level synchronous function that exists in the changed
module in both base and reference versions. Aliases and literal keyword
arguments are supported.

Each extracted function receives its own scenario. Its solver inputs contain
the base Python source files. The reference output contains the later Python
source files. Only function arguments enter the verifier; expected literal
answers remain in the host-side evaluator. Tests and Git objects are excluded
from solver inputs.

## What is quarantined

Unsupported semantics cause candidate rejection with a reason; the compiler
does not quietly extract just the easy assertions from an otherwise unsupported
test file. Examples include:

- Merge commits, root commits, or changes without both source and tests.
- Relative or wildcard imports, unresolved modules, and unrelated imports with potential setup effects.
- Fixtures, parametrization, decorators, helper methods, class setup, and test arguments.
- Loops, runtime variables, comparisons other than direct equality, and expanded arguments.
- Asynchronous functions, methods, non-JSON values, or unavailable source snapshots.
- Deleted or oversized test files and incomplete Python source snapshots.

The extracted environment includes Python source only. Package installation,
native extensions, services, custom build steps, and arbitrary test runners are
not inferred. Projects requiring them need a separately reviewed environment
and evaluator.

## Review the draft

Every scenario records the base and reference commit hashes, original commit
subject, test paths, extraction version, and negative base output. Instructions
are derived from the commit subject; they may lack context or reveal the
solution. Review them against the original issue and record your decision before
using the scenario to compare systems.

Source permissions are marked for review. Keep private source snapshots and
grading data in the configured workspace rather than the project repository.
Do not label historical public tasks as uncontaminated.

One diagnostic validation cycle establishes observed reference/base behavior only.
The benchmark release workflow requires three fresh cycles, unchanged regression
controls, reviewed wording, and exact environment and grader fingerprints.
