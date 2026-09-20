# QueryLab

## What this project is about

QueryLab is a query optimization and plan-evaluation engine that makes physical
planning decisions measurable. It models the core path from SQL parsing and
catalog statistics through plan generation, I/O cost estimation, paged
execution, and result analysis. Its implementations of scans, joins, sorting,
indexes, and buffer management are kept explicit so each plan can be inspected
from estimate through execution.

QueryLab generates `customers`, `orders`, and `products` tables, stores their
rows in simulated pages, and gathers catalog statistics. For each supported SQL
query, it:

1. creates alternative access paths, join orders, and join algorithms;
2. estimates the page I/O cost of every candidate;
3. chooses the plan with the lowest estimated I/O;
4. executes the candidates through an LRU buffer pool;
5. reports estimated versus actual rows and page I/O; and
6. shows whether another plan was actually cheaper.

This creates a hands-on environment for exploring why optimizers make mistakes:
skewed data, correlated columns, stale statistics, limited memory, index
clustering, table page layout, and buffer reuse can all make reality differ
from a textbook estimate.

The project is built around three questions:

1. What did the optimizer expect each operator to do?
2. How many rows and page I/O operations did execution actually produce?
3. Did the optimizer choose the plan with the lowest actual I/O?

The third question is measured with **optimizer regret**. A cardinality estimate
can be very inaccurate without causing harm if the optimizer still chooses the
best plan. Regret identifies estimation errors that lead to a worse plan.

The complete design notes are in [query_evaluator.md](./query_evaluator.md).

## Cost model

QueryLab deliberately uses page I/O as its only optimization cost:

```text
estimated I/O = estimated page reads + estimated page writes

actual I/O = buffer-pool misses + temporary page writes
```

A buffer-pool hit costs zero because the page is already in simulated memory.
A miss costs one because the page must be loaded from simulated storage.
External sorting and partitioned hash joins can also write temporary pages.

CPU counters such as comparisons, hash operations, and predicate evaluations
are available as educational diagnostics, but they do not affect plan ranking.
Wall-clock Python runtime is not used as the query cost.

Regret is:

```text
regret =
    (chosen plan actual I/O - best plan actual I/O)
    / best plan actual I/O
```

Zero regret means the optimizer chose an actual-I/O winner. Multiple plans may
tie for the lowest actual I/O.

## Architecture

```text
┌─────────────┐
│  SQL Query  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ SQL Parser  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐       ┌─────────────────┐
│ Query Optimizer │◄──────│ Catalog + Stats │
└────────┬────────┘       └─────────────────┘
         │
         ▼
┌─────────────────┐
│ Candidate Plans │
└────────┬────────┘
         │
         ▼
┌─────────────────┐       ┌─────────────────┐
│ Plan Evaluator  │◄──────│ Pages + Buffer  │
└────────┬────────┘       └─────────────────┘
         │
         ▼
┌─────────────────┐
│ Results + Regret│
└─────────────────┘
```

### Query processing flow

1. The parser converts supported SQL into a small logical query model.
2. The catalog supplies table, column, histogram, page, and index statistics.
3. The plan generator enumerates access paths, left-deep join orders, and join
   algorithms.
4. The cost estimator predicts page I/O and ranks the candidate plans.
5. The evaluator gives each candidate a fresh buffer pool, executes it, and
   meters actual page I/O.
6. QueryLab verifies that every executed candidate returns the same result.
7. The report compares estimates with actual measurements and calculates
   regret.

## Storage and generated data

The default full-size data configuration is:

| Table | Columns | Rows | Data pages |
|---|---|---:|---:|
| `customers` | `id`, `region`, `tier`, `signup_year` | 10,000 | 100 |
| `orders` | `id`, `customer_id`, `product_id`, `amount`, `order_date` | 200,000 | 2,000 |
| `products` | `id`, `category`, `price` | 1,000 | 10 |

The default layout has 100 tuples per page. Page counts can also be overridden
directly, allowing the same rows and distributions to be tested under different
physical layouts.

The generator supports:

- deterministic random seeds;
- uniform or skewed customer references;
- correlated `region` and `tier` values;
- configurable row counts and tuples per page;
- direct per-table page-count overrides;
- clustered and unclustered indexes.

The CLI uses a smaller learning configuration by default: 100 customers, 2,000
orders, and 50 products. This keeps exhaustive evaluation of intentionally bad
nested-loop plans interactive.

## Implemented algorithms

The implementations favor named steps, ordinary loops, and short explanatory
comments over compact or clever Python.

### Access paths

- Sequential table scan
- Equality index scan
- Clustered index cost estimation
- Unclustered index cost estimation

### Join algorithms

