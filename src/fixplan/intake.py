"""Turn whatever was uploaded into a list of text files.

Accepts a directory, a single file, a ZIP or tar archive, or raw text (pasted or piped).
Archives are unpacked in memory with strict limits: no paths outside the root, no links,
no device files, a cap on the unpacked size and on the number of files. Binary files are
skipped. Nothing from the upload is ever executed.
"""

from __future__ import annotations

import fnmatch
import io
import sys
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

from .config import Config
from .detect import detect, is_binary
from .models import SourceFile

MAX_SINGLE_FILE = 2 * 1024 * 1024     # bigger files are skipped (2 MiB of text is plenty)
ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


class IntakeError(ValueError):
    pass


class Intake:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.files: list[SourceFile] = []
        self.notes: list[str] = []
        self._bytes = 0
        self._skipped_binary = 0
        self._skipped_excluded = 0

    # -- entry points ------------------------------------------------------------
    def add_path(self, path: str | Path) -> "Intake":
        p = Path(path).expanduser()
        if not p.exists():
            raise IntakeError(f"{p}: not found")
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_symlink() or not f.is_file():
                    continue
                rel = f.relative_to(p).as_posix()
                if self._excluded(rel):
                    self._skipped_excluded += 1
                    continue
                try:
                    if f.stat().st_size > MAX_SINGLE_FILE:
                        self.notes.append(f"{rel}: skipped, larger than 2 MiB")
                        continue
                    self._add(rel, f.read_bytes())
                except OSError as exc:
                    self.notes.append(f"{rel}: cannot read ({exc})")
                if self._full():
                    break
        elif p.name.lower().endswith(ARCHIVE_EXT):
            self.add_archive(p.name, p.read_bytes())
        else:
            self._add(p.name, p.read_bytes())
        return self

    def add_bytes(self, name: str, data: bytes) -> "Intake":
        """An uploaded file: an archive is unpacked, anything else is one file."""
        # Browsers send folder uploads as "folder/sub/file": keep the folders when safe.
        rel = _safe_rel(name or "", "") or ""
        rel = "/".join(_clean_name(p) for p in rel.split("/") if _clean_name(p)) or "upload.txt"
        if rel.lower().endswith(ARCHIVE_EXT):
            return self.add_archive(rel, data)
        if self._excluded(rel):
            self._skipped_excluded += 1
            return self
        self._add(rel, data)
        return self

    def add_text(self, text: str, name: str = "") -> "Intake":
        """Pasted or piped text. Without a name the type is detected from the content."""
        name = _clean_name(name) or "pasted"
        self._add(name, text.encode("utf-8", "replace"))
        return self

    def add_archive(self, name: str, data: bytes) -> "Intake":
        limit = self.cfg.max_upload_mb * 1024 * 1024
        prefix = ""   # archives of one top folder (repo-main/...) lose that folder
        try:
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    members = [m for m in zf.infolist() if not m.is_dir()]
                    prefix = _common_top([m.filename for m in members])
                    for m in members:
                        if _is_zip_symlink(m):
                            continue
                        rel = _safe_rel(m.filename, prefix)
                        if rel is None:
                            self.notes.append(f"{m.filename}: unsafe path in archive, skipped")
                            continue
                        if self._excluded(rel):
                            self._skipped_excluded += 1
                            continue
                        if m.file_size > MAX_SINGLE_FILE:
                            self.notes.append(f"{rel}: skipped, larger than 2 MiB")
                            continue
                        if self._bytes + m.file_size > limit:
                            raise IntakeError(f"archive unpacks to more than {self.cfg.max_upload_mb} MB")
                        with zf.open(m) as fh:
                            self._add(rel, fh.read(MAX_SINGLE_FILE + 1))
                        if self._full():
                            break
            else:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                    members = [m for m in tf.getmembers() if m.isfile()]
                    prefix = _common_top([m.name for m in members])
                    for m in members:
                        rel = _safe_rel(m.name, prefix)
                        if rel is None:
                            self.notes.append(f"{m.name}: unsafe path in archive, skipped")
                            continue
                        if self._excluded(rel):
                            self._skipped_excluded += 1
                            continue
                        if m.size > MAX_SINGLE_FILE:
                            self.notes.append(f"{rel}: skipped, larger than 2 MiB")
                            continue
                        if self._bytes + m.size > limit:
                            raise IntakeError(f"archive unpacks to more than {self.cfg.max_upload_mb} MB")
                        fh = tf.extractfile(m)
                        if fh:
                            self._add(rel, fh.read(MAX_SINGLE_FILE + 1))
                        if self._full():
                            break
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as exc:
            raise IntakeError(f"{name}: not a readable archive ({exc})") from exc
        return self

    # -- result --------------------------------------------------------------------
    def result(self) -> tuple[list[SourceFile], list[str]]:
        notes = list(self.notes)
        if self._skipped_binary:
            notes.append(f"{self._skipped_binary} binary file(s) skipped")
        if self._skipped_excluded:
            notes.append(f"{self._skipped_excluded} file(s) excluded (vendored, generated, .git)")
        if self._full():
            notes.append(f"stopped at {self.cfg.max_files} files (max_files)")
        if not self.files:
            raise IntakeError("nothing to review: no text files in the upload")
        return self.files, notes

    # -- internals -------------------------------------------------------------------
    def _full(self) -> bool:
        return len(self.files) >= self.cfg.max_files

    def _excluded(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("./" + rel, pat)
                   for pat in self.cfg.exclude)

    def _add(self, rel: str, data: bytes) -> None:
        if self._full():
            return
        if len(data) > MAX_SINGLE_FILE:
            self.notes.append(f"{rel}: skipped, larger than 2 MiB")
            return
        if is_binary(data):
            self._skipped_binary += 1
            return
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        if not text.strip():
            return
        self._bytes += len(data)
        truncated = False
        if len(text) > self.cfg.max_file_chars * 4:   # keep memory sane; review chunks the rest
            text, truncated = text[: self.cfg.max_file_chars * 4], True
        kind = detect(rel, text)
        if kind == "log" and truncated:
            # for logs the end is where the failure is: keep head and tail
            full = data.decode("utf-8", "replace")
            keep = self.cfg.max_file_chars * 2
            text = full[:keep] + "\n\n[... middle of the log cut by fixplan ...]\n\n" + full[-keep:]
        self.files.append(SourceFile(path=rel, kind=kind, text=text, truncated=truncated))


def _clean_name(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    return "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')[:200]


def _common_top(names: list[str]) -> str:
    tops = {n.replace("\\", "/").lstrip("./").split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n.replace("\\", "/").lstrip("./") for n in names):
        return tops.pop() + "/"
    return ""


def _safe_rel(name: str, prefix: str) -> str | None:
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    if prefix and n.startswith(prefix):
        n = n[len(prefix):]
    p = PurePosixPath(n)
    if not n or p.is_absolute() or ".." in p.parts or (len(n) > 1 and n[1] == ":"):
        return None
    return p.as_posix()


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def read_stdin_or_path(target: str, cfg: Config, stdin_text: str | None = None) -> Intake:
    intake = Intake(cfg)
    if target == "-":
        intake.add_text(stdin_text if stdin_text is not None else sys.stdin.read())
    else:
        intake.add_path(target)
    return intake
