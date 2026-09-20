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

## Contents

- [Cost model](#cost-model)
- [Architecture](#architecture)
- [Storage and generated data](#storage-and-generated-data)
- [How candidate query plans differ](#how-candidate-query-plans-differ)
- [Running QueryLab](#running-querylab)
- [Evaluation results](#evaluation-results)
- [Supported SQL](#supported-sql)

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

## How candidate query plans differ

Different plans return the same rows, but they can reach those rows in very
different ways. QueryLab changes three independent parts of a plan:

1. **Access path:** how each table is read.
2. **Join input order:** which input appears on the left and right.
3. **Join algorithm:** how matching rows are found.

The optimizer combines these choices, estimates each combination, and selects
the complete plan with the lowest estimated I/O.

### Query used for the plan comparison

The access paths and join plans in this section are generated for the following
query:

```sql
SELECT c.region, SUM(o.amount)
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE c.tier = 'gold'
GROUP BY c.region;
```

The query:

1. filters `customers` to keep only gold-tier customers;
2. joins those customers to `orders` using
   `customers.id = orders.customer_id`;
3. groups the joined rows by customer region; and
4. calculates the total order amount for each region.

The result is the same for every valid physical plan. What changes is how the
tables are read, which join input is processed first, which join algorithm is
used, and how much page I/O that work requires.

### 1. Access path

| Access path | What it does | I/O tradeoff |
|---|---|---|
| Sequential scan | Reads every data page and tests each row | Predictable; often best when much of the table is needed |
| Clustered index scan | Traverses the index, then reads nearby matching data pages | Effective when matching rows occupy a small page range |
| Unclustered index scan | Traverses the index, then follows row locations to data pages | Effective for very selective filters; scattered matches may cause many reads |

For the predicate `c.tier = 'gold'`, QueryLab compares:

```text
SeqScan(customers): read all customer pages, then apply the filter

IndexScan(customers.tier): find "gold" in the index, then fetch matching rows
```

An index is not automatically better. If the table occupies one page, a
sequential scan costs one read while the index must read an index page and a
data page.

### 2. Join input order

The same join can be written with either input first:

```text
HashJoin(SeqScan(customers), SeqScan(orders))

HashJoin(SeqScan(orders), SeqScan(customers))
```

Input order matters most for nested-loop joins:

```text
NestedLoop(left, right)
```

The left side is the outer input. The right side may be revisited for every
outer row or block. A small right input may remain cached, while repeatedly
reading a large right input can be very expensive.

For hash join, QueryLab builds the hash table from the smaller observed input.
For sort-merge join, both inputs must be ordered by the join key.

### 3. Join algorithm

| Join algorithm | How it works | Main I/O behavior | Usually useful when |
|---|---|---|---|
| Hash join | Build a hash table from the smaller input, then probe it with the other input | No extra I/O if the build side fits; otherwise partitions are written and read | Equality joins with enough memory |
| Block nested loop | Load a block of left pages and scan the right input once per block | Fewer right-side rescans than tuple nested loop | No useful index or hash strategy is available |
| Tuple nested loop | Compare every left row with every right row | Can repeatedly read the right input | The outer input is tiny or the inner input stays cached |
| Sort-merge join | Sort both inputs, then advance through matching key groups | Sorting may create temporary-page reads and writes | Inputs are already sorted or sorted output is useful |

### Plan families checked by the main evaluation

The main two-table evaluation checks these plan families with both left/right
input orders where applicable:

```text
Aggregate(HashJoin(SeqScan(c), SeqScan(o)))
Aggregate(HashJoin(IndexScan(c.tier), SeqScan(o)))

Aggregate(BlockNestedLoop(SeqScan(c), SeqScan(o)))
Aggregate(BlockNestedLoop(IndexScan(c.tier), SeqScan(o)))

Aggregate(NestedLoop(SeqScan(c), SeqScan(o)))
Aggregate(NestedLoop(IndexScan(c.tier), SeqScan(o)))

Aggregate(SortMerge(SeqScan(c), SeqScan(o)))
Aggregate(SortMerge(IndexScan(c.tier), SeqScan(o)))
```

Reversing the inputs produces the other eight candidates, for 16 total plans.
The complete measured list appears in
[Evaluation 1](#evaluation-1-default-exhaustive-two-table-query).

### Other implemented operators and optimizer behavior

- External merge sort with run generation, temporary writes, and multiway
  merge passes
- Projection and grouped `COUNT`, `SUM`, and `AVG`
- Predicate pushdown
- Left-deep join-order enumeration
- Histogram and uniformity-based selectivity estimates
- Independence assumption for multiple predicates
- Frozen statistics for stale-statistics experiments
- Estimated-I/O ranking with stable tie-breaking

## Running QueryLab

### Default exhaustive evaluation

```powershell
python -m querylab.cli evaluate
```

Save the analyzed catalog used by an evaluation:

```powershell
python -m querylab.cli evaluate `
  --save-catalog catalog_snapshots\default.json
```

Load the same statistics in a later run:

```powershell
python -m querylab.cli evaluate `
  --load-catalog catalog_snapshots\default.json
```

A loaded snapshot is frozen. The optimizer continues to use its stored values
even if the generated table data changes, which enables repeatable
stale-statistics experiments.

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

### How to read a query plan

A physical plan is a tree that is read from the bottom up:

```text
Aggregate
└── HashJoin
    ├── SeqScan(customers)
    └── SeqScan(orders)
```

The scan nodes read table rows first. Their output becomes the input to the join.
The join's output then becomes the input to the aggregate.

The plan names used below mean:

- `SeqScan(c)`: read every page of `customers` using alias `c`;
- `IndexScan(c.tier)`: use the index on `customers.tier`;
- `HashJoin(left, right)`: join the two child results with a hash table;
- `BlockNestedLoop(left, right)`: process the left input in memory-sized blocks
  and rescan the right input;
- `NestedLoop(left, right)`: use each left row as the outer row and scan the
  right input;
- `SortMerge(left, right)`: sort both inputs by the join key and merge them;
- `Aggregate(child)`: group the child rows and compute the requested aggregate.

### Evaluation 1: default exhaustive two-table query

Command:

```powershell
python -m querylab.cli evaluate
```

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

In plain language, this query finds gold customers, joins them to their orders,
and returns total order amount by customer region.

#### Catalog statistics used for this decision

The complete analyzed catalog is stored in
[`catalog_snapshots/default.json`](./catalog_snapshots/default.json). It records
table cardinalities, page counts, column statistics, all histogram buckets, and
index metadata.

The optimizer uses these stored table statistics for the main query:

| Table | Catalog rows | Catalog pages | Relevant indexes |
|---|---:|---:|---|
| `customers` | 100 | 1 | `id` clustered, height 1; `tier` unclustered, height 1 |
| `orders` | 2,000 | 20 | `customer_id` unclustered, height 2 |
| `products` | 50 | 1 | Not used by this query |

The relevant column statistics are:

| Column | Distinct values | Minimum | Maximum | Why it is used |
|---|---:|---:|---:|---|
| `customers.tier` | 3 | `bronze` | `silver` | Estimate `tier = 'gold'` |
| `customers.id` | 100 | 1 | 100 | Estimate join cardinality |
| `orders.customer_id` | 100 | 1 | 100 | Estimate join cardinality |

The full JSON contains ten equi-depth histogram buckets per populated column.
The first `customers.tier` bucket whose range contains `gold` has:

```json
{
  "lower": "bronze",
  "upper": "gold",
  "row_count": 10,
  "distinct_count": 2
}
```

From that bucket, the optimizer estimates:

```text
estimated matches in bucket = 10 rows / 2 distinct values = 5
estimated gold selectivity  = 5 / 100 = 0.05
estimated gold customers    = 100 × 0.05 = 5
actual gold customers       = 25
```

It then estimates the equality join:

```text
estimated joined rows
    = filtered customers × orders
      / max(distinct customers.id, distinct orders.customer_id)

    = 5 × 2,000 / max(100, 100)
    = 100 rows
```

Finally, the catalog page counts produce the chosen hash plan's I/O estimate:

```text
customers sequential scan = 1 page
orders sequential scan    = 20 pages
in-memory hash join       = 0 additional pages
aggregate                 = 0 additional pages
------------------------------------------------
estimated total           = 21 page I/O operations
```

The filtered customer input is estimated at one page, which fits within the 14
frames available to the hash join (`16` total frames minus two reserved
frames). That is why the estimator does not add partition I/O to this hash plan.

#### Which plans were generated?

The optimizer varies three physical choices:

| Choice | Options |
|---|---|
| Access path for `customers` | `SeqScan(c)` or `IndexScan(c.tier)` |
| Left and right join inputs | `(customers, orders)` or `(orders, customers)` |
| Join algorithm | hash, block nested loop, tuple nested loop, or sort-merge |

`orders` uses a sequential scan because this query has no indexed equality
predicate on `orders`.

```text
2 customer access paths
× 2 left/right input orders
× 4 join algorithms
= 16 complete candidate plans
```

Every candidate was executed with a fresh 16-frame buffer pool. QueryLab
verified that all 16 plans returned the same result before comparing their I/O.

#### Which plan was chosen?

```text
Aggregate: GROUP BY c.region, SUM(o.amount)
└── HashJoin: c.id = o.customer_id
    ├── SeqScan(customers AS c): apply c.tier = 'gold'
    └── SeqScan(orders AS o)
```

The optimizer chose this plan because it had the lowest estimated total I/O:
21 page operations.

Chosen-plan operators:

| Operator | Estimated rows | Actual rows | Estimated I/O | Actual I/O |
|---|---:|---:|---:|---:|
| `SeqScan(customers AS c)` | 5 | 25 | 1 | 1 |
| `SeqScan(orders AS o)` | 2,000 | 2,000 | 20 | 20 |
| `HashJoin` | 100 | 527 | 21 | 0 additional |
| `Aggregate` | 100 | 3 | 21 cumulative | 0 additional |

#### Where is the filter applied?

All current candidates use **filter pushdown**. The predicate
`c.tier = 'gold'` is evaluated while `customers` is being scanned, before any
join runs:

```text
┌──────────────────────────────────────────┐
│ Scan customers and keep tier = "gold"   │  25 rows out
└────────────────────┬─────────────────────┘
                     │
                     ▼
              ┌─────────────┐
              │ Join orders │
              └─────────────┘
```

A separate join-first plan would look like this:

```text
┌─────────────────────────┐
│ Join customers + orders │  all joined rows
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Keep c.tier = "gold"    │
└─────────────────────────┘
```

QueryLab does **not currently enumerate the second shape**. It always pushes a
safe table predicate down to the scan. Therefore, the 16 measured candidates
differ by access path, input order, and join algorithm—not by filter placement.

For a sequential scan, applying the filter itself adds no page I/O. The scan
must read the one customer page whether it keeps 25 rows or all 100 rows:

```text
Customer sequential scan + filter:
estimated I/O = 1
actual I/O    = 1
```

The filter still matters because only 25 rows, instead of 100, reach the join.
That can greatly reduce repeated scans, hash-table size, sorting, and temporary
I/O in later operators.

Using the unclustered tier index changes the access cost:

```text
Customer tier index + filter:
estimated I/O = 6
actual I/O    = 2
```

The actual two operations are an index-page read and a customer-data-page read.
The estimate assumes matching unclustered rows may require more scattered data
page reads.

#### Plan diagrams and join costs

The diagrams below use the same query, rows, pages, and 16-frame buffer pool.
The join cost shown is **additional I/O after its two inputs have been read**.

##### Plan A: filter first, then hash join — chosen

```text
┌─────────────────────────────┐
│ Aggregate by region         │  +0 actual I/O
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│ Hash join on customer_id    │  +0 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Customers scan │  │ Orders scan         │
│ + gold filter  │  │                     │
│ 1 actual I/O   │  │ 20 actual I/O       │
└─────────────────┘  └─────────────────────┘

Total: 21 estimated I/O, 21 actual I/O
```

The filtered customer input fits in memory, so the hash join builds and probes
its hash table without additional page reads or writes.

##### Plan B: index filter, then hash join

```text
┌─────────────────────────────┐
│ Aggregate by region         │  +0 actual I/O
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│ Hash join on customer_id    │  +0 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Tier index scan│  │ Orders scan         │
│ + gold filter  │  │                     │
│ 2 actual I/O   │  │ 20 actual I/O       │
└─────────────────┘  └─────────────────────┘

Total: 26 estimated I/O, 22 actual I/O
```

This plan finds gold customers through the index. For this one-page customer
table, the extra index traversal makes it one I/O more expensive than the
sequential-scan hash plan.

##### Plan C: filter first, then block nested-loop join

```text
┌─────────────────────────────┐
│ Block nested-loop join      │  +0 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Customers scan │  │ Orders scan         │
│ + gold filter  │  │                     │
│ 1 actual I/O   │  │ 20 actual I/O       │
└─────────────────┘  └─────────────────────┘

Total: 21 estimated I/O, 21 actual I/O
```

All 25 filtered customer rows fit in one memory block, so `orders` is read only
once. This plan ties the hash join on page I/O, although it performs more row
comparisons.

##### Plan D: filtered customers as the outer tuple loop

```text
┌─────────────────────────────┐
│ Tuple nested-loop join      │  +480 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Customers scan │  │ Orders scan         │
│ + gold filter  │  │ repeated per outer  │
│ 1 actual I/O   │  │ 20 pages per pass   │
└─────────────────┘  └─────────────────────┘

Total: 101 estimated I/O, 501 actual I/O
```

Execution found 25 gold customers. The first orders scan costs 20 I/O and is
already included in the base input cost. The remaining 24 outer rows each cause
another 20-page orders scan:

```text
additional join I/O = 24 × 20 = 480
total actual I/O    = 1 + 20 + 480 = 501
```

##### Plan E: reverse the tuple nested-loop inputs

```text
┌─────────────────────────────┐
│ Tuple nested-loop join      │  +0 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Orders scan    │  │ Customers scan      │
│ outer input    │  │ + gold filter       │
│ 20 actual I/O  │  │ 1 page, stays cached│
└─────────────────┘  └─────────────────────┘

Total: 2,020 estimated I/O, 21 actual I/O
```

The estimator assumes the customer page may be read once per order. In actual
execution, that one page remains in the 16-frame buffer pool, so every repeated
request is a hit. This is why reversing the inputs changes the result so much.

##### Plan F: filter first, then sort-merge join

```text
┌─────────────────────────────┐
│ Sort-merge join             │  +60 actual I/O
└──────────┬───────────┬──────┘
           │           │
┌──────────▼──────┐  ┌─▼──────────────────┐
│ Sort filtered  │  │ Sort orders by      │
│ customers by id│  │ customer_id         │
└─────────────────┘  └─────────────────────┘

Base scans:          21 actual I/O
Temporary sort I/O:  60 actual I/O
Total:               81 actual I/O
```

The filtered customers fit in memory, but the 2,000 order rows require external
sorting. Run generation and merge passes write and reread temporary pages,
making this plan more expensive than the in-memory hash join.

##### Cost comparison by stage

| Plan | Customer access and filter | Orders scan | Additional join/sort I/O | Estimated total | Actual total |
|---|---:|---:|---:|---:|---:|
| Sequential scan + hash | 1 / 1 | 20 / 20 | 0 / 0 | 21 | 21 |
| Tier index + hash | 6 / 2 | 20 / 20 | 0 / 0 | 26 | 22 |
| Sequential scan + block nested loop | 1 / 1 | 20 / 20 | 0 / 0 | 21 | 21 |
| Sequential scan + tuple nested loop | 1 / 1 | 20 / 20 | 80 / 480 | 101 | 501 |
| Reversed tuple nested loop | 1 / 1 | 20 / 20 | 1,999 / 0 | 2,020 | 21 |
| Sequential scan + sort-merge | 1 / 1 | 20 / 20 | 80 / 60 | 101 | 81 |

Values written as `estimated / actual` show where the estimator differed from
execution.

#### All plans checked and compared

The table starts with the worst measured plan and moves toward the best.
`Left input` and `Right input` show the exact child order in the physical join.

The optimizer does not know actual I/O when it chooses a plan. It selects the
candidate with the **lowest estimated I/O**. Three plans tie at an estimate of
21:

1. hash join with `customers` on the left;
2. block nested-loop join with `customers` on the left; and
3. hash join with `orders` on the left.

QueryLab uses a stable plan ID to break equal estimated costs. It therefore
chooses:

```text
Aggregate(
  HashJoin(
    SeqScan(customers with gold filter),
    SeqScan(orders)
  )
)
```

| Worst → best | Left input | Right input | Join algorithm | Estimated I/O | Actual I/O | Optimizer selected |
|---:|---|---|---|---:|---:|---|
| 1 | `IndexScan(c.tier)` | `SeqScan(o)` | Tuple nested loop | 106 | 502 | |
| 2 | `SeqScan(c)` | `SeqScan(o)` | Tuple nested loop | 101 | 501 | |
| 3 | `IndexScan(c.tier)` | `SeqScan(o)` | Sort-merge | 106 | 82 | |
| 4 | `SeqScan(o)` | `IndexScan(c.tier)` | Sort-merge | 106 | 82 | |
| 5 | `SeqScan(c)` | `SeqScan(o)` | Sort-merge | 101 | 81 | |
| 6 | `SeqScan(o)` | `SeqScan(c)` | Sort-merge | 101 | 81 | |
| 7 | `SeqScan(o)` | `IndexScan(c.tier)` | Tuple nested loop | 2,025 | 22 | |
| 8 | `SeqScan(o)` | `IndexScan(c.tier)` | Block nested loop | 27 | 22 | |
| 9 | `SeqScan(o)` | `IndexScan(c.tier)` | Hash | 26 | 22 | |
| 10 | `IndexScan(c.tier)` | `SeqScan(o)` | Block nested loop | 26 | 22 | |
| 11 | `IndexScan(c.tier)` | `SeqScan(o)` | Hash | 26 | 22 | |
| 12 | `SeqScan(o)` | `SeqScan(c)` | Tuple nested loop | 2,020 | **21** | |
| 13 | `SeqScan(o)` | `SeqScan(c)` | Block nested loop | 22 | **21** | |
| 14 | `SeqScan(o)` | `SeqScan(c)` | Hash | 21 | **21** | |
| 15 | `SeqScan(c)` | `SeqScan(o)` | Block nested loop | 21 | **21** | |
| 16 | `SeqScan(c)` | `SeqScan(o)` | Hash | **21** | **21** | **Yes** |

Rows 12 through 16 tie for the lowest actual I/O. The optimizer-selected hash
plan is therefore one of five actual winners:

```text
chosen estimated I/O = 21
chosen actual I/O    = 21
best actual I/O      = 21
regret               = (21 - 21) / 21 = 0.0%
```

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

Each physical layout runs two queries.

Filtered query:

```sql
SELECT c.region, SUM(o.amount)
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE c.tier = 'gold'
GROUP BY c.region;
```

Unfiltered query:

```sql
SELECT c.region, SUM(o.amount)
FROM customers c
JOIN orders o ON c.id = o.customer_id
GROUP BY c.region;
```

The filtered query compares all 16 plans described in Evaluation 1. Without the
`tier` predicate, the unfiltered query has no customer index access path, so it
compares:

```text
1 customer access path
× 2 left/right input orders
× 4 join algorithms
= 8 complete candidate plans
```

| Evaluation | Candidates | Chosen plan | Actual best plan | Est. I/O | Act. I/O | Regret |
|---|---:|---|---|---:|---:|---:|
| Customers-wide, filtered | 16 | `Hash(IndexScan(c.tier), SeqScan(o))` | Same as chosen | 106 | 123 | 0.0% |
| Customers-wide, unfiltered | 8 | `Hash(SeqScan(c), SeqScan(o))` | Same as chosen | 150 | 150 | 0.0% |
| Orders-wide, filtered | 16 | `Hash(IndexScan(c.tier), SeqScan(o))` | `Hash(SeqScan(c), SeqScan(o))` | 206 | 211 | 0.5% |
| Orders-wide, unfiltered | 8 | `Hash(SeqScan(c), SeqScan(o))` | Same as chosen | 210 | 210 | 0.0% |

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

Command:

```powershell
python -m querylab.cli evaluate `
  "SELECT c.id FROM customers c WHERE c.tier = 'gold'" `
  --customers 20 --orders 100 --products 10
```

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

Command:

```powershell
python -m querylab.cli evaluate `
  "SELECT c.region, SUM(o.amount) FROM customers c JOIN orders o ON c.id = o.customer_id JOIN products p ON o.product_id = p.id WHERE p.category = 'books' GROUP BY c.region" `
  --customers 10 --orders 30 --products 5
```

Configuration:

```text
customers = 10 rows
orders = 30 rows
products = 5 rows
buffer frames = 16
all candidate plans executed
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

In plain language, this query selects products in the `books` category, joins
them to orders and customers, and totals matching order amounts by customer
region.

#### Which three-table plans were generated?

The join graph is:

```text
customers ── customer_id ── orders ── product_id ── products
```

Only connected left-deep orders are valid:

1. `customers → orders → products`
2. `orders → customers → products`
3. `orders → products → customers`
4. `products → orders → customers`

For each order, the optimizer tries:

- sequential or category-index access for `products`;
- one sequential access path for `customers`;
- one sequential access path for `orders`; and
- four algorithms for the first join and four for the second join.

```text
4 valid left-deep table orders
× 2 product access paths
× 4 first-join algorithms
× 4 second-join algorithms
= 128 complete candidate plans
```

All 128 candidates were executed and verified to return the same result.

#### Chosen three-table plan

```text
Aggregate: GROUP BY c.region, SUM(o.amount)
└── HashJoin: o.product_id = p.id
    ├── HashJoin: c.id = o.customer_id
    │   ├── SeqScan(customers AS c)
    │   └── SeqScan(orders AS o)
    └── SeqScan(products AS p): apply p.category = 'books'
```

| Operator | Estimated rows | Actual rows | Estimated I/O | Actual I/O |
|---|---:|---:|---:|---:|
| `SeqScan(customers AS c)` | 10 | 10 | 1 | 1 |
| `SeqScan(orders AS o)` | 30 | 30 | 1 | 1 |
| First `HashJoin` | 30 | 30 | 2 | 0 additional |
| `SeqScan(products AS p)` | 1 | 1 | 1 | 1 |
| Second `HashJoin` | 6 | 9 | 3 | 0 additional |
| `Aggregate` | 6 | 4 | 3 cumulative | 0 additional |

Of the 128 plans:

| Actual total I/O | Number of plans |
|---:|---:|
| 3 | 64 |
| 4 | 64 |

**Result:** the chosen plan used **3 actual page I/O operations**, tied for the
lowest actual cost, and produced **0.0% regret**.

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

## Requirements

- Python 3.11 or newer
- No runtime third-party packages

The current implementation was validated with Python 3.12.10.

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
catalog_snapshots/
  default.json   saved statistics for the main README evaluation
tests/
```
