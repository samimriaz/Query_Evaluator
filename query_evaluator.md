# QueryLab — A Query Optimizer & Evaluator Simulator

A small database engine that does one thing well: it **estimates** the page I/O cost of a query plan, then **actually runs** that plan while metering real page I/O, and shows you how far off the estimate was — and why.

This mirrors the classic architecture (Parser → Optimizer [Plan Generator + Cost Estimator ↔ Catalog Manager] → Plan Evaluator) but instruments every stage so the gap between *estimated* and *actual* is the main output, not a side effect.

---

## 1. Goal

Answer three questions for any query you throw at it:

1. **What did the optimizer think?** Estimated rows, page reads/writes per operator, and the chosen plan.
2. **What actually happened?** Real rows produced, buffer-pool misses, and temporary page writes.
3. **Was the choice right?** Run *every* candidate plan, not just the chosen one, and report the optimizer's *regret* — how much worse the chosen plan was than the true best plan.

Point 3 is the upgrade over the basic "estimate vs actual" idea. Being off by 10× on cardinality is harmless if you still picked the best plan; being off by 20% can be a disaster if it flipped a hash join to a nested loop. Regret measures what matters.

---

## 2. SQL scope

The first version supports a deliberately small, read-only SQL subset: projections, filters joined by `AND`, inner equi-joins, and grouped aggregates over at most three tables.

Explicitly deferred:
- subqueries
- outer joins
- `NULL`
- `DISTINCT`
- window functions
- updates and deletes

These features can be added later, but they are not part of the initial optimizer, executor, or correctness requirements.

---

## 3. Architecture (maps 1:1 to the diagram)

```
SQL text
   │
   ▼
┌─────────────────┐
│  Query Parser    │  tiny SQL subset → logical tree (Select/Project/Join/Agg)
└─────────────────┘
   │ parsed query
   ▼
┌───────────────────────────────────────────────┐        ┌──────────────────┐
│  Query Optimizer                              │        │  Catalog Manager │
│  ┌────────────────┐   ┌────────────────────┐  │◄──────►│  row counts      │
│  │ Plan Generator │   │ Plan Cost Estimator │  │        │  page counts     │
│  │ enumerates     │   │ selectivity +       │  │        │  distinct values │
│  │ access paths,  │   │ page I/O formulas   │  │        │  min/max, hist.  │
│  │ join orders,   │   │ per operator        │  │        │  indexes         │
│  │ join algos     │   │                     │  │        │  (can be STALE)  │
│  └────────────────┘   └────────────────────┘  │        └──────────────────┘
└───────────────────────────────────────────────┘
   │ evaluation plan (physical operator tree)
   ▼
┌─────────────────────┐        ┌──────────────────┐
│ Query Plan Evaluator │◄──────►│ Storage + Buffer │
│ iterator (Volcano)   │        │ Pool (LRU, N     │
│ executes each op,    │        │ frames) — counts │
│ meters real work     │        │ real page reads  │
└─────────────────────┘        └──────────────────┘
   │
   ▼
┌─────────────────────┐
│ Comparison Report    │ estimated vs actual, per operator + total; regret table
└─────────────────────┘
```

---

## 4. Dummy data

Three tables, generated with knobs so you can break the optimizer's assumptions on purpose:

| Table | Columns | Rows (default) |
|---|---|---|
| `customers` | id, region, tier, signup_year | 10,000 |
| `orders` | id, customer_id, product_id, amount, order_date | 200,000 |
| `products` | id, category, price | 1,000 |

Generator knobs:
- **Skew** — Zipfian `customer_id` in `orders` (a few customers place most orders). Breaks the *uniformity* assumption.
- **Correlation** — `region` and `tier` correlated. Breaks the *independence* assumption used for AND predicates.
- **Page size / tuple width** — controls tuples-per-page, so I/O counts are realistic.
- **Table page layout** — derive page counts from page size and tuple width, or directly set the number of data pages per table for controlled experiments.
- **Indexes** — choose which columns get a B+tree, clustered or unclustered.

Storage is simulated: each table is a list of fixed-size *pages*; every access goes through a buffer pool with a configurable frame count and LRU eviction. **Actual I/O cost = buffer-pool misses + temporary page writes.**

The default simplified layout uses 100 tuples per page:

| Table | Rows | Default data pages |
|---|---:|---:|
| `customers` | 10,000 | 100 |
| `orders` | 200,000 | 2,000 |
| `products` | 1,000 | 10 |

The experiment runner can override these values while keeping the row counts and data distributions unchanged. For example:

| Layout | `customers` pages | `orders` pages | What it models |
|---|---:|---:|---|
| A | 500 | 1,000 | Wider customer rows or poorer customer packing; denser order pages |
| B (default) | 100 | 2,000 | Denser customer pages; wider order rows or poorer order packing |

This makes it possible to isolate how physical page layout changes access-path, join-order, and join-algorithm choices.

---

## 5. Catalog Manager

Per table: `n_rows`, `n_pages`, `tuples_per_page`.
Per column: `n_distinct`, `min`, `max`, optional `equi-depth histogram`, index metadata (type, height, clustered?).

