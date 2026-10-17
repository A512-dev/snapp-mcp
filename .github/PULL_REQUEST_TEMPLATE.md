## What changed

<!-- One or two sentences. -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pytest` passes (offline tests, recorded fixtures)
- [ ] New or changed tools have a docstring telling the agent when to use them
- [ ] README tool tables and counts, and `CHANGELOG.md`, are updated
- [ ] Tools that write anything (like the calorie cache) are registered with `read_only=False`
