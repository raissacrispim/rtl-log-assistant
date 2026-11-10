from pathlib import Path

import pytest

from rtl_log_assistant.parsers import detect_tool, parse_file, parse_text

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "logs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize("log", sorted(EXAMPLES.glob("*.log")), ids=lambda p: p.name)
def test_real_logs_all_pass(log):
    digest = parse_file(log)
    assert digest.status == "pass"
    assert digest.target, "o alvo (módulo/testbench) deve ser identificado"
    assert digest.counts.get("error", 0) == 0
    assert not [i for i in digest.issues if i.source == "design"]


def test_tool_detection_on_real_logs():
    tools = {p.name.split("_")[0]: parse_file(p).tool for p in EXAMPLES.glob("*.log")}
    assert tools == {"lint": "spyglass", "compile": "vcs-compile", "sim": "vcs-sim"}


def test_spyglass_real_log_details():
    d = parse_file(EXAMPLES / "lint_minisoc_aes_mix_columns.log")
    assert d.target == "minisoc_aes_mix_columns"
    assert d.counts == {"fatal": 0, "error": 0, "warning": 0, "info": 2}
    (deprecated,) = d.issues
    assert deprecated.code == "38" and deprecated.source == "tool"
    assert "POLICY_DEPRECATED" in deprecated.message


def test_sim_real_log_keeps_pass_line():
    d = parse_file(EXAMPLES / "sim_minisoc_aes_sbox_tb.log")
    assert d.target == "minisoc_aes_sbox_tb"
    assert any("514 verificacoes" in h for h in d.highlights)


def test_compile_error():
    d = parse_file(FIXTURES / "compile_error.log")
    assert d.tool == "vcs-compile" and d.status == "fail"
    assert d.counts == {"fatal": 0, "error": 1, "warning": 1}
    err = next(i for i in d.issues if i.severity == "error")
    assert err.code == "SE" and err.line == 7
    assert any("minisoc_aes_mix_columns.sv\", 42" in c for c in err.context)
    warn = next(i for i in d.issues if i.severity == "warning")
    assert warn.code == "TFIPC"


def test_sim_failure():
    d = parse_file(FIXTURES / "sim_fail.log")
    assert d.status == "fail"
    assert d.counts == {"fatal": 1, "error": 2, "warning": 0}
    first = next(i for i in d.issues if i.severity == "error")
    assert first.message.startswith("S-box: entrada 8'h53")
    assert first.context[0].startswith('"tb/minisoc_aes_sbox_tb.sv", 57')
    fatal = next(i for i in d.issues if i.severity == "fatal")
    assert fatal.message == "FAIL: 2 de 514 verificacoes falharam"


def test_sim_without_pass_is_unknown_not_pass():
    d = parse_file(FIXTURES / "sim_incomplete.log")
    assert d.status == "unknown"
    assert any("$finish" in h for h in d.highlights)


def test_spyglass_with_errors():
    d = parse_file(FIXTURES / "lint_errors.log")
    assert d.status == "fail"
    assert d.target == "minisoc_aes_shift_rows"
    assert d.counts == {"fatal": 0, "error": 2, "warning": 3, "info": 1}


def test_repeated_messages_are_grouped():
    text = (
        "Chronologic VCS (TM)\n"
        "Warning-[TFIPC] Too few instance port connections\n\n"
        "Warning-[TFIPC] Too few instance port connections\n\n"
        "CPU time: .1 seconds to compile\n"
    )
    d = parse_text(text)
    (warn,) = d.issues
    assert warn.occurrences == 2
    assert d.counts["warning"] == 2 and d.status == "warn"


def test_unknown_tool():
    assert detect_tool("make: *** [all] Error 1") == "unknown"
    d = parse_text("make: *** [all] Error 1\n")
    assert d.status == "fail"
