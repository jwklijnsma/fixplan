# Built-in rules

`fixplan rules` prints this list. Switch a rule off with `disabled_rules: [FP013]` in
`~/.fixplan.yml` (or `FIXPLAN_DISABLED_RULES=FP013,FP027`).

| Rule | Severity | Problem | File kinds |
|---|---|---|---|
| `FP001` | high | Hard-coded secret. | all code and config |
| `FP002` | critical | A private key is committed. | all code and config |
| `FP003` | high | Downloads a script and runs it straight from the network. | ansible, dockerfile, github-actions, gitlab-ci, helm, kubernetes, powershell, python, shell, systemd, yaml |
| `FP004` | high | World-writable permissions (777). | all code and config |
| `FP005` | high | TLS certificate verification is switched off. | all code and config |
| `FP006` | high | Credentials inside a URL. | all code and config |
| `FP007` | medium | SELinux or the host firewall is switched off. | ansible, dockerfile, helm, kubernetes, shell, systemd, yaml |
| `FP010` | medium | Base image without a fixed tag (or :latest). | dockerfile |
| `FP011` | medium | The container runs as root. | dockerfile |
| `FP012` | low | ADD with a URL downloads without checksum verification. | dockerfile |
| `FP013` | low | apt-get install without --no-install-recommends. | dockerfile |
| `FP020` | high | Privileged container. | compose, helm, kubernetes |
| `FP021` | high | The pod shares the host's network/PID/IPC namespace. | helm, kubernetes |
| `FP022` | medium | hostPath volume: the pod can read or write the node's disk. | helm, kubernetes |
| `FP023` | medium | Image without a fixed tag (or :latest). | compose, helm, kubernetes |
| `FP024` | medium | allowPrivilegeEscalation is true. | helm, kubernetes |
| `FP025` | medium | runAsUser: 0 runs the container as root. | helm, kubernetes |
| `FP026` | medium | Container without a memory limit. | kubernetes |
| `FP027` | low | Deployment container without a readinessProbe. | kubernetes |
| `FP030` | medium | eval on a variable: whatever is in it gets executed. | shell |
| `FP031` | medium | rm -rf on a variable that may be empty (rm -rf /). | dockerfile, shell |
| `FP040` | high | SSH allows root to log in. | ansible, shell, sshd |
| `FP041` | medium | SSH allows password login. | ansible, shell, sshd |
| `FP050` | medium | subprocess with shell=True: risk of shell injection. | python |
| `FP051` | high | yaml.load without SafeLoader can run code from the YAML. | python |
| `FP052` | medium | Unpickling data can run code if the data is not trusted. | python |
| `FP053` | medium | eval/exec runs a string as code. | javascript, php, python |
| `FP060` | medium | Open to the whole internet (0.0.0.0/0). | terraform |
| `FP061` | high | Publicly readable storage or database. | terraform |
| `FP070` | medium | Old TLS versions (TLS 1.0/1.1 or SSLv3) are enabled. | apache, haproxy, nginx |
| `FP071` | low | nginx advertises its version. | nginx |
| `FP089` | high | The program crashed (segfault or panic). | log |
| `FP090` | high | The process ran out of memory. | log |
| `FP091` | high | The disk (or inode table) is full. | log |
| `FP092` | high | TLS certificate problem (unknown CA, expired, or wrong name). | log |
| `FP093` | medium | Connection refused: nothing listens on that host and port. | log |
| `FP094` | medium | DNS lookup failed. | log |
| `FP095` | medium | Permission denied. | log |
| `FP096` | high | The container keeps crashing on start. | log |
| `FP097` | high | The image cannot be pulled. | log |
| `FP098` | medium | A timeout: a dependency is slow or unreachable (firewall, proxy, wrong route). | log |
| `FP099` | medium | The process hit its open-file limit. | log |

Secrets (`FP001`) skip values that look like placeholders or references
(`{{ vault_x }}`, `${VAR}`, `changeme`, `os.environ[...]`, `secretKeyRef`, …).

Log rules (`FP089`–`FP099`) are reported once per log with the count and the first line,
so the model and the plan see the pattern, not a thousand repeats.

## File types

`fixplan types` prints this list. Detection looks at the path first (`Dockerfile`,
`.github/workflows/`, `templates/` next to `Chart.yaml`, `roles/…`) and the content second
(`apiVersion:` + `kind:`, a shebang, `[Unit]`, timestamps and `ERROR` lines).

| Kind | |
|---|---|
| `dockerfile` | Dockerfile / Containerfile |
| `compose` | Docker Compose |
| `kubernetes` | Kubernetes / OpenShift manifest |
| `helm` | Helm chart |
| `kustomize` | Kustomize |
| `ansible` | Ansible |
| `terraform` | Terraform / OpenTofu |
| `github-actions` | GitHub Actions workflow |
| `gitlab-ci` | GitLab CI |
| `shell` | Shell script |
| `python` | Python |
| `javascript` | JavaScript / TypeScript |
| `go` | Go |
| `java` | Java / Kotlin |
| `php` | PHP |
| `sql` | SQL |
| `nginx` | nginx config |
| `apache` | Apache httpd config |
| `systemd` | systemd unit |
| `haproxy` | HAProxy config |
| `sshd` | sshd config |
| `powershell` | PowerShell |
| `log` | Log / error output |
| `yaml` | YAML |
| `json` | JSON |
| `ini` | INI / config |
| `markdown` | Markdown / text |
| `text` | Text |