Two important features:
- `ANALYZE` recomputes stats from real data.
- Stats can be **frozen** so you can insert/skew data afterwards and watch the optimizer plan on stale numbers — exactly what happens in production.

---

## 6. Plan Cost Estimator (textbook formulas)

Page I/O is the optimizer's only cost metric:

`estimated cost = estimated page reads + estimated page writes`

`actual cost = buffer-pool misses + temporary page writes`

No `io_weight` is needed because CPU work and wall-clock time are not combined with I/O. CPU activity may still be metered for educational diagnostics, but it does not affect plan ranking or regret. If plans have equal estimated I/O, use a stable plan ID as a deterministic tie-breaker.

Selectivity:
- `col = v` → `1 / n_distinct` (or histogram bucket if available)
- `col > v` → `(max − v) / (max − min)`
- `p1 AND p2` → `sel(p1) × sel(p2)` (independence — knowingly wrong on correlated data)
- Join `R ⋈ S` on `R.a = S.b` → `|R| × |S| / max(V(a,R), V(b,S))`

I/O cost per operator (B = pages, M = buffer frames):
| Operator | Estimated I/O |
|---|---|
| Sequential scan | B(R) |
| Index scan (clustered) | height + ⌈sel × B(R)⌉ |
| Index scan (unclustered) | height + sel × \|R\| (one page per match) |
| Nested loop join | B(R) + \|R\| × B(S) |
| Block nested loop | B(R) + ⌈B(R)/(M−2)⌉ × B(S) |
| Index nested loop | B(R) + \|R\| × (index lookup cost) |
| Sort-merge join | sort(R) + sort(S) + B(R) + B(S) |
| Hash join | B(R) + B(S) if the smaller input fits in memory; 3 × (B(R) + B(S)) for one partitioning pass; more if recursive partitioning is needed |
| External sort | 2 × B × ⌈log_{M−1}(B/M)⌉ + B |

Regret is computed from measured I/O:

`regret = (chosen plan actual I/O - best plan actual I/O) / best plan actual I/O`

---

## 7. Plan Generator

For a query on k tables (k ≤ 3 is plenty):
1. Enumerate access paths per table (seq scan vs each usable index).
2. Enumerate left-deep join orders.
3. For each join, try every join algorithm.
4. Push selections down; decide whether to materialize or pipeline.

Total plan space for a 3-table query is a few dozen plans — small enough to **run them all** for the regret analysis.

Filters are included in both estimation and execution:
- A filter over a sequential scan normally does not reduce that scan's page reads; every table page must still be examined.
- A usable index can reduce page reads for a selective filter.
- In either case, fewer rows leaving the filter can reduce the I/O of downstream joins, sorts, and materialized results.
- The experiment runner executes paired filtered and unfiltered queries so these effects can be compared directly.

---

## 8. Plan Evaluator

Volcano-style iterator model (`open() / next() / close()`) so pipelining is real. Each operator is wrapped in a meter that records:
- rows in / rows out
- page fetches requested, buffer-pool misses, and temporary page writes
- comparisons / hashes / predicate evaluations as supplemental diagnostics

The buffer pool is shared across operators in a plan, so the actual I/O reflects cache reuse the estimator didn't model.

---

## 9. Output — the interesting part

For one query:

```
Query: SELECT c.region, SUM(o.amount) FROM customers c JOIN orders o
       ON c.id = o.customer_id WHERE c.tier = 'gold' AND o.amount > 500
       GROUP BY c.region

Chosen plan: HashAgg( HashJoin( IdxScan(customers.tier), SeqScan(orders, amount>500) ) )

Operator                     Est rows   Act rows   Est I/O   Act I/O
────────────────────────────────────────────────────────────────────
IdxScan customers tier=gold     2,500      2,511        61        61
SeqScan orders amount>500      40,000     18,730     2,000     2,000
HashJoin                       10,000      4,703     6,183     6,120
HashAgg                             5          5         0         0
────────────────────────────────────────────────────────────────────
TOTAL                                                8,244     8,181

Why the estimate was off:
  • orders.amount: uniform assumption, actual distribution is right-skewed (2.1× over-estimate)
  • Join cardinality propagated that error downstream

Regret analysis (all 14 candidate plans executed):
  Rank  Plan                                               Est I/O    Actual I/O
  1     HashJoin(IdxScan(c), SeqScan(o))   ← chosen        8,244        8,181   ✔ optimal
  2     SortMerge(IdxScan(c), SeqScan(o))                  9,910        9,832
  ...
  14    NestedLoop(SeqScan(o), SeqScan(c))            2,000,100    1,998,300
  Regret: 0%  (optimizer picked the true best plan)
```

Now flip a knob (`--skew 1.2 --stale-stats`) and rerun: the estimate is off by 50×, the optimizer picks an index nested loop, and regret jumps to 340%. That contrast is the whole point of the project.

---

## 10. Experiments the project should make easy

