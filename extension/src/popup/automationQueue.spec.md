# Automation Queue Logic Cases

## `chunkUrls(urls, 10)`

- `[]` -> `[]`
- `[u1]` -> `[[u1]]`
- `10 urls` -> `1 batch (10)`
- `11 urls` -> `2 batches (10 + 1)`
- `23 urls` -> `3 batches (10 + 10 + 3)`

## `clampAutomationConcurrency(value)`

- `undefined` -> `2` (default)
- `0` -> `1` (min clamp)
- `1` -> `1`
- `2` -> `2`
- `3` -> `3`
- `4` -> `3` (max clamp)

## Worker-Pool Concurrency Guard

- When input concurrency is outside `1..3`, effective worker count must be clamped to `1..3`.
- Worker pool result order must match input order.
