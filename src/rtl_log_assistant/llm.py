"""Diagnóstico com Claude a partir do resumo estruturado dos logs.

Só o resumo gerado pelos parsers é enviado (não o log bruto): isso reduz o
custo, evita mandar ruído para o modelo e deixa claro de onde vem cada fato.
"""

from __future__ import annotations

import anthropic

from .parsers import LogDigest
from .report import digests_to_json

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"

SYSTEM_PROMPT = """\
Você é uma engenheira de verificação de hardware experiente, ajudando uma \
estudante a entender os resultados do fluxo RTL dela (lint com SpyGlass, \
compilação e simulação com VCS).

Você recebe um JSON com o resumo de cada log, extraído por um parser \
determinístico. Campos: tool (etapa), target (módulo ou testbench), status \
(pass/warn/fail/unknown), counts, issues (severidade, mensagem, linha no log, \
código, origem "design" ou "tool", contexto) e highlights.

Escreva um relatório curto em português, em Markdown, com estas seções:
1. **Status geral**: uma frase dizendo se o fluxo está saudável.
2. **Problemas por prioridade**: falhas primeiro, depois avisos. Para cada um, \
cite o arquivo de log e a linha.
3. **Causa provável**: o que costuma causar cada problema em RTL/testbench.
4. **Próximos passos**: ações concretas e verificáveis.

Regras:
- Use apenas os dados do JSON. Se algo não puder ser concluído a partir deles, \
diga isso em vez de supor.
- Separe problemas do design (origin "design") de avisos do ambiente da \
ferramenta (origin "tool"), que normalmente não exigem mudança no RTL.
- Status "unknown" significa que o log não permite concluir: trate como algo \
a investigar, não como sucesso.
- Se tudo passou, diga isso em poucas linhas e não invente problemas.
"""


class DiagnosisError(RuntimeError):
    """O modelo não conseguiu gerar o diagnóstico."""


def diagnose(
    digests: list[LogDigest],
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> str:
    client = client or anthropic.Anthropic()
    payload = digests_to_json(digests)

    try:
        response = _request(client, payload, model, effort)
    except TypeError as e:
        # O SDK sinaliza credencial ausente com TypeError na hora do pedido.
        if "authentication" in str(e):
            raise DiagnosisError(
                "Nenhuma credencial da Anthropic encontrada. Defina ANTHROPIC_API_KEY "
                "ou rode sem --ai para ter só o relatório."
            ) from e
        raise

    if response.stop_reason == "refusal":
        raise DiagnosisError("O modelo recusou gerar o diagnóstico para esses logs.")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise DiagnosisError("O modelo não retornou texto.")
    if response.stop_reason == "max_tokens":
        text += "\n\n_(Resposta interrompida pelo limite de tokens.)_"
    return text + "\n"


def _request(client, payload: str, model: str, effort: str):
    return client.beta.messages.create(
        model=model,
        max_tokens=16000,
        # Se o modelo recusar por política de segurança, a API refaz o pedido
        # automaticamente com um modelo de fallback.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": effort},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"<resumo_dos_logs>\n{payload}\n</resumo_dos_logs>\n\n"
                "Escreva o relatório de diagnóstico.",
            }
        ],
    )
