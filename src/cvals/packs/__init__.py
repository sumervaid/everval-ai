"""Original, small fixtures for learning the evaluation workflow.

These scenarios are pipeline controls, not a representative enterprise benchmark.
All examples use invented records and deliberately small dependency-free programs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from cvals.models import Check, CheckKind, Collection, Domain, Scenario

_CREATED = "2026-10-13T00:00:00+00:00"
_PROVENANCE = {
    "origin": "original_fixture",
    "license": "Apache-2.0",
    "purpose": "Demonstrate domain-specific evaluation; not a real-world performance claim",
}


def _check(
    identifier: str, name: str, kind: CheckKind, path: str, expected: Any, **kwargs: Any
) -> Check:
    return Check(id=identifier, name=name, kind=kind, path=path, expected=expected, **kwargs)


def _scenario(
    identifier: str,
    name: str,
    domain: Domain,
    instruction: str,
    inputs: dict[str, Any],
    reference: dict[str, Any],
    checks: list[Check],
    negatives: list[dict[str, Any]],
    tags: list[str],
) -> Scenario:
    return Scenario(
        id=identifier,
        name=name,
        domain=domain,
        instruction=instruction,
        description="Original reviewable fixture with explicit success criteria.",
        inputs=inputs,
        reference_output=reference,
        checks=checks,
        tags=tags,
        provenance={**_PROVENANCE, "negative_controls": negatives},
    )


def _coding() -> Collection:
    definitions = [
        (
            "stable-unique",
            "Preserve order while removing duplicates",
            "stable_unique",
            "Return the first occurrence of each hashable value in its original order. Accept an empty list.",
            "def stable_unique(values):\n    return sorted(set(values))\n",
            "def stable_unique(values):\n    return list(dict.fromkeys(values))\n",
            [
                {"args": [[3, 1, 3, 2, 1]], "expected": [3, 1, 2]},
                {"args": [[]], "expected": []},
                {"args": [["b", "a", "b"]], "expected": ["b", "a"]},
                {"args": [[0, -1, 0, 2]], "expected": [0, -1, 2]},
            ],
        ),
        (
            "balanced-chunks",
            "Keep the final partial chunk",
            "chunked",
            "Split a list into consecutive chunks of at most size elements. Reject size <= 0 with ValueError.",
            "def chunked(values, size):\n    return [values[i:i+size] for i in range(0, len(values)-size+1, size)]\n",
            "def chunked(values, size):\n    if size <= 0:\n        raise ValueError('size must be positive')\n    return [values[i:i+size] for i in range(0, len(values), size)]\n",
            [
                {"args": [[1, 2, 3, 4, 5], 2], "expected": [[1, 2], [3, 4], [5]]},
                {"args": [[], 3], "expected": []},
                {"args": [[1], 4], "expected": [[1]]},
                {"args": [[1], 0], "raises": "ValueError"},
                {"args": [[1], -1], "raises": "ValueError"},
            ],
        ),
        (
            "weighted-mean",
            "Use weights when averaging",
            "weighted_mean",
            "Given equally sized lists of numeric values and nonnegative weights, return sum(value*weight)/sum(weights). Raise ValueError for mismatched lengths, negative weights, or total weight zero.",
            "def weighted_mean(values, weights):\n    return sum(values) / len(values)\n",
            "def weighted_mean(values, weights):\n    if len(values) != len(weights) or any(w < 0 for w in weights) or sum(weights) == 0:\n        raise ValueError('invalid weights')\n    return sum(v*w for v, w in zip(values, weights)) / sum(weights)\n",
            [
                {"args": [[10, 20], [1, 3]], "expected": 17.5},
                {"args": [[8, 2], [0, 2]], "expected": 2.0},
                {"args": [[4], [2]], "expected": 4.0},
                {"args": [[1], [0]], "raises": "ValueError"},
                {"args": [[1, 2], [1]], "raises": "ValueError"},
                {"args": [[1], [-1]], "raises": "ValueError"},
            ],
        ),
        (
            "first-present",
            "Preserve false and zero values",
            "coalesce",
            "Return the first value that is not None. Return None when the list is empty or contains only None.",
            "def coalesce(values):\n    return next((value for value in values if value), None)\n",
            "def coalesce(values):\n    return next((value for value in values if value is not None), None)\n",
            [
                {"args": [[None, 0, 1]], "expected": 0},
                {"args": [[None, False, True]], "expected": False},
                {"args": [[None, "", "hello"]], "expected": ""},
                {"args": [[]], "expected": None},
                {"args": [[None, None]], "expected": None},
                {"args": [[None, "yes"]], "expected": "yes"},
            ],
        ),
        (
            "interval-union",
            "Merge overlapping and touching intervals",
            "merge_intervals",
            "Return a sorted union of valid inclusive [start, end] intervals. Merge touching endpoints and accept empty input.",
            "def merge_intervals(intervals):\n    return sorted(intervals)\n",
            "def merge_intervals(intervals):\n    merged = []\n    for start, end in sorted(intervals):\n        if merged and start <= merged[-1][1]:\n            merged[-1][1] = max(merged[-1][1], end)\n        else:\n            merged.append([start, end])\n    return merged\n",
            [
                {"args": [[[5, 7], [1, 3], [3, 6]]], "expected": [[1, 7]]},
                {"args": [[]], "expected": []},
                {"args": [[[1, 8], [2, 3]]], "expected": [[1, 8]]},
                {"args": [[[4, 5], [1, 2]]], "expected": [[1, 2], [4, 5]]},
                {"args": [[[0, 0], [0, 0]]], "expected": [[0, 0]]},
            ],
        ),
        (
            "middle-value",
            "Calculate the median for even-length lists",
            "median",
            "Return the median of a numeric list, averaging the middle two values when its length is even. Raise ValueError for an empty list.",
            "def median(values):\n    ordered = sorted(values)\n    return ordered[len(ordered)//2]\n",
            "def median(values):\n    if not values:\n        raise ValueError('empty list')\n    ordered = sorted(values)\n    middle = len(ordered)//2\n    return ordered[middle] if len(ordered)%2 else (ordered[middle-1]+ordered[middle])/2\n",
            [
                {"args": [[4, 1, 2, 3]], "expected": 2.5},
                {"args": [[9, 1, 5]], "expected": 5},
                {"args": [[-4, -2]], "expected": -3.0},
                {"args": [[7]], "expected": 7},
                {"args": [[]], "raises": "ValueError"},
            ],
        ),
        (
            "boolean-config",
            "Parse explicit Boolean configuration",
            "parse_bool",
            "Ignore surrounding whitespace and case. Accept true, yes, 1 as True; false, no, 0 as False. Reject other strings with ValueError.",
            "def parse_bool(value):\n    return bool(value)\n",
            "def parse_bool(value):\n    normalized = value.strip().lower()\n    if normalized in {'true', 'yes', '1'}:\n        return True\n    if normalized in {'false', 'no', '0'}:\n        return False\n    raise ValueError('unknown Boolean')\n",
            [
                {"args": [" false "], "expected": False},
                {"args": ["YES"], "expected": True},
                {"args": ["0"], "expected": False},
                {"args": ["1"], "expected": True},
                {"args": ["No"], "expected": False},
                {"args": ["maybe"], "raises": "ValueError"},
                {"args": [""], "raises": "ValueError"},
            ],
        ),
        (
            "count-occurrences",
            "Count repeated values",
            "frequencies",
            "Return a dictionary counting occurrences of each string in a list. Preserve exact spelling and support empty input.",
            "def frequencies(values):\n    return {value: 1 for value in values}\n",
            "def frequencies(values):\n    counts = {}\n    for value in values:\n        counts[value] = counts.get(value, 0) + 1\n    return counts\n",
            [
                {"args": [["a", "b", "a", "a"]], "expected": {"a": 3, "b": 1}},
                {"args": [[]], "expected": {}},
                {"args": [["A", "a"]], "expected": {"A": 1, "a": 1}},
                {"args": [["", ""]], "expected": {"": 2}},
            ],
        ),
        (
            "bounded-value",
            "Clamp values to inclusive bounds",
            "clamp",
            "Clamp a numeric value to inclusive lower and upper bounds. Raise ValueError if lower is greater than upper.",
            "def clamp(value, lower, upper):\n    return min(value, upper)\n",
            "def clamp(value, lower, upper):\n    if lower > upper:\n        raise ValueError('reversed bounds')\n    return max(lower, min(value, upper))\n",
            [
                {"args": [-5, 0, 10], "expected": 0},
                {"args": [15, 0, 10], "expected": 10},
                {"args": [4, 0, 10], "expected": 4},
                {"args": [5, 2, 2], "expected": 2},
                {"args": [5, 10, 0], "raises": "ValueError"},
            ],
        ),
        (
            "one-level-flatten",
            "Flatten exactly one level",
            "flatten_once",
            "Flatten a list of lists by exactly one level, keeping order, duplicates, and deeper nested lists.",
            "def flatten_once(groups):\n    return [group[0] for group in groups if group]\n",
            "def flatten_once(groups):\n    return [value for group in groups for value in group]\n",
            [
                {"args": [[[1, 2], [3, 4]]], "expected": [1, 2, 3, 4]},
                {"args": [[[], [1], []]], "expected": [1]},
                {"args": [[]], "expected": []},
                {"args": [[[[1]], [2, 2]]], "expected": [[1], 2, 2]},
            ],
        ),
    ]
    scenarios = []
    for identifier, name, function, objective, base, reference, cases in definitions:
        scenarios.append(
            _scenario(
                f"coding-{identifier}",
                name,
                "coding",
                objective
                + f" Edit solution.py and return a JSON object with files mapping paths to complete UTF-8 source. Keep the public function {function}.",
                {"files": {"solution.py": base}},
                {"files": {"solution.py": reference}},
                [
                    _check(
                        "function-cases",
                        "Hidden function behavior",
                        "python_tests",
                        "files",
                        {
                            "files": {"solution.py": base},
                            "tests": "",
                            "entrypoint": f"solution:{function}",
                            "cases": cases,
                        },
                    )
                ],
                [{"files": {"solution.py": base}}],
                ["original-fixture", "python", "function-cases"],
            )
        )
    return Collection(
        id="starter-coding",
        name="Python repair workshop",
        domain="coding",
        version="1.0.0",
        created_at=_CREATED,
        description="Ten original function-repair controls. Expected outputs stay outside the verifier.",
        scenarios=scenarios,
    )


def _documents() -> Collection:
    cases: list[dict[str, Any]] = [
        {
            "id": "contract-notice",
            "name": "Read a contract notice requirement",
            "instruction": "Extract the binding renewal-notice requirements. Return notice_days as an integer, delivery as a string, consequence as a string, and citations as an array of objects with string source_id and quote fields. For delivery, copy only the delivery-method noun phrase from the binding source in lowercase, without an article or final punctuation. For consequence, copy only the lowercase time expression saying when a late notice takes effect; omit leading prepositions and articles, and omit sentence punctuation. Do not return a full sentence in either string. Quotes must be exact source substrings with their original casing. Cite the binding source. Additional output fields are ignored.",
            "sources": [
                {
                    "id": "agreement-7",
                    "text": "Either party may prevent automatic renewal by giving written notice at least 45 days before the renewal date. Notice must be delivered by registered mail. A late notice takes effect at the following renewal date.",
                },
                {
                    "id": "marketing-faq",
                    "text": "Our support team usually replies to cancellation questions within two business days. Support emails do not amend the agreement.",
                },
            ],
            "reference": {
                "notice_days": 45,
                "delivery": "registered mail",
                "consequence": "following renewal date",
                "citations": [
                    {
                        "source_id": "agreement-7",
                        "quote": "A late notice takes effect at the following renewal date.",
                    }
                ],
            },
            "checks": [
                _check("notice", "Notice deadline", "numeric", "notice_days", 45),
                _check(
                    "delivery", "Binding delivery method", "equals", "delivery", "registered mail"
                ),
                _check(
                    "consequence",
                    "Late-notice consequence",
                    "equals",
                    "consequence",
                    "following renewal date",
                ),
                _check(
                    "citation",
                    "Agreement citation and verbatim quote",
                    "citation",
                    "citations",
                    {"source_ids": ["agreement-7"], "require_quote": True},
                ),
            ],
            "wrong": {"notice_days": 2},
        },
        {
            "id": "incident-obligations",
            "name": "Identify incident response obligations",
            "instruction": "Read the incident policy using only mandatory requirements. Return notify_within_hours as an integer; notify_role as the exact lowercase role noun phrase from the policy, without an article; required_records as an alphabetically sorted array of the exact lowercase record-field phrases from the policy; and citations as an array of objects containing string source_id and quote fields. Quotes must be exact policy substrings with their original casing. Do not add explanations or punctuation to the role or record-field strings. Additional output fields are ignored.",
            "sources": [
                {
                    "id": "security-policy",
                    "text": "On confirming a customer-data incident, the responder must notify the security lead within 4 hours. The incident record must include affected systems, discovery time, and containment actions. A public statement is not required unless approved by the communications owner.",
                },
            ],
            "reference": {
                "notify_within_hours": 4,
                "notify_role": "security lead",
                "required_records": ["affected systems", "containment actions", "discovery time"],
                "citations": [
                    {
                        "source_id": "security-policy",
                        "quote": "The incident record must include affected systems, discovery time, and containment actions.",
                    }
                ],
            },
            "checks": [
                _check(
                    "deadline",
                    "Required notification deadline",
                    "numeric",
                    "notify_within_hours",
                    4,
                ),
                _check("role", "Accountable recipient", "equals", "notify_role", "security lead"),
                _check(
                    "records",
                    "Complete mandatory record fields",
                    "equals",
                    "required_records",
                    ["affected systems", "containment actions", "discovery time"],
                ),
                _check(
                    "citation",
                    "Policy citation and exact quotation",
                    "citation",
                    "citations",
                    {"source_ids": ["security-policy"], "require_quote": True},
                ),
            ],
            "wrong": {"required_records": ["public statement"]},
        },
        {
            "id": "retention-conflict",
            "name": "Apply a legal-hold exception",
            "instruction": "Decide whether the support transcript described in the sources can be deleted under the retention policy. Return can_delete as a JSON boolean, reason_code as exactly 'legal_hold' or 'retention_elapsed', policy_owner as a string, and citations as an array of objects with string source_id and quote fields. Copy policy_owner exactly from the value after 'Policy owner:' in the case source, preserving its casing and omitting terminal punctuation. Include at least one citation to the policy source and at least one to the case source; every quote must be an exact substring of its cited source. Additional output fields are ignored.",
            "sources": [
                {
                    "id": "retention",
                    "text": "Support transcripts are deleted after 90 days. An active legal hold suspends deletion until the Legal team releases the hold.",
                },
                {
                    "id": "hold-register",
                    "text": "Transcript T-204 is 100 days old. Legal hold LH-12 is active. Policy owner: Legal.",
                },
            ],
            "reference": {
                "can_delete": False,
                "reason_code": "legal_hold",
                "policy_owner": "Legal",
                "citations": [
                    {
                        "source_id": "retention",
                        "quote": "An active legal hold suspends deletion until the Legal team releases the hold.",
                    },
                    {"source_id": "hold-register", "quote": "Legal hold LH-12 is active."},
                ],
            },
            "checks": [
                _check("decision", "Hold prevents deletion", "equals", "can_delete", False),
                _check("reason", "Correct exception", "equals", "reason_code", "legal_hold"),
                _check("owner", "Release authority", "equals", "policy_owner", "Legal"),
                _check(
                    "citation",
                    "Both policy and case evidence",
                    "citation",
                    "citations",
                    {"source_ids": ["retention", "hold-register"], "require_quote": True},
                ),
            ],
            "wrong": {"can_delete": True, "reason_code": "retention_elapsed"},
        },
    ]
    scenarios = []
    for item in cases:
        wrong = {**deepcopy(item["reference"]), **item["wrong"]}
        missing_citation = {
            **deepcopy(item["reference"]),
            "citations": [{"source_id": "invented", "quote": "Unsupported"}],
        }
        scenarios.append(
            _scenario(
                f"documents-{item['id']}",
                item["name"],
                "documents",
                item["instruction"],
                {"sources": item["sources"]},
                item["reference"],
                item["checks"],
                [wrong, missing_citation],
                ["original-fixture", "evidence-extraction"],
            )
        )
    return Collection(
        id="starter-documents",
        name="Evidence and policy reading",
        domain="documents",
        description="Three invented document tasks with exact facts and source checks; citation checks do not establish semantic entailment.",
        version="1.1.0",
        created_at=_CREATED,
        scenarios=scenarios,
    )


def _data() -> Collection:
    scenarios = [
        _scenario(
            "data-invoice-reconciliation",
            "Reconcile purchase orders and invoices",
            "data",
            "All amounts are integer cents. Match invoices to purchase orders by po_id. Flag duplicate invoice IDs after their first occurrence; exclude duplicates and invoices with an amount mismatch from payable_total_cents. Return payable_total_cents as an integer and approved_invoice_ids, duplicate_invoice_ids, and mismatch_invoice_ids as arrays of strings. Each array must contain each applicable ID once, sorted lexicographically, preserving the input ID spelling and casing. An eligible first occurrence remains approved even if a later duplicate is excluded. Additional output fields are ignored.",
            {
                "purchase_orders": [
                    {"id": "PO-1", "amount_cents": 12500},
                    {"id": "PO-2", "amount_cents": 8000},
                    {"id": "PO-3", "amount_cents": 3400},
                ],
                "invoices": [
                    {"id": "I-1", "po_id": "PO-1", "amount_cents": 12500},
                    {"id": "I-2", "po_id": "PO-2", "amount_cents": 8500},
                    {"id": "I-1", "po_id": "PO-1", "amount_cents": 12500},
                    {"id": "I-3", "po_id": "PO-3", "amount_cents": 3400},
                ],
            },
            {
                "payable_total_cents": 15900,
                "approved_invoice_ids": ["I-1", "I-3"],
                "duplicate_invoice_ids": ["I-1"],
                "mismatch_invoice_ids": ["I-2"],
            },
            [
                _check(
                    "total",
                    "Only eligible unique invoices counted",
                    "numeric",
                    "payable_total_cents",
                    15900,
                ),
                _check(
                    "approved",
                    "Approved invoice membership",
                    "equals",
                    "approved_invoice_ids",
                    ["I-1", "I-3"],
                ),
                _check(
                    "duplicate", "Duplicate detection", "equals", "duplicate_invoice_ids", ["I-1"]
                ),
                _check(
                    "mismatch",
                    "Amount mismatch detection",
                    "equals",
                    "mismatch_invoice_ids",
                    ["I-2"],
                ),
            ],
            [
                {
                    "payable_total_cents": 36900,
                    "approved_invoice_ids": ["I-1", "I-2", "I-3"],
                    "duplicate_invoice_ids": [],
                    "mismatch_invoice_ids": [],
                }
            ],
            ["original-fixture", "reconciliation"],
        ),
        _scenario(
            "data-weighted-service-level",
            "Calculate request-weighted success",
            "data",
            "Calculate total requests, successful requests, and overall success_rate from these shards. Do not average shard percentages. Return total_requests and successful_requests as integers, success_rate as a JSON number representing a fraction from 0 to 1 accurate within 0.000001, and meets_target as a JSON boolean for a target of at least 0.98. Do not return numeric strings or a percentage. Additional output fields are ignored.",
            {
                "shards": [
                    {"name": "east", "requests": 900, "successful": 891},
                    {"name": "west", "requests": 100, "successful": 90},
                ]
            },
            {
                "total_requests": 1000,
                "successful_requests": 981,
                "success_rate": 0.981,
                "meets_target": True,
            },
            [
                _check(
                    "requests", "Complete request denominator", "numeric", "total_requests", 1000
                ),
                _check(
                    "successful",
                    "Complete success numerator",
                    "numeric",
                    "successful_requests",
                    981,
                ),
                _check(
                    "rate",
                    "Weighted success fraction",
                    "numeric",
                    "success_rate",
                    0.981,
                    tolerance=0.000001,
                ),
                _check("target", "Threshold decision", "equals", "meets_target", True),
            ],
            [
                {
                    "total_requests": 1000,
                    "successful_requests": 981,
                    "success_rate": 0.945,
                    "meets_target": False,
                }
            ],
            ["original-fixture", "aggregation"],
        ),
        _scenario(
            "data-customer-deduplication",
            "Normalize and deduplicate customer rows",
            "data",
            "Normalize email with trim and lowercase. For each normalized email, keep the row with the greatest updated_at ISO date. Return customers as an array sorted by normalized email. Each customer object must contain exactly email and plan, both strings: use the normalized email and copy plan from the selected row with its original spelling and casing. Do not include updated_at or other fields inside customer objects. Also return input_rows and unique_customers as integers. Additional top-level output fields are ignored.",
            {
                "rows": [
                    {"email": " A@Example.com ", "plan": "basic", "updated_at": "2026-01-01"},
                    {"email": "a@example.com", "plan": "pro", "updated_at": "2026-02-01"},
                    {"email": "B@example.com", "plan": "team", "updated_at": "2026-01-15"},
                    {"email": " b@example.com ", "plan": "basic", "updated_at": "2025-12-01"},
                ]
            },
            {
                "customers": [
                    {"email": "a@example.com", "plan": "pro"},
                    {"email": "b@example.com", "plan": "team"},
                ],
                "input_rows": 4,
                "unique_customers": 2,
            },
            [
                _check(
                    "customers",
                    "Normalized records with newest plans",
                    "equals",
                    "customers",
                    [
                        {"email": "a@example.com", "plan": "pro"},
                        {"email": "b@example.com", "plan": "team"},
                    ],
                ),
                _check("rows", "Input accounting", "numeric", "input_rows", 4),
                _check("unique", "Unique customer accounting", "numeric", "unique_customers", 2),
            ],
            [
                {
                    "customers": [
                        {"email": "a@example.com", "plan": "basic"},
                        {"email": "b@example.com", "plan": "basic"},
                    ],
                    "input_rows": 4,
                    "unique_customers": 2,
                }
            ],
            ["original-fixture", "data-quality"],
        ),
    ]
    return Collection(
        id="starter-data",
        name="Structured data decisions",
        domain="data",
        description="Three invented reconciliation and aggregation tasks with independently specified numeric and membership checks.",
        version="1.1.0",
        created_at=_CREATED,
        scenarios=scenarios,
    )


def _workflow() -> Collection:
    scenarios = [
        _scenario(
            "workflow-refund-approval",
            "Route a refund requiring approval",
            "workflow",
            "Simulate the refund request using the event and approvals. Return final_state containing ticket, refund, and customer, and actions as an ordered array of action-name strings. Preserve every existing field in each state object; do not add or remove fields. A requested refund above 5000 cents requires manager approval before payout. If that approval is required but absent, set both ticket.status and refund.status to 'pending_approval', leave refund.paid_cents unchanged, and return only the action 'request_manager_approval'. Otherwise set ticket.status to 'closed', refund.status to 'paid', refund.paid_cents to the requested integer amount, and return only the action 'pay_refund'. Keep ticket.id and the entire customer record unchanged. Use these exact lowercase status and action names; actions are strings, not objects, and must not include extra bookkeeping actions. Additional top-level output fields are ignored.",
            {
                "initial_state": {
                    "ticket": {"id": "T-10", "status": "open"},
                    "refund": {"status": "none", "paid_cents": 0},
                    "customer": {"id": "C-2", "tier": "standard"},
                },
                "event": {"refund_requested_cents": 7500},
                "approvals": [],
            },
            {
                "final_state": {
                    "ticket": {"id": "T-10", "status": "pending_approval"},
                    "refund": {"status": "pending_approval", "paid_cents": 0},
                    "customer": {"id": "C-2", "tier": "standard"},
                },
                "actions": ["request_manager_approval"],
            },
            [
                _check(
                    "ticket",
                    "Ticket awaits approval",
                    "equals",
                    "final_state.ticket",
                    {"id": "T-10", "status": "pending_approval"},
                ),
                _check(
                    "refund",
                    "No premature payout",
                    "equals",
                    "final_state.refund",
                    {"status": "pending_approval", "paid_cents": 0},
                ),
                _check(
                    "customer",
                    "Unrelated record preserved",
                    "equals",
                    "final_state.customer",
                    {"id": "C-2", "tier": "standard"},
                ),
                _check(
                    "actions",
                    "Required approval transition",
                    "equals",
                    "actions",
                    ["request_manager_approval"],
                ),
            ],
            [
                {
                    "final_state": {
                        "ticket": {"id": "T-10", "status": "closed"},
                        "refund": {"status": "paid", "paid_cents": 7500},
                        "customer": {"id": "C-2", "tier": "standard"},
                    },
                    "actions": ["pay_refund"],
                }
            ],
            ["original-fixture", "state-simulation", "approval"],
        ),
        _scenario(
            "workflow-stock-reservation",
            "Handle a duplicate reservation event",
            "workflow",
            "Process the incoming reservation event exactly once. Return complete final_state and actions as an ordered array of action-name strings. Preserve the state's existing fields and their types: available and reserved are integers, processed_events is an array of event-ID strings. If event_id is already in processed_events, keep all state unchanged, including ledger order, and return actions=['ignore_duplicate']. Otherwise, when stock is sufficient, subtract quantity from available, add it to reserved, append event_id to processed_events, and return actions=['reserve_stock']. If stock is insufficient, keep state unchanged and return actions=['reject_insufficient_stock']. Use the exact action strings, not objects; do not add state fields or extra actions. Additional top-level output fields are ignored.",
            {
                "initial_state": {"available": 6, "reserved": 4, "processed_events": ["E-1"]},
                "event": {"event_id": "E-1", "quantity": 4},
            },
            {
                "final_state": {"available": 6, "reserved": 4, "processed_events": ["E-1"]},
                "actions": ["ignore_duplicate"],
            },
            [
                _check(
                    "available",
                    "Duplicate does not consume stock",
                    "numeric",
                    "final_state.available",
                    6,
                ),
                _check(
                    "reserved",
                    "Duplicate does not increase reservations",
                    "numeric",
                    "final_state.reserved",
                    4,
                ),
                _check(
                    "ledger",
                    "Event ledger remains stable",
                    "equals",
                    "final_state.processed_events",
                    ["E-1"],
                ),
                _check("action", "Duplicate transition", "equals", "actions", ["ignore_duplicate"]),
            ],
            [
                {
                    "final_state": {
                        "available": 2,
                        "reserved": 8,
                        "processed_events": ["E-1", "E-1"],
                    },
                    "actions": ["reserve_stock"],
                }
            ],
            ["original-fixture", "state-simulation", "idempotency"],
        ),
        _scenario(
            "workflow-access-expiration",
            "Revoke an expired temporary grant",
            "workflow",
            "At current_time, revoke temporary grants whose expires_at is at or before current_time. An expires_at value of null denotes a permanent grant. Preserve unexpired and permanent grants. Return final_state.grants as an array in input order; preserve every grant object's existing keys and values except set status to the exact lowercase string 'revoked' for an expired grant. Do not add metadata fields or reformat timestamps. Return actions as an array of strings, one 'revoke:<id>' per revoked grant in input order, substituting its unchanged ID. Do not return action objects or extra actions. Additional top-level output fields are ignored.",
            {
                "current_time": "2026-03-01T12:00:00Z",
                "initial_state": {
                    "grants": [
                        {"id": "G-1", "status": "active", "expires_at": "2026-03-01T12:00:00Z"},
                        {"id": "G-2", "status": "active", "expires_at": "2026-03-02T12:00:00Z"},
                        {"id": "G-3", "status": "active", "expires_at": None},
                    ]
                },
            },
            {
                "final_state": {
                    "grants": [
                        {"id": "G-1", "status": "revoked", "expires_at": "2026-03-01T12:00:00Z"},
                        {"id": "G-2", "status": "active", "expires_at": "2026-03-02T12:00:00Z"},
                        {"id": "G-3", "status": "active", "expires_at": None},
                    ]
                },
                "actions": ["revoke:G-1"],
            },
            [
                _check(
                    "grants",
                    "Expiry boundary and unrelated grants",
                    "equals",
                    "final_state.grants",
                    [
                        {"id": "G-1", "status": "revoked", "expires_at": "2026-03-01T12:00:00Z"},
                        {"id": "G-2", "status": "active", "expires_at": "2026-03-02T12:00:00Z"},
                        {"id": "G-3", "status": "active", "expires_at": None},
                    ],
                ),
                _check("actions", "Exact revocation scope", "equals", "actions", ["revoke:G-1"]),
            ],
            [
                {
                    "final_state": {
                        "grants": [
                            {"id": "G-1", "status": "active", "expires_at": "2026-03-01T12:00:00Z"},
                            {"id": "G-2", "status": "active", "expires_at": "2026-03-02T12:00:00Z"},
                            {"id": "G-3", "status": "active", "expires_at": None},
                        ]
                    },
                    "actions": [],
                }
            ],
            ["original-fixture", "state-simulation", "least-privilege"],
        ),
    ]
    return Collection(
        id="starter-workflow",
        name="Business workflow simulations",
        domain="workflow",
        description="Three final-state simulations. They do not connect to live applications or verify actual tool actions.",
        version="1.1.0",
        created_at=_CREATED,
        scenarios=scenarios,
    )


def starter_collections() -> list[Collection]:
    """Return fresh models so callers cannot mutate a shared fixture registry."""
    return [_coding(), _documents(), _data(), _workflow()]
