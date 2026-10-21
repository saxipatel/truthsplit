# Difference Autopsy

How TruthSplit attributes `delta = right − left` to causes, and what each evidence label means. The code is `backend/app/diagnosis/autopsy.py`.

## Data differences vs definition differences

- A **definition difference** means the two metrics count different things from the same kind of data: another date window or time zone, another filter, another refund policy, another grain. Both numbers can be correct for their own definition. TruthSplit measures the effect; it does not say which definition is right.
- A **data difference** means that under the same definition, the records differ: a record is missing, there's an extra one, an exact duplicate, a different amount, a join copy, or a row without a key.

A finding states **which records** account for an amount. Whether that is the **root cause** depends on the rule:

| Label | Meaning | Used for |
|---|---|---|
| `confirmed` | The amount is recomputed exactly from listed records, and the mechanism is explicit: a declared definition rule, declared query metadata, or an exact per-record arithmetic identity. Assumptions are written on the finding. | definition replay steps; join amplification with declared 1:N join metadata; query-window cutoff; refund identity; missing, extra, duplicate and mismatch (as record-level facts, not causes) |
| `suspected` | A pattern fits, but the evidence needed to confirm it is missing. **No amount is attributed**: evidence rows have no `contribution`, any indicative amount is in `calculation` / `detail.indicative_excess`, and the difference stays with the record-level findings. | join amplification without join metadata; one-sided records just outside the other side's observed time range; a definition rule that can't be replayed because the left data lacks the field; non-unique keys |
| `unexplained` | Part of the difference that the data can't attribute. | rows without a business key |

There are no confidence percentages. Each finding carries the `rule_id` that produced it and a `classification_reason`.

## Order of attribution

Each stage only sees what earlier stages left, so nothing is counted twice, and `Σ attributed + unexplained = delta` is asserted on every run.

1. **Definition replay.** The left definition is switched to the right one a rule at a time, and the left data is re-evaluated after each switch:
   1. date window, time zone and end bound
   2. filters
   3. refund policy
   4. grain
   5. null policy
   6. value field

   Step *i* contributes `left metric after step i − left metric before step i`, and every record whose contribution changed is listed with its value before and after. Two windows written in different zones that cover the same instants count as identical.
2. **Join amplification**, per side. A key qualifies when its rows carry one identical amount but are not byte-identical. It is confirmed only when that side's snapshot declares a 1:N join starting at the table the amount comes from (`registered_queries.py`: `measure_table`, `joins`). The extra copies are then removed before record matching.
3. **Record-level reconciliation** (`comparison/matching.py`) partitions what remains by business key: extra, missing, exact duplicates, mismatch. Two refinements run first:
   - **Query-window cutoff:** one-sided keys whose timestamps lie outside the window the *other* snapshot's SQL applied.
   - **Refund identity:** matched keys whose difference equals, to the cent, the refund recorded for that key. This is how a source that reports amounts already net of refunds shows up.
4. **Rows without a key** are the unexplained remainder.

## Overlapping causes

One record can be affected by more than one rule. For example, a fully refunded order that also falls outside the other side's window. The ordered replay assigns it to whichever rule comes first. For every definition step the engine also computes the rule's **standalone** effect (that rule alone, applied to the original left definition). Where standalone ≠ ordered, the finding has an `overlap_note` that gives both figures. Per-cause amounts depend on the order; their sum does not.

## Demo scenarios

`sample_data/manifest.json` is an oracle computed from a pure-Python model of the seeded source, not from the database or the engine. `tests/integration/test_demo.py` checks the engine against it, and against SQL written separately for those tests:

- **Scenario A, fully explained.** Finance (net of refunds, New York window) against Sales (gross, payments join, UTC window). Delta 155,997.39 = refunds 102,360.14 + join amplification 56,224.00 − 9,604.00 (finance-only cutoff) + 7,017.25 (sales-only cutoff); unexplained 0.00. All four findings are confirmed. Fifteen orders belong to two causes. A per-order check shows that the contributions across all findings add up to each order's actual difference, so nothing is counted twice: the refund part of a refunded order goes to the refund step on the left, and its join copies are removed on the right.
- **Scenario B, unexplained remainder.** The finance query against a ledger export with planted problems:
  - confirmed: missing orders −6,425.81, exact duplicates +3,166.56, record-level mismatch +5,518.00
  - suspected, not attributed: join-like fan-out, indicative 5,113.00, which stays inside the mismatch figure
  - unexplained: manual journal lines without an order number, 996.75
  - suspected: non-unique keys

Neither Finance nor Sales is "wrong" in scenario A. They answer different questions, and the sales query counts split-payment orders more than once.

## Limits

- **Rules are not inferred from SQL.** Query-level rules use declared registered-query metadata. A file source has no metadata, so its join copies can only be suspected.
- **Refund identity** needs the refund amount recorded on at least one side. Neither demo scenario exercises it (both record refunds explicitly), so it is covered by unit tests only.
- **Unreplayable rules:** if the left data lacks a field the right definition uses, that rule is reported as suspected and its effect stays inside the record-level findings.
