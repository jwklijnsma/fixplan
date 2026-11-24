"""Core data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
import hashlib


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value, default: "Severity | None" = None) -> "Severity":
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(max(0, min(4, value)))
        if not value:
            return default if default is not None else cls.MEDIUM
        key = str(value).strip().upper()
        aliases = {
            "BLOCKER": "CRITICAL", "FATAL": "CRITICAL", "MAJOR": "HIGH", "ERROR": "HIGH",
            "MINOR": "LOW", "WARNING": "MEDIUM", "WARN": "MEDIUM", "MODERATE": "MEDIUM",
            "NOTE": "INFO", "HINT": "INFO", "STYLE": "INFO", "INFORMATIONAL": "INFO",
        }
        key = aliases.get(key, key)
        try:
            return cls[key]
        except KeyError:
            return default if default is not None else cls.MEDIUM

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass
class SourceFile:
    """One text file from the upload, with the type we detected for it."""
    path: str            # relative to the upload root, always with "/"
    kind: str            # "dockerfile", "kubernetes", "terraform", "log", ...
    text: str
    truncated: bool = False

    @property
    def lines(self) -> int:
        return self.text.count("\n") + 1


@dataclass
class Finding:
    rule: str
    severity: Severity
    file: str
    line: int | None
    message: str
    source: str                 # "static", a linter name, or "llm"
    category: str = "general"   # security, reliability, correctness, performance, style, ...
    suggestion: str = ""
    confidence: float | None = None

    @property
    def id(self) -> str:
        raw = f"{self.rule}|{self.file}|{self.line}|{self.message}"
        return "F" + hashlib.sha1(raw.encode()).hexdigest()[:8]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.label
        d["id"] = self.id
        return d


@dataclass
class Step:
    """One step of the fix plan."""
    title: str
    why: str
    priority: Severity
    files: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)    # Finding ids this step resolves
    actions: list[str] = field(default_factory=list)     # what to do, in order
    commands: list[str] = field(default_factory=list)    # shell commands, if any
    patch: str = ""                                      # unified diff or snippet, if any
    verify: list[str] = field(default_factory=list)      # how to check it worked
    effort: str = ""                                     # "minutes", "hours", "days"
    risk: str = ""                                       # what can break while fixing

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority"] = self.priority.label
        return d


@dataclass
class Plan:
    title: str
    summary: str
    steps: list[Step]
    findings: list[Finding]
    files: list[SourceFile]
    notes: list[str] = field(default_factory=list)       # warnings: skipped files, tool errors
    generator: str = "llm"                               # "llm" or "rules" (no model)
    model: str = ""
    seconds: float = 0.0

    @property
    def worst(self) -> Severity | None:
        return max((f.severity for f in self.findings), default=None)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "generator": self.generator,
            "model": self.model,
            "seconds": round(self.seconds, 1),
            "steps": [s.to_dict() for s in self.steps],
            "findings": [f.to_dict() for f in self.findings],
            "files": [{"path": f.path, "kind": f.kind, "lines": f.lines,
                       "truncated": f.truncated} for f in self.files],
            "notes": self.notes,
        }
