# CLAUDE.md

Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) first. It records the project's purpose, decisions,
current state, Fabric facts relied on, and open items.

- Tests: `uv sync --group dev && uv run pytest -q`
- Fabric deployable files are generated: edit `src/housing_meta/fabric_pipeline.py` /
  `fabric_assets/`, then `uv run housing-fabric bundle`. Never hand-edit `fabric/bundle/`.
- Production metadata lives in OneLake, not git. The `metadata/*.csv` files are demo copies.
- Ask before committing.