- Tuple nested-loop join
- Block nested-loop join
- In-memory hash join
- Partitioned Grace hash join when the build input does not fit
- Sort-merge join with duplicate-key groups

### Sorting and result operators

- External merge sort
  - memory-sized run generation;
  - temporary run writes;
  - multiway merge passes.
- Projection
- Grouped `COUNT`, `SUM`, and `AVG`

### Optimizer behavior

- Predicate pushdown
- Sequential versus indexed access paths
- Left-deep join-order enumeration
- Join-algorithm enumeration
- Histogram and uniformity-based selectivity estimates
- Independence assumption for multiple predicates
- Frozen statistics for stale-statistics experiments
- Estimated-I/O ranking with stable tie-breaking

## Supported SQL

The initial parser supports:

- `SELECT`
- column projection
- `COUNT`, `SUM`, and `AVG`
- `FROM`
- table aliases
- inner equi-joins
- up to three tables
- `WHERE` comparisons using `=`, `<`, `<=`, `>`, and `>=`
- predicates joined by `AND`
- `GROUP BY`

The following features are intentionally deferred:

- subqueries
- outer joins
- `NULL`
- `DISTINCT`
- window functions
- updates and deletes

## Project structure

```text
querylab/
  catalog/       table, column, and histogram statistics
  datagen/       deterministic demonstration data
  executor/      scans, joins, sorting, aggregation, and metrics
  optimizer/     selectivity, cost estimation, and plan generation
  parser/        supported SQL parser
  report/        candidate comparison, correctness, and regret
  storage/       pages, tables, indexes, and LRU buffer pool
  cli.py         command-line interface
  database.py    tables and indexes owned by one database
  experiments.py repeatable layout and filtering experiments
tests/
```

## Requirements

- Python 3.11 or newer
- No runtime third-party packages

The current implementation was validated with Python 3.12.10.

## Running QueryLab

### Default exhaustive evaluation

```powershell
python -m querylab.cli evaluate
```

### Custom query

```powershell
python -m querylab.cli evaluate "SELECT c.region, SUM(o.amount) FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.region"
```

### Layout and filtering experiments

```powershell
python -m querylab.cli experiment
```

### Full-size generated data

Use `--chosen-only` when working with the full data size. Exhaustively executing
an intentionally poor tuple nested-loop plan can require billions of
comparisons.

```powershell
python -m querylab.cli evaluate `
  --customers 10000 `
  --orders 200000 `
  --products 1000 `
  --chosen-only
```

### Tests

```powershell
python -m unittest discover -s tests -v
```

## Evaluation results

The following results were generated with seed `42`. Page I/O is deterministic
for these configurations.

### Evaluation 1: default exhaustive two-table query

Configuration:

```text
customers = 100 rows
orders = 2,000 rows
products = 50 rows
buffer frames = 16
```

Query:

```sql
SELECT c.region, SUM(o.amount)
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE c.tier = 'gold'
GROUP BY c.region;
```

Chosen plan:

```text
Aggregate(
  HashJoin(
    SeqScan(customers AS c),
    SeqScan(orders AS o)
  )
)
```

Chosen-plan operators:

| Operator | Estimated rows | Actual rows | Estimated I/O | Actual I/O |
|---|---:|---:|---:|---:|
| `SeqScan(customers AS c)` | 5 | 25 | 1 | 1 |
| `SeqScan(orders AS o)` | 2,000 | 2,000 | 20 | 20 |
| `HashJoin` | 100 | 527 | 21 | 0 additional |
| `Aggregate` | 100 | 3 | 21 cumulative | 0 additional |

Candidate ranking by actual I/O:

| Rank | Plan | Estimated I/O | Actual I/O |
|---:|---|---:|---:|
| 1 | Hash join, customers then orders | 21 | 21 |
| 2 | Block nested loop, customers then orders | 21 | 21 |
| 3 | Hash join, orders then customers | 21 | 21 |
| 4 | Block nested loop, orders then customers | 22 | 21 |
| 5 | Nested loop, orders then customers | 2,020 | 21 |
| 6 | Hash join with customer tier index | 26 | 22 |
| 7 | Block nested loop with customer tier index | 26 | 22 |
| 8 | Reverse hash join with customer tier index | 26 | 22 |
| 9 | Reverse block nested loop with customer tier index | 27 | 22 |
| 10 | Reverse nested loop with customer tier index | 2,025 | 22 |
| 11 | Sort-merge, customers then orders | 101 | 81 |
| 12 | Sort-merge, orders then customers | 101 | 81 |
| 13 | Sort-merge with customer tier index | 106 | 82 |
| 14 | Reverse sort-merge with customer tier index | 106 | 82 |
| 15 | Nested loop, customers then orders | 101 | 501 |
| 16 | Nested loop with customer tier index | 106 | 502 |

