# Web UI

```bash
fixplan serve --open                     # http://localhost:8766, no token on localhost
FIXPLAN_WEB_TOKEN=$(openssl rand -hex 24) fixplan serve --bind 0.0.0.0
```

- **Upload**: drop files, a whole folder or a ZIP/tar archive, or use the buttons. `.git`,
  `node_modules`, virtualenvs and `.terraform` are left out in the browser already.
- **Paste**: a log, an error, a manifest. Give it a file name (`app.log`, `Dockerfile`) if
  detection gets it wrong.
- **Goal** and **language** per plan; **rules only** for a result in seconds.
- The plan shows each step with copy buttons on commands and patches, a findings table and
  the detected files. Download as Markdown, JSON or SARIF, or copy the Markdown into an
  issue or merge request.

Jobs run one at a time (one GPU); the list on the left shows progress. Finished plans are
kept in `~/.cache/fixplan/jobs` (`--data` to change it), the newest 100.

## Security

| | |
|---|---|
| Token | Required when binding to anything but localhost (`FIXPLAN_WEB_TOKEN` or `--token`). Open `http://host:8766/#token=<token>` once; the browser remembers it. `--no-token` only behind an authenticating proxy. |
| Cross-site | Without a token, API calls from another origin are refused. The page sets a strict CSP and `frame-ancestors 'none'`. |
| Uploads | Size limit `max_upload_mb` (default 50, unpacked). Archive entries with `..`, absolute paths, links or device files are skipped. Nothing is written to disk or executed; linters read a private temp copy. |
| Model | Uploaded content is passed as data between markers, with an explicit instruction not to follow instructions in it. Uploads cannot set any configuration. |

## API

All calls take `Authorization: Bearer <token>` when a token is set.

| | |
|---|---|
| `GET /api/info` | version, model, Ollama status, linters |
| `POST /api/jobs` | JSON `{files: [{name, data(base64)}], text, text_name, goal, language, no_llm}` → job |
| `GET /api/jobs` | all jobs |
| `GET /api/jobs/<id>` | status, progress and the plan |
| `GET /api/jobs/<id>/plan.md` (`.json`, `.sarif`) | downloads |
| `DELETE /api/jobs/<id>` | delete a finished job |
| `GET /healthz` | liveness, no token |
