# Task Split Manifest v2 Design

> Status: partial freeze. Existing 40 IDs/splits are frozen; 40 new IDs remain PENDING catalog
> implementation and user-approved catalog hash.

## 1. Audited current catalog

- Source: `configs/task_templates.yaml`
- SHA-256 recorded by Day 7--13:
  `8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`
- 40 templates, 10/domain; 24/8/8 Train/Validation/Test.
- 200 generated instances, 120/40/40; five/template.
- All 200 current instances have exactly one reference step.
- No template crosses split; all template families occur in Train.

### Existing Train (24)

```text
data_filter_category_eq
data_filter_amount_gt
data_filter_score_gte
data_filter_latency_lt
data_aggregate_units_sum
data_aggregate_latency_mean
file_list_project_tree
file_list_reports
file_search_project_code
file_search_casefold
file_read_exact_note
file_read_multiline
api_inventory_primary
api_inventory_backup
api_inventory_low_stock
api_order_pending_read
api_order_processing_read
api_order_start_processing
logic_calculate_addition
logic_calculate_subtraction
logic_calculate_multiplication
logic_calculate_division
logic_convert_metres_kilometres
logic_validate_tolerance
```

### Existing Validation (8)

```text
data_filter_priority_lte
data_aggregate_events_count
file_search_line_position
file_list_nested_assets
api_inventory_bulk_stock
api_order_shipped_read
logic_calculate_parentheses
logic_convert_kilometres_metres
```

### Existing Test (8)

```text
data_filter_inventory_gt
data_aggregate_revenue_sum
file_search_multiple
file_read_unicode
api_order_ship
api_order_cancel
logic_calculate_power
logic_convert_grams_kilograms
```

These IDs, definitions and splits must not change.

## 2. Current coverage/complexity

| Domain | Families | Existing templates | Multi-step | State-changing | Conditional branch | Intermediate verification |
|---|---|---:|---:|---:|---:|---:|
| Data Analysis | filter, aggregate | 10 | 0 | 0 | 0 | 0 |
| File Retrieval | list, search, read | 10 | 0 | 0 | 0 | 0 |
| API Workflow | inventory, get_order, update_order | 10 | 0 | 3 update templates | 0 | 0 |
| Calculation & Logic | calculator, conversion, validation | 10 | 0 | 0 | 0 | 0 |

Current templates satisfy deterministic single-step environment tests, not the v2 multi-step
research objective.

## 3. New-template allocation contract

| Domain | New Train | New Validation | New Test | New total | Final total |
|---|---:|---:|---:|---:|---:|
| Data Analysis | 6 | 2 | 2 | 10 | 20 |
| File Retrieval | 6 | 2 | 2 | 10 | 20 |
| API Workflow | 6 | 2 | 2 | 10 | 20 |
| Calculation & Logic | 6 | 2 | 2 | 10 | 20 |
| **Total** | **24** | **8** | **8** | **40** | **80** |

Every new template must contain 3--8 genuinely dependent reference operations. A later operation
must consume a previous result or a state transition; splitting one operation into cosmetic steps
does not qualify. Each template requires:

- deterministic success checks and at least one negative verifier probe;
- explicit read/write/idempotency semantics;
- allowed fault locations and Agent-visible response contracts;
- fixed `template_id`, split, seed rule, Prompt/tool/verifier version;
- context-length and max-step feasibility under the B1 limits.

Recommended archetypes are read/filter/aggregate/validate, locate/search/read/cross-check,
query/decide/update/query-verify, and input/multi-step calculate/validate/summarize. Exact IDs and
definitions are intentionally not invented in B1.

## 4. Instance and lineage rule

Formal v2 sampling uses two pre-registered seeds per template, giving 96/32/32 task attempts.
All instances, Checkpoints, repeats, CONTINUE outcomes and future intervention branches inherit
the template split. Test templates are inaccessible to fit/tuning pipelines except for integrity
checks before the one final evaluation.

## 5. Freeze Gate

The full manifest remains `CONDITIONAL` until a v2 catalog artifact proves:

- 80 unique template IDs and 48/16/16 split;
- 20 templates/domain and 6/2/2 new allocation/domain;
- all 40 new templates pass Reference Runner and negative verifier probes;
- 3--8 dependent steps for every new template;
- Domain x Fault x Split schedule is valid and pre-outcome;
- a new catalog hash and split-manifest hash are recorded.

Formal data collection is blocked before this Gate. B2 lifecycle/Restore development may use
existing Train/development templates and does not alter their formal split.
