import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from mock_ollama import MockOllama  # noqa: E402

from fixplan.config import Config  # noqa: E402

DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
        - name: web
          image: nginx:latest
          ports:
            - containerPort: 8080
          securityContext:
            privileged: true
"""

DOCKERFILE = """\
FROM python
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://get.example.com/install.sh | sh
ENV API_KEY=s3cr3tvalue123456
USER root
CMD ["python", "app.py"]
"""

LOG = """\
2026-11-24 10:00:01 INFO starting app
2026-11-24 10:00:02 INFO connecting to db:5432
2026-11-24 10:00:03 ERROR connect: connection refused
2026-11-24 10:00:04 ERROR connect: connection refused
2026-11-24 10:00:05 FATAL giving up
"""


@pytest.fixture
def sample(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "deploy.yaml").write_text(DEPLOY)
    (tmp_path / "Dockerfile").write_text(DOCKERFILE)
    (tmp_path / "app.log").write_text(LOG)
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0binary")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("eval(x)")
    return tmp_path


@pytest.fixture
def cfg():
    c = Config()
    c.use_lint = False
    return c


@pytest.fixture
def ollama():
    with MockOllama() as m:
        yield m