1. **Uniformity vs reality** — same query, skewed vs uniform data. Histogram on/off.
2. **Independence assumption** — correlated predicates, see selectivity error compound.
3. **Stale statistics** — freeze catalog, bulk-insert, replan.
4. **Buffer pool size** — watch block nested loop and hash join flip in relative cost as M changes.
5. **Clustered vs unclustered index** — where does the index stop being worth it? (find the selectivity crossover empirically and compare to the estimator's crossover).
6. **Join order sensitivity** — 3-table query where one wrong join order is 100× worse.
7. **Physical page layout** — run the same query with layouts such as `(customers=500, orders=1,000)` and `(customers=100, orders=2,000)` while keeping row counts fixed.
8. **Filtered vs unfiltered** — compare full scans, selective filters without indexes, and selective filters with clustered or unclustered indexes.

---

## 11. Suggested implementation

**Language:** Python (fast to write, readable; performance is irrelevant since we're counting simulated pages, not wall time). Optional TypeScript/React front end later.

**Code readability requirements:**
- Prefer straightforward loops, conditionals, and small named functions over compressed one-liners or clever abstractions.
- Use descriptive names for rows, pages, buffers, partitions, runs, and intermediate results.
- Keep each physical algorithm easy to find and follow from start to finish.
- Add short docstrings that explain an algorithm's purpose, inputs, output, memory assumptions, and I/O behavior.
- Comment the important phases and decisions inside algorithms, especially hash join, block nested-loop join, sort-merge join, external merge sort, and index lookup.
- Explain *why* a step is needed rather than commenting every obvious line.
- Avoid metaprogramming, deeply nested comprehensions, unnecessary decorators, and abstractions that hide the algorithm being taught.
- Use type hints and small data classes where they improve clarity, but do not sacrifice readability to make the code shorter.

The implementation is educational first. For example, hash join should visibly show the build and probe phases, and external merge sort should visibly show run generation and merge passes. These steps should not be hidden behind compact library calls.

**Package layout:**
```
querylab/
  storage/     pages.py, buffer_pool.py, table.py, btree_index.py
  catalog/     stats.py, histogram.py, catalog.py
  parser/      sql_parser.py, logical_plan.py
  optimizer/   plan_generator.py, cost_estimator.py, selectivity.py
  executor/    operators.py (scan, filter, project, joins, sort, agg), meter.py
  report/      compare.py, regret.py, render.py (text / markdown / HTML)
  datagen/     generate.py (skew, correlation, size knobs)
  cli.py
tests/
```

**Milestones:**
1. Storage + buffer pool + data generator with metering (1 table, seq scan). *You can already show est vs actual I/O here.*
2. Catalog + selectivity + index scans.
3. Two-table joins: all algorithms, estimator formulas, plan enumeration.
4. Comparison report + regret analysis.
5. Knobs & experiments; write up findings.
6. (Optional) Web UI: paste SQL, see plan tree with est/actual badges on each node, slider for buffer pool size.

**Running the Python core:**
```powershell
# Compare every candidate plan for the built-in learning-size data set.
python -m querylab.cli evaluate

# Supply another query from the supported SQL subset.
python -m querylab.cli evaluate "SELECT c.region, SUM(o.amount) FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.region"

# Compare alternate physical page layouts with filtered and unfiltered queries.
python -m querylab.cli experiment

# Generate the full documented row counts. Use --chosen-only when a plan such
# as nested-loop join would make exhaustive execution intentionally expensive.
python -m querylab.cli evaluate --customers 10000 --orders 200000 --products 1000 --chosen-only
```

The CLI defaults to a smaller learning data set (`100` customers, `2,000` orders, and `50` products) so every candidate, including intentionally bad nested-loop plans, can be executed interactively. `DataConfig` retains the full documented defaults for larger experiments.

---

## 12. Alternative / extension ideas

If you want to push past the basic simulator, these build directly on it:

**A. Feedback-driven optimizer (recommended extension).** After each execution, write the *actual* cardinalities back into the catalog ("learned" selectivity, like SQL Server's cardinality feedback or Oracle's adaptive statistics). Show regret dropping over successive runs of a workload. This turns a static demo into a story about a system improving itself.

**B. Adaptive execution.** Let the evaluator switch join algorithm mid-flight when it notices the observed row count has blown past the estimate by 10× (like PostgreSQL's proposed adaptive joins / Oracle adaptive plans). Compare "static plan" vs "adaptive plan" regret.

**C. Optimizer arena.** Implement two or three estimator strategies (naive uniform, histogram-based, sampling-based) and race them across a generated workload. Output a leaderboard by total regret. Good for understanding *why* real systems invest in histograms and sampling.

**D. "Explain, but honest."** Generate an EXPLAIN ANALYZE-style visual plan tree where each node is colored by estimate error, with a hover explaining which assumption failed. This is the most demo-friendly version if you want to show it to others.

My recommendation: build sections 1–11 as the core, then add **A** — it's the smallest addition with the biggest payoff, and it reframes the project from "look how wrong estimators are" to "here's how a database learns from its mistakes."