import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rtl_log_assistant.cli import main
from rtl_log_assistant.llm import (
    DEFAULT_MODELS,
    SYSTEM_PROMPT,
    DiagnosisError,
    diagnose,
    pick_gemini_model,
)
from rtl_log_assistant.parsers import parse_file
from rtl_log_assistant.report import overall_status, render_markdown

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "logs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ----------------------------- relatório ---------------------------------- #


def test_report_groups_tool_warning_once():
    digests = [parse_file(p) for p in EXAMPLES.glob("*.log")]
    md = render_markdown(digests)
    assert "13 logs" in md
    assert md.count("POLICY_DEPRECATED") == 1
    assert "em 7 log(s)" in md
    assert "## Problemas por log" not in md


def test_report_and_status_with_failures():
    digests = [parse_file(p) for p in FIXTURES.glob("*.log")]
    assert overall_status(digests) == "fail"
    md = render_markdown(digests)
    assert "Error-[SE]" not in md  # o código aparece normalizado
    assert "[SE] Syntax error" in md
    assert "FAIL: 2 de 514 verificacoes falharam" in md


# -------------------------------- CLI ------------------------------------- #


def test_cli_exit_codes(capsys):
    assert main([str(EXAMPLES)]) == 0
    assert main([str(FIXTURES)]) == 1
    assert main(["nao/existe.log"]) == 2
    assert "Caminho não encontrado" in capsys.readouterr().err


def test_cli_json_is_valid(capsys):
    main([str(EXAMPLES), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 13
    assert {d["tool"] for d in data} == {"spyglass", "vcs-compile", "vcs-sim"}


def test_cli_writes_output_file(tmp_path):
    out = tmp_path / "relatorio.md"
    main([str(EXAMPLES), "-o", str(out)])
    assert out.read_text(encoding="utf-8").startswith("# Relatório de logs RTL")


# ------------------------- LLM (cliente falso) ----------------------------- #


class FakeClient:
    """Imita client.beta.messages.create sem chamar a API."""

    def __init__(self, text="Tudo certo.", stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=text),
            ],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._response = response

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_diagnose_sends_digest_not_raw_log():
    client = FakeClient("## Status geral\nFalhou.")
    digests = [parse_file(p) for p in FIXTURES.glob("*.log")]
    text = diagnose(digests, client=client)

    assert text == "## Status geral\nFalhou.\n"
    (call,) = client.calls
    assert call["model"] == DEFAULT_MODELS["claude"]
    assert call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "medium"}
    content = call["messages"][0]["content"]
    assert "FAIL: 2 de 514 verificacoes falharam" in content
    assert "Checking Rule" not in content  # ruído do log não vai para o modelo


def test_diagnose_refusal_raises():
    client = FakeClient(stop_reason="refusal")
    with pytest.raises(DiagnosisError):
        diagnose([parse_file(FIXTURES / "sim_fail.log")], client=client)


def test_diagnose_marks_truncated_output():
    client = FakeClient("Parcial", stop_reason="max_tokens")
    text = diagnose([parse_file(FIXTURES / "sim_fail.log")], client=client)
    assert "limite de tokens" in text


def test_diagnose_without_credentials_gives_clear_error():
    class NoAuthClient(FakeClient):
        def _create(self, **kwargs):
            raise TypeError('"Could not resolve authentication method. ..."')

    with pytest.raises(DiagnosisError, match="ANTHROPIC_API_KEY"):
        diagnose([parse_file(FIXTURES / "sim_fail.log")], client=NoAuthClient())


# ------------------------ Gemini (cliente falso) --------------------------- #


