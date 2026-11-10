"""Parsers determinísticos para logs de EDA (SpyGlass e VCS).

A ideia é reduzir centenas de linhas de log a um resumo pequeno e confiável,
que pode ser lido direto ou enviado a um LLM sem desperdiçar tokens com ruído
(banner, carregamento de políticas, "Checking Rule ...").
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_CONTEXT_LINES = 4

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"


@dataclass
class Issue:
    """Um problema encontrado no log."""

    severity: str  # "fatal" | "error" | "warning"
    message: str
    line: int  # linha (1-based) no arquivo de log
    code: str | None = None  # ex.: "SE" em "Error-[SE]", "38" em "WARNING [38]"
    source: str = "design"  # "design" (RTL/testbench) ou "tool" (ambiente da ferramenta)
    context: list[str] = field(default_factory=list)
    occurrences: int = 1


@dataclass
class LogDigest:
    """Resumo estruturado de um arquivo de log."""

    path: str
    tool: str  # "spyglass" | "vcs-compile" | "vcs-sim" | "unknown"
    target: str | None
    status: str
    counts: dict[str, int]
    issues: list[Issue]
    highlights: list[str]
    total_lines: int

    def to_dict(self) -> dict:
        return asdict(self)


def detect_tool(text: str) -> str:
    if "SpyGlass" in text:
        return "spyglass"
    if "V C S   S i m u l a t i o n" in text or "Chronologic VCS simulator" in text:
        return "vcs-sim"
    if "Chronologic VCS" in text:
        return "vcs-compile"
    return "unknown"


def parse_file(path: str | Path) -> LogDigest:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, str(path))


def parse_text(text: str, path: str = "<memória>") -> LogDigest:
    lines = text.splitlines()
    tool = detect_tool(text)
    parser = {
        "spyglass": _parse_spyglass,
        "vcs-compile": _parse_vcs_compile,
        "vcs-sim": _parse_vcs_sim,
    }.get(tool, _parse_unknown)
    digest = parser(lines, path)
    digest.issues = _dedupe(digest.issues)
    return digest


# --------------------------------------------------------------------------- #
# SpyGlass
# --------------------------------------------------------------------------- #

_SG_TOP = re.compile(r"with top `([^']+)'")
_SG_REPORTED = re.compile(
    r"Reported Messages:\s*(\d+)\s*Fatals?,\s*(\d+)\s*Errors?,\s*(\d+)\s*Warnings?,\s*(\d+)\s*Infos?"
)
_SG_TOTAL = re.compile(
    r"^\s*Total\s*:\s*(\d+)\s*errors?,\s*(\d+)\s*warnings?,\s*(\d+)\s*information"
)
_SG_EXIT = re.compile(r"SpyGlass Exit Code (\d+)(?:\s*\((.*)\))?")
_SG_MSG = re.compile(r"^(FATAL|ERROR|WARNING)\s*\[(\w+)\]\s*(.*)$")


def _parse_spyglass(lines: list[str], path: str) -> LogDigest:
    target = None
    counts = {"fatal": 0, "error": 0, "warning": 0, "info": 0}
    have_reported = False
    exit_code: int | None = None
    issues: list[Issue] = []
    highlights: list[str] = []

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if target is None and (m := _SG_TOP.search(line)):
            target = m.group(1)
        if m := _SG_REPORTED.search(line):
            fat, err, warn, info = map(int, m.groups())
            counts = {"fatal": fat, "error": err, "warning": warn, "info": info}
            have_reported = True
        elif not have_reported and (m := _SG_TOTAL.search(line)):
            err, warn, info = map(int, m.groups())
            counts.update(error=err, warning=warn, info=info)
        if m := _SG_EXIT.search(line):
            exit_code = int(m.group(1))
            highlights.append(line.strip())
        if m := _SG_MSG.match(line.strip()):
            severity, code, message = m.groups()
            issues.append(
                Issue(
                    severity=severity.lower(),
                    message=_squash(message),
                    line=i,
                    code=code,
                    source="tool",
                )
            )

    if counts["fatal"] or counts["error"]:
        status = STATUS_FAIL
    elif counts["warning"]:
        status = STATUS_WARN
    elif exit_code == 0 or have_reported:
        status = STATUS_PASS
    else:
        status = STATUS_UNKNOWN
    if exit_code not in (None, 0) and status == STATUS_PASS:
        status = STATUS_WARN

    return LogDigest(path, "spyglass", target, status, counts, issues, highlights, len(lines))


# --------------------------------------------------------------------------- #
# VCS: compilação
# --------------------------------------------------------------------------- #

_VCS_DIAG = re.compile(r"^(Error|Warning|Lint)-\[([\w-]+)\]\s*(.*)$")
_VCS_CPU = re.compile(r"CPU time:.*seconds to compile")


def _parse_vcs_compile(lines: list[str], path: str) -> LogDigest:
    target = None
    issues: list[Issue] = []
    highlights: list[str] = []
    compiled = False

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if line.strip() == "Top Level Modules:" and i < len(lines):
            target = lines[i].strip() or target
        if m := _VCS_DIAG.match(line.strip()):
            kind, code, message = m.groups()
            severity = "error" if kind == "Error" else "warning"
            issues.append(
                Issue(severity, _squash(message), i, code, "design", _context_after(lines, i))
            )
        if _VCS_CPU.search(line):
            compiled = True
            highlights.append(line.strip())

    counts = _count(issues)
    if counts["error"]:
        status = STATUS_FAIL
    elif counts["warning"]:
        status = STATUS_WARN
    elif compiled:
        status = STATUS_PASS
    else:
        status = STATUS_UNKNOWN
    return LogDigest(path, "vcs-compile", target, status, counts, issues, highlights, len(lines))


# --------------------------------------------------------------------------- #
# VCS: simulação
# --------------------------------------------------------------------------- #

_SIM_DIAG = re.compile(r"^(Fatal|Error|Warning):\s*(.*)$")
_SIM_BANNER = re.compile(r"^===\s*(\S+)\s*===$")
_SIM_FINISH = re.compile(r'\$finish called from file "([^"]+)"')
_SIM_TIME = re.compile(r"^Time:\s*(.+)$")


def _parse_vcs_sim(lines: list[str], path: str) -> LogDigest:
    target = None
    issues: list[Issue] = []
    highlights: list[str] = []
    passed = failed = finished = False

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if target is None and (m := _SIM_BANNER.match(line)):
            target = m.group(1)
        if m := _SIM_DIAG.match(line):
            kind, location = m.groups()
            # Em $error/$fatal o VCS imprime "arquivo, linha: escopo: tempo" e a
            # mensagem do testbench vem na linha seguinte.
            following = lines[i].strip() if i < len(lines) else ""
            message = following if following and not _SIM_DIAG.match(following) else location
            context = [location] if message != location else []
            issues.append(Issue(kind.lower(), _squash(message), i, None, "design", context))
        elif m := _VCS_DIAG.match(line):
            kind, code, message = m.groups()
            severity = "error" if kind == "Error" else "warning"
            issues.append(Issue(severity, _squash(message), i, code, "design", _context_after(lines, i)))
        if line.startswith("PASS"):
            passed = True
            highlights.append(line)
        elif line.startswith("FAIL"):
            failed = True
            highlights.append(line)
        if _SIM_FINISH.search(line):
            finished = True
        if m := _SIM_TIME.match(line):
            highlights.append(f"Tempo simulado: {m.group(1)}")

    counts = _count(issues)
    if failed or counts["fatal"] or counts["error"]:
        status = STATUS_FAIL
    elif counts["warning"]:
        status = STATUS_WARN
    elif passed:
        status = STATUS_PASS
    else:
        # Terminou sem PASS explícito (ou nem terminou): não dá para afirmar nada.
        status = STATUS_UNKNOWN
        if not finished:
            highlights.append("Simulação sem $finish: pode ter sido interrompida.")
    return LogDigest(path, "vcs-sim", target, status, counts, issues, highlights, len(lines))


# --------------------------------------------------------------------------- #
# Desconhecido
# --------------------------------------------------------------------------- #

_GENERIC = re.compile(r"\b(fatal|error|warning)\b", re.IGNORECASE)


def _parse_unknown(lines: list[str], path: str) -> LogDigest:
    issues = []
    for i, raw in enumerate(lines, start=1):
        if m := _GENERIC.search(raw):
            issues.append(Issue(m.group(1).lower(), _squash(raw.strip()), i, None, "design"))
    counts = _count(issues)
    status = STATUS_FAIL if counts["error"] or counts["fatal"] else STATUS_UNKNOWN
    return LogDigest(path, "unknown", None, status, counts, issues, [], len(lines))


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #


def _context_after(lines: list[str], line_no: int) -> list[str]:
    """Linhas que detalham um diagnóstico (até a próxima linha vazia)."""
    context = []
    for nxt in lines[line_no : line_no + MAX_CONTEXT_LINES]:
        if not nxt.strip():
            break
        if _VCS_DIAG.match(nxt.strip()) or _SIM_DIAG.match(nxt.strip()):
            break
        context.append(nxt.strip())
    return context


def _count(issues: list[Issue]) -> dict[str, int]:
    counts = {"fatal": 0, "error": 0, "warning": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + issue.occurrences
    return counts


def _dedupe(issues: list[Issue]) -> list[Issue]:
    """Agrupa mensagens repetidas, mantendo a primeira ocorrência."""
    seen: dict[tuple[str, str | None, str], Issue] = {}
    for issue in issues:
        key = (issue.severity, issue.code, issue.message)
        if key in seen:
            seen[key].occurrences += 1
        else:
            seen[key] = issue
    return list(seen.values())


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
