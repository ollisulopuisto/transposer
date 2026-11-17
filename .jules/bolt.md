## 2026-11-17 - Bounded early-exit edit distance is slower on short strings
**Learning:** Aborting `_distance` once `min(current row) > budget` triggers on 72% of calls but only saves 1-2 of ~4-7 rows; the per-row `min()` overhead makes it ~10% slower overall (0.90x). The budget (~1.0) is too generous relative to short (<=12 char) strings for early exit to pay off.
**Action:** Don't add bounded early-exit to `_distance`. Memoize the pure `repair_chord_symbol` instead: 2.9x on repetitive charts, no regression on all-unique input, all 399 tests pass, lint clean.
