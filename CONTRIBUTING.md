# Development workflow

The default branch is `main`. Create one branch per change, open a pull request, and wait for backend preview and frontend build checks before merging. Record what changed, how it was tested, and any model/hardware limitations in the PR description. Do not commit weights, generated media, project data, secrets or `.env` files.

Local checks:

```bash
cd backend && python -m pip install -r requirements-dev.txt && python -m pytest -q
cd ../frontend && npm ci && npm run build
```

The CPU preview CI proves the orchestration and media assembly. It cannot assess AI output quality. Add GPU integration tests as a separate, manually triggered workflow once a GPU runner exists; do not make public CI depend on private checkpoints.
