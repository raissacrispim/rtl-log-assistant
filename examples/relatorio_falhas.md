# Relatório de logs RTL

**Status geral:** ❌ `fail` · 4 logs · 43 linhas analisadas

| Status | Etapa | Alvo | Erros | Avisos | Log |
|:-:|---|---|--:|--:|---|
| ❌ | Lint (SpyGlass) | `minisoc_aes_shift_rows` | 2 | 3 | `lint_errors.log` |
| ❌ | Compilação (VCS) | `?` | 1 | 1 | `compile_error.log` |
| ❌ | Simulação (VCS) | `minisoc_aes_sbox_tb` | 3 | 0 | `sim_fail.log` |
| ❔ | Simulação (VCS) | `minisoc_aes_round_tb` | 0 | 0 | `sim_incomplete.log` |

## Problemas por log

### ❌ `compile_error.log`
- **error** [SE] Syntax error — linha 7
  - `Following verilog source has syntax error :`
  - `"rtl/minisoc_aes/minisoc_aes_mix_columns.sv", 42: token is 'endmodule'`
  - `endmodule`
- **warning** [TFIPC] Too few instance port connections — linha 12
  - `rtl/minisoc_aes/minisoc_aes_sub_bytes.sv, 18`
  - `The above instance has fewer port connections than the module definition.`
- CPU time: .210 seconds to compile

### ❌ `lint_errors.log`
- SpyGlass Exit Code 2 (Rule-checking completed with errors)

### ❌ `sim_fail.log`
- **error** S-box: entrada 8'h53 -> obtido 8'h00, esperado 8'hed — linha 5
  - `"tb/minisoc_aes_sbox_tb.sv", 57: minisoc_aes_sbox_tb: at time 12000 ps`
- **error** S-box: entrada 8'h54 -> obtido 8'h00, esperado 8'h20 — linha 7
  - `"tb/minisoc_aes_sbox_tb.sv", 57: minisoc_aes_sbox_tb: at time 13000 ps`
- **fatal** FAIL: 2 de 514 verificacoes falharam — linha 9
  - `"tb/minisoc_aes_sbox_tb.sv", 76: minisoc_aes_sbox_tb: at time 258000 ps`
- FAIL: 2 de 514 verificacoes falharam
- Tempo simulado: 258000 ps

### ❔ `sim_incomplete.log`
- Simulação sem $finish: pode ter sido interrompida.


## Avisos do ambiente da ferramenta

_Não indicam problema no RTL, mas vale acompanhar._

- **warning** [38] POLICY_DEPRECATED: Policies lint have been deprecated. — em 1 log(s)
