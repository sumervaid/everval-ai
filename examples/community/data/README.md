# Data analyst: net revenue from paid orders

```sh
uv run everval demo --example data
```

Three invented orders expose two common errors: counting a cancelled order and failing to subtract refunds. The correct result is USD 140 net revenue from two paid orders.

The checks require a typed JSON object, the correct numeric amount within half a cent, and the paid-order count. Near misses test both business rules.

This is deterministic data evaluation with no warehouse connection. For a real analytics agent, supply a permitted frozen dataset or a reviewed application adapter and independently check units, aggregation rules, missing-value behavior, and tolerances. Do not turn the agent's existing answer into the expected answer.
