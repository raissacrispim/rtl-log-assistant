"""Diagnóstico com LLM (Claude ou Gemini) a partir do resumo dos logs.

Só o resumo gerado pelos parsers é enviado (não o log bruto): isso reduz o
custo, evita mandar ruído para o modelo e deixa claro de onde vem cada fato.

O prompt é o mesmo para os dois provedores. Só muda a chamada à API.
"""

from __future__ import annotations

from .parsers import LogDigest
from .report import digests_to_json

PROVIDERS = ("claude", "gemini")
DEFAULT_PROVIDER = "claude"
DEFAULT_MODELS = {
    "claude": "claude-opus-5-5",
    # Alias que aponta sempre para o Flash mais recente (disponível no plano gratuito).
    "gemini": "gemini-flash-latest",
}
DEFAULT_EFFORT = "medium"
MAX_OUTPUT_TOKENS = 16000

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


def build_user_message(digests: list[LogDigest]) -> str:
    return (
        f"<resumo_dos_logs>\n{digests_to_json(digests)}\n</resumo_dos_logs>\n\n"
        "Escreva o relatório de diagnóstico."
    )


def diagnose(
    digests: list[LogDigest],
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
    effort: str = DEFAULT_EFFORT,
    client=None,
) -> str:
    """Gera o diagnóstico em Markdown. `client` permite injetar um cliente falso nos testes."""
    if provider not in PROVIDERS:
        raise ValueError(f"provedor desconhecido: {provider}")
    model = model or DEFAULT_MODELS[provider]
    message = build_user_message(digests)
    if provider == "claude":
        text, truncated = _diagnose_claude(message, model, effort, client)
    else:
        text, truncated = _diagnose_gemini(message, model, client)

    text = (text or "").strip()
    if not text:
        raise DiagnosisError("O modelo não retornou texto.")
    if truncated:
        text += "\n\n_(Resposta interrompida pelo limite de tokens.)_"
    return text + "\n"


# --------------------------------------------------------------------------- #
# Claude (Anthropic)
# --------------------------------------------------------------------------- #


def _diagnose_claude(message: str, model: str, effort: str, client) -> tuple[str, bool]:
    import anthropic

    client = client or anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            # Se o modelo recusar por política de segurança, a API refaz o pedido
            # automaticamente com um modelo de fallback.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
        )
    except TypeError as e:
        # O SDK sinaliza credencial ausente com TypeError na hora do pedido.
        if "authentication" in str(e):
            raise DiagnosisError(
                "Nenhuma credencial da Anthropic encontrada. Defina ANTHROPIC_API_KEY, "
                "use --provider gemini ou rode sem --ai."
            ) from e
        raise
    except anthropic.AuthenticationError as e:
        raise DiagnosisError("Chave da Anthropic inválida.") from e
    except anthropic.APIConnectionError as e:
        raise DiagnosisError("Sem conexão com a API da Anthropic.") from e
    except anthropic.APIStatusError as e:
        raise DiagnosisError(f"Erro da API da Anthropic ({e.status_code}): {e.message}") from e

    if response.stop_reason == "refusal":
        raise DiagnosisError("O modelo recusou gerar o diagnóstico para esses logs.")
    text = "".join(block.text for block in response.content if block.type == "text")
    return text, response.stop_reason == "max_tokens"


# --------------------------------------------------------------------------- #
# Gemini (Google)
# --------------------------------------------------------------------------- #

_GEMINI_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


def _diagnose_gemini(message: str, model: str, client) -> tuple[str, bool]:
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError as e:
        raise DiagnosisError(
            'Pacote do Gemini não instalado. Rode: pip install -e ".[gemini]"'
        ) from e

    if client is None:
        try:
            client = genai.Client()  # lê GEMINI_API_KEY (ou GOOGLE_API_KEY)
        except ValueError as e:
            raise DiagnosisError(
                "Nenhuma chave do Gemini encontrada. Defina GEMINI_API_KEY "
                "(crie uma grátis em aistudio.google.com) ou rode sem --ai."
            ) from e

    try:
        response = client.models.generate_content(
            model=model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
    except errors.APIError as e:
        hint = " (limite do plano gratuito? espere um minuto)" if e.code == 429 else ""
        raise DiagnosisError(f"Erro da API do Gemini ({e.code}): {e.message}{hint}") from e

    feedback = response.prompt_feedback
    if feedback is not None and feedback.block_reason:
        raise DiagnosisError(f"O Gemini bloqueou o pedido: {feedback.block_reason}")
    finish = _finish_reason(response)
    if finish in _GEMINI_BLOCKED:
        raise DiagnosisError(f"O Gemini interrompeu a resposta: {finish}")
    return response.text or "", finish == "MAX_TOKENS"


def _finish_reason(response) -> str | None:
    if not response.candidates:
        return None
    reason = response.candidates[0].finish_reason
    return getattr(reason, "name", reason)
