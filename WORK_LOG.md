
Review follow-up, 2026-11-17: Cache entries are immutable ChordRepair values; tests now verify separate thresholds, repeated hits and the 2048-entry cap. Full tests exposed an existing completion/cleanup race. Terminal job status is now published after input cleanup on both success and failure, with deterministic tests that failed before the fix. All 402 tests and Ruff on changed files pass. No live deployed server was tested.

Merge integration: current main already includes the completion-order fix with its own success-path regression. Kept that implementation instead of replacing it, and retained the new success/failure invariant coverage. The PR production change is therefore chord-repair caching only relative to current main.
