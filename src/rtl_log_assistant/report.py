"""Relatório em Markdown gerado só a partir dos parsers (sem LLM)."""

from __future__ import annotations

import json
from pathlib import Path

from .parsers import STATUS_FAIL, STATUS_PASS, STATUS_UNKNOWN, STATUS_WARN, LogDigest

_ICON = {STATUS_PASS: "✅", STATUS_WARN: "⚠️", STATUS_FAIL: "❌", STATUS_UNKNOWN: "❔"}
_TOOL = {
    "spyglass": "Lint (SpyGlass)",
    "vcs-compile": "Compilação (VCS)",
    "vcs-sim": "Simulação (VCS)",
    "unknown": "Desconhecido",
}
_ORDER = {STATUS_FAIL: 0, STATUS_UNKNOWN: 1, STATUS_WARN: 2, STATUS_PASS: 3}


def overall_status(digests: list[LogDigest]) -> str:
    if not digests:
        return STATUS_UNKNOWN
    return min((d.status for d in digests), key=_ORDER.__getitem__)


def render_markdown(digests: list[LogDigest]) -> str:
    total_lines = sum(d.total_lines for d in digests)
    status = overall_status(digests)
    out = [
        "# Relatório de logs RTL",
        "",
        f"**Status geral:** {_ICON[status]} `{status}` · "
        f"{len(digests)} logs · {total_lines} linhas analisadas",
        "",
        "| Status | Etapa | Alvo | Erros | Avisos | Log |",
        "|:-:|---|---|--:|--:|---|",
    ]
    for d in sorted(digests, key=lambda d: (_ORDER[d.status], d.tool, d.path)):
        errors = d.counts.get("fatal", 0) + d.counts.get("error", 0)
        out.append(
            f"| {_ICON[d.status]} | {_TOOL[d.tool]} | `{d.target or '?'}` | "
            f"{errors} | {d.counts.get('warning', 0)} | `{Path(d.path).name}` |"
        )

    problems = [
        d
        for d in digests
        if any(i.source == "design" for i in d.issues)
        or d.status in (STATUS_FAIL, STATUS_UNKNOWN)
    ]
    if problems:
        out += ["", "## Problemas por log", ""]
        for d in problems:
            out.append(f"### {_ICON[d.status]} `{Path(d.path).name}`")
            for issue in d.issues:
                if issue.source == "design":
                    out += _issue_lines(issue)
            for h in d.highlights:
                out.append(f"- {h}")
            out.append("")

    tool_issues = _group_tool_issues(digests)
    if tool_issues:
        out += [
            "",
            "## Avisos do ambiente da ferramenta",
            "",
            "_Não indicam problema no RTL, mas vale acompanhar._",
            "",
        ]
        for (severity, code, message), files in tool_issues.items():
            code_txt = f"[{code}] " if code else ""
            out.append(f"- **{severity}** {code_txt}{message} — em {len(files)} log(s)")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def digests_to_json(digests: list[LogDigest]) -> str:
    """Formato compacto usado como entrada do LLM e pela opção --json."""
    return json.dumps([d.to_dict() for d in digests], ensure_ascii=False, indent=1)


def _issue_lines(issue) -> list[str]:
    code = f"[{issue.code}] " if issue.code else ""
    times = f" (×{issue.occurrences})" if issue.occurrences > 1 else ""
    lines = [f"- **{issue.severity}** {code}{issue.message}{times} — linha {issue.line}"]
    lines += [f"  - `{ctx}`" for ctx in issue.context]
    return lines


def _group_tool_issues(digests: list[LogDigest]) -> dict[tuple, list[str]]:
    grouped: dict[tuple, list[str]] = {}
    for d in digests:
        for issue in d.issues:
            if issue.source == "tool":
                key = (issue.severity, issue.code, issue.message)
                grouped.setdefault(key, []).append(d.path)
    return grouped
