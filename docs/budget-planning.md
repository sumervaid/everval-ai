# Budget planning

Everval keeps three different quantities visible: recorded usage, money reserved for admitted work, and projections for future runs. Unknown usage is never presented as zero.

```sh
everval budget status
everval budget project EXPERIMENT_ID --runs 30 --cap-usd 200
everval budget project FULL_ID --screening-experiment-id SCREEN_ID \
  --runs 30 --escalation-rate 0.2 --cap-usd 200
everval spending set --lifetime 200
```

In the terminal, choose **Budget ledger** or select an experiment and choose **Forecast budget**. The API exposes `GET /api/budget/status` and `POST /api/budget/projection` to operators.

## Reading a projection

- **Expected cost** extrapolates a completed live run with fully known usage. It stays unknown for controls, replays, partial runs or missing billing.
- **Reservation estimate** uses the frozen configuration's token ceilings and declared rates. It is an admission estimate, not a provider invoice guarantee.
- **Admission budget** is the amount the schedule or Autopilot policy must be able to reserve. Autopilot reserves screening and a possible full run before starting either.
- **Affordable cycles** uses the tightest currently remaining daily, monthly, lifetime or supplied planning cap. It does not assume future UTC resets.
- **Sequential attempt time** sums recorded attempt durations and excludes queueing. It is not a prediction of service throughput.

For illustration only, suppose a fully accounted screening run costs $0.10 and a full run costs $1.00. At 30 checks and an assumed 20% escalation rate, the expected cost is $9.00. If their configured admission budgets are $0.50 and $3.00, covering every possible escalation requires $105.00 in cumulative admission capacity. Those figures are arithmetic assumptions, not measurements from a model or a price quotation.

Configured rates and pricing versions travel with frozen experiments. Forecasts assume future tasks, configurations and rates remain unchanged. External tools, hosting, electricity and development services are outside the provider ledger. Provider invoices remain authoritative.

## Operational controls

Workspace and project caps are checked in the same SQLite transaction that reserves a trial. Schedule and Autopilot caps additionally govern cumulative run admission. Unknown charges retain reservations and halt the affected automation. A configured adapter is responsible for enforcing its own internal usage ceilings; an HTTP timeout cannot stop work inside another service.

Setting a cap does not enable hosted inference. Hosted inference still requires explicit remote-provider opt-in, configuration, credentials and per-run budget. Installation demonstrations and ordinary tests need no paid calls.

The cap command replaces that scope's configuration. Include daily and monthly options too if you want to preserve them. Local provider accounting does not measure the bill for the software used to develop Everval.