**Result:** regret was **0.0%**.

The estimate expected only five gold customers, while execution found 25. The
chosen hash join still tied for the lowest actual I/O, so this cardinality error
did not cause optimizer regret.

The reverse nested-loop plan is also instructive. Its estimate was 2,020 I/O,
but its actual cost was only 21 because the one-page `customers` table remained
in the buffer pool during repeated probes. This demonstrates why buffer reuse
can make a textbook estimate pessimistic.

### Evaluation 2: physical page layouts and filtering

Command:

```powershell
python -m querylab.cli experiment
```

| Evaluation | Customer pages | Order pages | Estimated I/O | Actual I/O | Regret |
|---|---:|---:|---:|---:|---:|
| Customers-wide, filtered | 50 | 100 | 106 | 123 | 0.0% |
| Customers-wide, unfiltered | 50 | 100 | 150 | 150 | 0.0% |
| Orders-wide, filtered | 10 | 200 | 206 | 211 | 0.5% |
| Orders-wide, unfiltered | 10 | 200 | 210 | 210 | 0.0% |

These evaluations keep row counts fixed while changing how rows are packed into
pages.

Important observations:

- A filter over a sequential scan does not avoid reading the table's pages.
- A filter can still reduce rows passed into downstream joins and aggregates.
- A usable selective index may avoid reading irrelevant data pages.
- Making `orders` physically wider increased scan I/O from 100 to 200 pages.
- The filtered orders-wide case produced 0.5% regret, demonstrating that a
  small estimation difference can alter the actual winner.

### Evaluation 3: single-table access-path choice

Configuration:

```text
customers = 20 rows
customer data pages = 1
buffer frames = 16
```

Query:

```sql
SELECT c.id
FROM customers c
WHERE c.tier = 'gold';
```

| Plan | Estimated rows | Actual rows | Estimated I/O | Actual I/O |
|---|---:|---:|---:|---:|
| Sequential scan, chosen | 1 | 4 | 1 | 1 |
| Unclustered tier index | 1 | 4 | 2 | 2 |

**Result:** regret was **0.0%**.

Even though the predicate is selective, the entire table fits on one page.
Reading that page sequentially is cheaper than traversing an index and then
fetching matching data. A filter does not automatically imply that an index is
the best access path.

### Evaluation 4: three-table query

Configuration:

```text
customers = 10 rows
orders = 30 rows
products = 5 rows
buffer frames = 16
chosen plan only
```

Query:

```sql
SELECT c.region, SUM(o.amount)
FROM customers c
JOIN orders o ON c.id = o.customer_id
JOIN products p ON o.product_id = p.id
WHERE p.category = 'books'
GROUP BY c.region;
```

Chosen plan:

```text
Aggregate(
  HashJoin(
    HashJoin(
      SeqScan(customers AS c),
      SeqScan(orders AS o)
    ),
    SeqScan(products AS p)
  )
)
```

| Operator | Estimated rows | Actual rows | Estimated I/O | Actual I/O |
|---|---:|---:|---:|---:|
| `SeqScan(customers AS c)` | 10 | 10 | 1 | 1 |
| `SeqScan(orders AS o)` | 30 | 30 | 1 | 1 |
| First `HashJoin` | 30 | 30 | 2 | 0 additional |
| `SeqScan(products AS p)` | 1 | 1 | 1 | 1 |
| Second `HashJoin` | 6 | 9 | 3 | 0 additional |
| `Aggregate` | 6 | 4 | 3 cumulative | 0 additional |

**Result:** the chosen plan used **3 actual page I/O operations**. Regret is
reported as **0.0%** because this run intentionally executed only the chosen
plan.

## Interpreting the report

Estimated I/O displayed on a parent plan node is cumulative. Actual I/O shown
for an operator is the additional I/O attributed to that operator:

- scans normally perform the base-table reads;
- an in-memory hash join can add zero I/O after its children have been read;
- an external sort adds temporary reads and writes;
- a partitioned hash join adds partition reads and writes;
- nested-loop rescans can add misses when the inner pages do not stay cached.

The candidate table compares complete-plan totals.

## Current limitations

- Storage and indexes are simulated rather than persisted to files.
- The B+tree index uses an in-memory key-to-row-location mapping while
  preserving measurable simulated traversal I/O.
- Only left-deep join trees are generated.
- Exhaustive execution is intended for learning-sized data sets.
- Wall-clock performance is not a benchmark target.
- SQL semantics are intentionally limited to the documented subset.

These limitations keep the optimizer and physical algorithms visible enough to
study without turning QueryLab into a full production database engine.
