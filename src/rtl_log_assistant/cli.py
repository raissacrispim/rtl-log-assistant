"""Linha de comando: rtl-log-assistant <logs ou pastas> [opções]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .llm import (
    DEFAULT_EFFORT,
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    PROVIDERS,
    DiagnosisError,
    diagnose,
    list_gemini_models,
)
from .parsers import STATUS_FAIL, parse_file
from .report import digests_to_json, overall_status, render_markdown


def collect_logs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(p.glob("*.log")))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"Caminho não encontrado: {p}")
    return files


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rtl-log-assistant",
        description="Resume logs de lint (SpyGlass) e simulação (VCS) e, "
        "opcionalmente, gera um diagnóstico com LLM (Claude ou Gemini).",
    )
    ap.add_argument("paths", nargs="*", help="arquivos .log ou pastas com logs")
    ap.add_argument(
        "--ai",
        action="store_true",
        help="adiciona diagnóstico gerado por LLM (precisa de chave do provedor)",
    )
    ap.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=PROVIDERS,
        help=f"provedor do LLM (padrão: {DEFAULT_PROVIDER}; gemini tem plano gratuito)",
    )
    ap.add_argument("--json", action="store_true", help="imprime o resumo estruturado em JSON")
    ap.add_argument("-o", "--output", help="salva o relatório neste arquivo")
    ap.add_argument(
        "--model",
        help="modelo (padrão: "
        + ", ".join(f"{p}: {m}" for p, m in DEFAULT_MODELS.items())
        + ")",
    )
    ap.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"esforço de raciocínio, só para Claude (padrão: {DEFAULT_EFFORT})",
    )
    ap.add_argument(
        "--list-models",
        action="store_true",
        help="lista os modelos do Gemini disponíveis para a sua chave e sai",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_models:
        try:
            print("\n".join(list_gemini_models()))
        except DiagnosisError as e:
            print(f"erro: {e}", file=sys.stderr)
            return 3
        return 0
    if not args.paths:
        parser.error("informe pelo menos um arquivo .log ou pasta")

    try:
        files = collect_logs(args.paths)
    except FileNotFoundError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    if not files:
        print("erro: nenhum arquivo .log encontrado", file=sys.stderr)
        return 2

    digests = [parse_file(f) for f in files]

    if args.json:
        output = digests_to_json(digests) + "\n"
    else:
        output = render_markdown(digests)
        if args.ai:
            title = "Claude" if args.provider == "claude" else "Gemini"
            try:
                output += f"\n## Diagnóstico ({title})\n\n" + diagnose(
                    digests, provider=args.provider, model=args.model, effort=args.effort
                )
            except DiagnosisError as e:
                print(f"erro: {e}", file=sys.stderr)
                return 3

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Relatório salvo em {args.output}")
    else:
        sys.stdout.write(output)

    # Código de saída útil em CI/Makefile: 1 se alguma etapa falhou.
    return 1 if overall_status(digests) == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