class FakeGeminiClient:
    """Imita client.models.generate_content do SDK google-genai."""

    def __init__(self, text="Tudo certo.", finish="STOP", block_reason=None, error=None):
        from google.genai import types

        self.calls = []
        self._error = error
        self._response = SimpleNamespace(
            text=text,
            prompt_feedback=SimpleNamespace(block_reason=block_reason),
            candidates=[SimpleNamespace(finish_reason=types.FinishReason[finish])],
        )
        self.models = SimpleNamespace(generate_content=self._generate, list=self._list)

    @staticmethod
    def _list():
        def model(name, actions=("generateContent",)):
            return SimpleNamespace(name=f"models/{name}", supported_actions=list(actions))

        return [
            model("gemini-3.6-flash"),
            model("gemini-3.8-flash"),
            model("gemini-3.8-flash-lite"),
            model("gemini-3.9-flash-preview"),
            model("text-embedding-005", ("embedContent",)),
        ]

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def test_gemini_request_uses_same_prompt_and_digest():
    client = FakeGeminiClient("## Status geral\nFalhou.")
    digests = [parse_file(p) for p in FIXTURES.glob("*.log")]
    text = diagnose(digests, provider="gemini", client=client)

    assert text == "## Status geral\nFalhou.\n"
    (call,) = client.calls
    assert call["model"] == "gemini-3.8-flash"  # escolhido automaticamente
    assert call["config"].system_instruction == SYSTEM_PROMPT
    assert "FAIL: 2 de 514 verificacoes falharam" in call["contents"]
    assert "Checking Rule" not in call["contents"]


def test_gemini_custom_model():
    client = FakeGeminiClient()
    diagnose([parse_file(FIXTURES / "sim_fail.log")], provider="gemini", model="outro", client=client)
    assert client.calls[0]["model"] == "outro"


@pytest.mark.parametrize(
    "kwargs", [{"finish": "SAFETY"}, {"block_reason": "OTHER"}, {"text": None}]
)
def test_gemini_blocked_or_empty_raises(kwargs):
    with pytest.raises(DiagnosisError):
        diagnose(
            [parse_file(FIXTURES / "sim_fail.log")],
            provider="gemini",
            client=FakeGeminiClient(**kwargs),
        )


def test_gemini_truncated_output():
    client = FakeGeminiClient("Parcial", finish="MAX_TOKENS")
    text = diagnose([parse_file(FIXTURES / "sim_fail.log")], provider="gemini", client=client)
    assert "limite de tokens" in text


def test_gemini_rate_limit_message():
    from google.genai import errors

    err = errors.ClientError(429, {"error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(DiagnosisError, match="plano gratuito"):
        diagnose(
            [parse_file(FIXTURES / "sim_fail.log")],
            provider="gemini",
            client=FakeGeminiClient(error=err),
        )


def test_cli_gemini_without_key(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert main([str(FIXTURES / "sim_fail.log"), "--ai", "--provider", "gemini"]) == 3
    assert "aistudio.google.com" in capsys.readouterr().err


@pytest.mark.parametrize(
    "names, expected",
    [
        (["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.8-flash-001"], "gemini-3.8-flash"),
        (["gemini-3.8-flash-lite", "gemini-3.6-flash"], "gemini-3.6-flash"),
        (["gemini-3.9-flash-preview"], "gemini-3.9-flash-preview"),  # só sobrou preview
        (["gemini-3.1-pro"], "gemini-3.1-pro"),  # nenhum Flash: usa o que houver
    ],
)
def test_pick_gemini_model(names, expected):
    assert pick_gemini_model(names) == expected


def test_pick_gemini_model_empty():
    with pytest.raises(DiagnosisError):
        pick_gemini_model([])


def test_cli_list_models(monkeypatch, capsys):
    import rtl_log_assistant.cli as cli

    monkeypatch.setattr(cli, "list_gemini_models", lambda: ["gemini-3.6-flash", "gemini-3.8-flash"])
    assert main(["--list-models"]) == 0
    assert capsys.readouterr().out.split() == ["gemini-3.6-flash", "gemini-3.8-flash"]


def test_cli_requires_paths_without_list_models():
    with pytest.raises(SystemExit):
        main([])
