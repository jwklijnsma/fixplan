# Command line

```bash
fixplan [plan] <targets...> [options]
```

Targets are files, folders, `.zip`/`.tar.gz` archives, or `-` for stdin. Default: `.`.

## Examples

```bash
fixplan                                   # this folder
fixplan Dockerfile k8s/                   # some files and a folder
fixplan service.zip -g "make it run on OpenShift"
fixplan . --lang nl                       # the plan in Dutch
journalctl -u nginx -n 500 | fixplan - --name nginx.log
kubectl logs deploy/api --previous | fixplan - --name api.log -g "why does it crash?"
fixplan infra/ --no-llm -f sarif          # rules + linters only, SARIF for code scanning
```

## Output

Reports go to `.fixplan/` (`-o` to change it): `plan.md`, `plan.json` and with
`-f md,json,sarif` also `plan.sarif`. When stdout is not a terminal (a pipe, CI) the
Markdown plan is printed too; `--print` forces it.

Each step has:

| Field | |
|---|---|
| title, priority, effort | what, how urgent, minutes/hours/days |
| why | the reason, in one or two sentences |
| actions | what to do, in order |
| commands | shell commands to run |
| patch | a unified diff or the corrected snippet |
| verify | how to prove it worked |
| risk | what may break while doing it |
| findings, files | which findings it resolves, in which files |

## Options

| Option | |
|---|---|
| `-g, --goal TEXT` | what you want to achieve; the plan is ordered around it |
| `--name NAME` | file name for stdin, helps detection (`app.log`, `Dockerfile`) |
| `-o, --out DIR` | report folder (default `.fixplan`) |
| `-f, --format LIST` | `md`, `json`, `sarif` (default `md,json`) |
| `--lang en\|nl` | language of the plan |
| `--no-llm` | built-in checks and linters only |
| `--no-lint` | do not run linters |
| `--fail-on SEV` | exit 1 at or above this severity; `never` always exits 0 |
| `--max-steps N` | cap the plan length (default 12) |
| `--host URL`, `--model NAME` | Ollama server and model |
| `--workers N` | parallel review requests (match `OLLAMA_NUM_PARALLEL`) |
| `--num-ctx N`, `--timeout S` | model context size, seconds of silence before giving up |
| `--quiet` | no progress output |

## In a pipeline

```yaml
# .gitlab-ci.yml
fixplan:
  image: ghcr.io/jwklijnsma/fixplan:latest
  variables:
    OLLAMA_HOST: http://gpu-box.internal:11434
  script:
    - fixplan plan . -f md,json,sarif --fail-on high
  artifacts:
    when: always
    paths: [.fixplan/]
```
