# rtl-log-assistant

Ferramenta de linha de comando que lê os logs do fluxo RTL (lint com
**SpyGlass**, compilação e simulação com **VCS**), resume o que importa e,
opcionalmente, pede ao **Claude** um diagnóstico em português, com a causa
provável e os próximos passos.

Nasceu do meu projeto de um acelerador AES-128 com interface SPI
([aes-spi-crypto-accelerator](https://github.com/raissacrispim/aes-spi-crypto-accelerator)).
Cada `make test` gera 13 logs e **3.348 linhas**, quase todas ruído (banner,
carregamento de políticas, centenas de "Checking Rule ..."). O que eu queria
saber cabia em uma tabela.

## Como funciona

```
logs (.log) ──► parsers determinísticos ──► resumo estruturado (JSON)
                                                   │
                                   ┌───────────────┴───────────────┐
                                   ▼                               ▼
                       relatório Markdown (sem IA)      diagnóstico com Claude (--ai)
```

1. **Parsers** (`parsers.py`) reconhecem a ferramenta e extraem só os fatos:
   módulo ou testbench, status (`pass`/`warn`/`fail`/`unknown`), contagem de
   erros e avisos, mensagens com o número da linha e o contexto, e linhas
   `PASS`/`FAIL` do testbench. Mensagens repetidas são agrupadas.
2. **Relatório** (`report.py`) monta uma tabela com o status de cada etapa e
   separa os problemas do design dos avisos do ambiente da ferramenta, como a
   depreciação de políticas do SpyGlass.
3. **Diagnóstico** (`llm.py`) envia **só o resumo**, nunca o log bruto, para a
   API do Claude, com instruções para responder com base nos dados e dizer
   quando algo não pode ser concluído.

### Decisões de projeto

- **Parser primeiro, LLM depois.** Nos logs do projeto AES, o resumo tem
  **8.360 caracteres, contra 280.808 do log bruto (3%)**. Isso deixa o pedido
  mais barato e mais rápido, e cada afirmação do modelo pode ser conferida no
  JSON e na linha do log.
- **A ferramenta funciona sem IA.** O relatório determinístico já resolve o
  dia a dia. O `--ai` é um complemento, não uma dependência.
- **`unknown` não é sucesso.** Uma simulação sem linha `PASS` (por exemplo,
  interrompida antes do `$finish`) aparece como "não dá para concluir", e não
  como aprovada.
- **Útil em CI.** O comando sai com código `1` se alguma etapa falhou, então
  pode entrar num `Makefile` ou num workflow.

## Instalação

Requer Python 3.10+.

```bash
git clone https://github.com/raissacrispim/rtl-log-assistant.git
cd rtl-log-assistant
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Uso

```bash
rtl-log-assistant examples/logs                    # relatório Markdown no terminal
rtl-log-assistant examples/logs -o relatorio.md    # salva em arquivo
rtl-log-assistant tests/fixtures --json            # resumo estruturado
rtl-log-assistant tests/fixtures --ai              # + diagnóstico do Claude
```

Para o `--ai`, configure uma chave da API da Anthropic:

```bash
export ANTHROPIC_API_KEY=...
```

Opções: `--model` (padrão `claude-opus-5-5`) e `--effort`
(`low`/`medium`/`high`/`xhigh`/`max`, padrão `medium`).

| Código de saída | Significado |
|:-:|---|
| 0 | nenhuma etapa falhou |
| 1 | alguma etapa falhou |
| 2 | caminho inválido ou nenhum `.log` encontrado |
| 3 | erro ao gerar o diagnóstico com IA |

## Exemplos

- [`examples/relatorio_aes.md`](examples/relatorio_aes.md): os 13 logs reais do
  projeto AES, todos passando.
- [`examples/relatorio_falhas.md`](examples/relatorio_falhas.md): logs com erro
  de sintaxe, falha de simulação, simulação interrompida e violações de lint.

Os logs em `examples/logs/` são reais. Os de `tests/fixtures/` são sintéticos,
escritos no formato do VCS e do SpyGlass para testar os casos de falha.

## Testes

```bash
pytest -q
```

São 31 testes. Eles cobrem os logs reais, os casos de falha, o relatório, a
CLI e a integração com o Claude. A chamada à API é substituída por um cliente
falso, então os testes rodam sem chave e sem custo. O GitHub Actions roda tudo
em Python 3.10 e 3.13 a cada push.

## Limitações e próximos passos

- O log do SpyGlass só traz a **contagem** de violações. As mensagens
  individuais ficam no `moresimple.rpt`. O próximo passo é ler esse relatório
  também.
- Suporte a outras ferramentas (Verilator, Icarus, Questa) e a logs de síntese
  (Design Compiler).
- Modo `--watch`, para gerar o relatório ao fim de cada `make test`.

## Licença

MIT
