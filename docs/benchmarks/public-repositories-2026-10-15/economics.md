# Final pilot economics and solver timing

The four fully graded test pairs are all ties: both workflows resolve all four. On this matched subset, review costs more overall and has a higher median solver duration. The complete operational test counts are six review resolutions versus four baseline resolutions, but two extra review resolutions have interrupted, ungraded baseline comparators. They do not establish a repair-quality win.

Source: final-summary.json SHA-256 6764d3b1462668807b318883c24cc160ea838e9d41b6b116374ecd8cf6fc1e75. All 16 retained result hashes and solver/grader cleanup flags were checked; ledger totals independently reproduce the export. Costs use exact decimal arithmetic and the original frozen rates.

Matched scope: admitted test pairs where both grading statuses are graded and both observed all required tests. All four also have complete costs and observed solver durations. This is a supplementary matched analysis; the original 12 selected and seven admitted test denominators remain unchanged.

| Matched task | Baseline cost | Review cost | Baseline seconds | Review seconds | Baseline / review requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| pallets__flask-5063 | $0.1405125 | $0.32356 | 61.93 | 158.51 | 11 / 17 |
| pytest-dev__pytest-11143 | $0.1227 | $0.20927 | 45.47 | 57.16 | 9 / 12 |
| sphinx-doc__sphinx-11445 | $0.0993 | $0.1898825 | 61.76 | 90.16 | 8 / 13 |
| sphinx-doc__sphinx-10325 | $0.40916 | $0.39552 | 219.06 | 140.42 | 16 / 17 |

Matched total cost: baseline $0.7716725, review $1.1182325; review-minus-baseline $0.34656. Ratio of the two sums: 1.449102 (44.91% higher review cost). This is not an average of per-task ratios.

Matched median solver time: baseline 61.84s, review 115.29s, four observations each. The median within-task review-minus-baseline difference is 20.04s. The two medians' difference is a different statistic.

Matched total solver time: baseline 388.23s, review 446.25s; ratio of summed times 1.149452 (14.95% higher review time). Individual outcomes vary: review is cheaper and faster on Sphinx10325, and more expensive and slower on the other three matched tasks. All quality outcomes tie.

| Operational scope | Arm | Attempts | Known cost | Retained unknown allocation | Committed cost | Median solver seconds | Requests |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test | baseline | 7 | $0.7716725 | $0.3723275 | $1.144 | 91.64 | 47 |
| test | everval | 7 | $1.744005 | $0.1194075 | $1.8634125 | 92.39 | 91 |
| development | baseline | 1 | $0.145025 | $0 | $0.145025 | 59.86 | 10 |
| development | everval | 1 | $0.2240525 | $0 | $0.2240525 | 78.27 | 14 |
| all_pilot_attempts | baseline | 8 | $0.9166975 | $0.3723275 | $1.289025 | 76.78 | 57 |
| all_pilot_attempts | everval | 8 | $1.9680575 | $0.1194075 | $2.087465 | 91.27 | 105 |

Operational timing includes all executed attempts, including early interruptions. All test attempts have timings (seven per arm); development has one per arm. These medians are not quality-matched timing or evidence of equal speed. Requests are ledger admissions, including interrupted calls. Development remains separate: baseline resolves its task, review submits an unresolved repair.

| Campaign accounting | USD |
| --- | ---: |
| Earlier acceptance spending | $0.482999 |
| Known new pilot spending | $2.884755 |
| Known cumulative spending | $3.367754 |
| Four retained uncertain reservations | $0.491735 |
| Committed cumulative budget | $3.859489 |
| User ceiling | $200 |

The ledger contains 158 settled requests, four uncertain requests and no pending requests. All four uncertain requests have null token counts, zero known cost and their full reservation retained. Zero known cost does not mean a free call. Both complete test arms have incomplete accounting, so exact all-attempt cost per resolved task is unavailable. The committed amount is a conservative budget allocation, not a confirmed invoice.

Solver elapsed time includes sandbox setup, provider calls, commands and cleanup; it excludes grading, preparation, controls and the stop/review/resume wait. Costs exclude local infrastructure, labor and development tools. One attempt per workflow on a small selected Python cohort cannot establish repeatability or statistical superiority. The post-stop amendment retained the original model, prompts, task set and limits and never reran an interrupted attempt.

Machine-readable exact amounts, paired differences, defined ratios, raw exported durations, request counts and reconciliation assertions are retained in economics.json. Unknown reservations and incomplete comparisons remain visible.
