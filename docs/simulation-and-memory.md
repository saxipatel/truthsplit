# What-if simulation and investigation memory

## What-if simulator

`POST /api/investigations/{id}/simulations` applies **one** correction to in-memory copies of the investigation's stored inputs and runs the full Difference Autopsy again. Nothing is written except one `simulations` row and an audit event:

- not the source database
- not `dataset_records` or `datasets`
- not the investigation, its findings or its evidence

| `rule_type` | Parameters | Effect on the copy |
|---|---|---|
| `remove_duplicates` | `side` (`left`/`right`/`both`), `kind` (`exact` or `join_copies`) | Drops rows identical to an earlier row of the same key (`exact`), or extra rows of a key whose rows share one amount but differ elsewhere (`join_copies`). |
| `align_window` | `side` | Gives that side the other side's window, time zone and end bound. |
| `refund_policy` | `side`, `policy` | Changes that side's refund policy. |
| `align_definition` | `side` | Gives that side the other side's whole definition. |
| `adjust_mapping` | `side`, `mapping` | Re-reads the stored upload and normalizes it with another column mapping. File datasets only; query snapshots keep their registered mapping. Refused if the new mapping rejects every row; otherwise rejected rows are reported in `result_json.remap`. |

Before simulating, the engine checks two things and refuses with 422 if either fails:

- that each dataset still matches the fingerprint recorded with the investigation;
- that re-evaluating the stored inputs reproduces the stored baseline exactly.

A rule whose result would read a column the data doesn't map is also refused.

The result reports:
- `before_*` and `after_*` for left, right and delta;
- `resolved_amount`, defined as `|before_delta| − |after_delta|`, which is negative when the change widens the gap;
- `result_json.delta_change`, defined as `after − before`;
- `affected_rows`, the records whose contribution changed, including ones dropped or added;
- the simulated findings, explained and unexplained amounts, and up to 200 changed records per side.

Repeating a simulation gives the same result.

## Memory

- **Status workflow** (`PATCH /api/investigations/{id}/status`):

  | From | Allowed transitions |
  |---|---|
  | open | in_review, resolved, archived |
  | in_review | open, resolved, archived |
  | resolved | open (reopen), archived |
  | archived | open |

  Resolving needs a note.
- **Notes** (`POST /api/investigations/{id}/notes`): `accepted` or `rejected` must name a finding of this investigation; `comment` may name one. Notes record human decisions and **never change a finding's evidence status**, which remains what the engine could prove.
- **History** (`GET /api/investigations/{id}/history`): audit events for creation, simulations, status changes and notes, oldest first. Investigations created before auditing existed carry a creation event marked `backfilled: true` (migration `0004`).
- **Browsing:** `GET /api/investigations` filters by `status`, `q` (case-insensitive title search; `%` and `_` are matched literally), `finding_type` and `is_demo`.
- **Recurring patterns** (`GET /api/memory/patterns`): for each finding type and rule, the number of investigations it appears in, counts by evidence status, and the sum of absolute attributed amounts.
- **Similar investigations** (`GET /api/investigations/{id}/similar`): deterministic point scoring, with every point given as a reason.

  | Shared between the two investigations | Points |
  |---|---|
  | a confirmed finding from the same rule | +3 |
  | a suspected finding from the same rule | +1 |
  | a metric definition name | +2 |
  | a source (registered query, or the same uploaded file by SHA-256) | +2 |
  | a differing Metric DNA field | +1 |

  Diagnoses accepted in a matching investigation are listed as suggestions, with `applies_here` when this investigation has a finding from the same rule. They are never applied. This is rule-based matching, not machine learning.
