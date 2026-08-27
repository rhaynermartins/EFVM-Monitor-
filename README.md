# EFVM Monitor

Prova de conceito em Python para consultar a disponibilidade de passagens do Trem de
Passageiros da Estrada de Ferro Vitória a Minas (EFVM) e emitir um alerta. O projeto
**não compra passagem** e não acessa etapas de login, CPF, reserva, assento ou pagamento.

## Escopo atual — Fases 1 e 2

- configurar origem, destino, data, classe e quantidade de passageiros em `.env`;
- consultar somente interfaces públicas usadas antes do fluxo de compra;
- informar claramente `TEM_VAGA`, `SEM_VAGA` ou `ERRO`;
- registrar logs no terminal e em `logs/efvm-monitor.log`;
- emitir alerta no terminal e, opcionalmente, por webhook;
- executar uma vez ou repetir a consulta em intervalo controlado.
- escolher origem, destino, data e classe em uma interface local;
- iniciar, acompanhar e parar um único monitoramento pelo navegador;
- emitir, opcionalmente, uma notificação nativa do navegador.

Não fazem parte deste MVP: login, dados pessoais, escolha ou bloqueio de assento, reserva,
pagamento, compra, solução de CAPTCHA e qualquer tentativa de contornar bloqueios ou
mecanismos anti-bot.

## Investigação legítima do portal

Em 27 de agosto de 2026, o fluxo público indicado pela [página oficial da Vale](https://www.vale.com/pt/trem-de-passageiros)
direcionava para o [Portal do Trem de Passageiros](https://tremdepassageiros.vale.com/sgpweb/portal/index.html#/home).
Ao carregar o formulário, o JavaScript público do próprio portal fazia consultas HTTP de
leitura para:

- obter ferrovias e a janela vigente de venda;
- obter estações, classes e tipos de tarifa;
- pesquisar passagens antes de qualquer início de venda.

Por isso, esta fase usa HTTP em vez de automação visual. A prova manual retornou tanto uma
resposta explícita sem passagens quanto uma resposta com opção disponível. O monitor não
usa nem persiste o `tokenCompra` presente em respostas com vaga.

Essas interfaces não são uma API pública documentada e podem mudar sem aviso. Use um
intervalo responsável, confira os termos vigentes do portal e interrompa o monitor caso o
site apresente CAPTCHA, restrição de acesso ou solicite autenticação. O intervalo mínimo
aceito pelo programa é 60 segundos; o exemplo usa 300 segundos.

## Requisitos

- Python 3.11 ou superior;
- acesso à internet;
- Git, se quiser contribuir.

## Instalação local

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Para usar o CLI, edite o `.env` e ajuste principalmente:

```dotenv
EFVM_ORIGIN=Belo Horizonte
EFVM_DESTINATION=Pedro Nolasco (Cariacica / Vitória)
EFVM_TRAVEL_DATE=2026-09-15
EFVM_CLASS=Econômica
EFVM_PASSENGERS=1
```

Os nomes precisam corresponder aos nomes reais retornados pelo portal. Também é possível
usar o ID numérico da estação ou classe. O programa não aceita correspondência parcial para
não escolher uma estação errada por aproximação.

## Interface local

Inicie o servidor:

```bash
efvm-monitor-web
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000) no navegador. A tela permite:

- selecionar origem e destino na lista atual do portal;
- escolher uma data dentro da janela vigente de venda;
- selecionar classe Econômica ou Executiva;
- monitorar exatamente 1 passageiro;
- escolher intervalos a partir de 60 segundos;
- iniciar e parar o monitoramento;
- acompanhar `AGUARDANDO`, `TEM_VAGA`, `SEM_VAGA`, `ERRO` e `PARADO`;
- ativar notificação do navegador quando aparecer uma vaga.

O servidor escuta apenas em `127.0.0.1` e não fica exposto à rede local. Para usar outra
porta, altere `EFVM_WEB_PORT` no `.env`.

O estado fica somente em memória. Encerrar o servidor encerra o monitoramento ativo.

## Execução pelo terminal

Consulta única:

```bash
efvm-monitor
```

Monitoramento contínuo com o intervalo definido por `EFVM_CHECK_INTERVAL_SECONDS`:

```bash
efvm-monitor --watch
```

Também é possível apontar para outro arquivo de configuração:

```bash
efvm-monitor --env-file caminho/consulta.env
```

Exemplos de saída:

```text
TEM_VAGA | O portal retornou 1 opção(ões) disponível(is).
SEM_VAGA | O portal informou que não há passagens para a pesquisa.
ERRO | A data excede a janela atual de venda de 45 dias.
```

Códigos de saída da consulta única:

| Código | Resultado |
| ---: | --- |
| `0` | `TEM_VAGA` |
| `1` | `SEM_VAGA` |
| `2` | `ERRO`, inclusive falha no webhook |

No modo contínuo, o alerta é emitido quando o estado muda para `TEM_VAGA`, evitando um novo
alerta idêntico a cada ciclo.

## Webhook opcional

Defina `ALERT_WEBHOOK_URL` para receber um `POST` JSON quando houver vaga:

```dotenv
ALERT_WEBHOOK_URL=https://seu-servico.example/alerta
```

O corpo contém apenas status, trajeto, data, classe, quantidade de opções e o link do portal.
Não contém CPF, credenciais, dados de pagamento ou token de compra. Sem webhook, o alerta
continua disponível no terminal e no arquivo de log.

## Testes e qualidade

```bash
pytest
ruff check .
```

Os testes locais verificam a classificação dos três estados, o serviço em segundo plano, as
cinco rotas da interface e as validações do formulário. Eles não fazem chamadas ao portal.

## Rotas locais

| Método | Rota | Função |
| --- | --- | --- |
| `GET` | `/` | Exibe a interface |
| `GET` | `/api/catalogo` | Lista estações, classes e janela de venda |
| `POST` | `/api/monitoramento` | Inicia um monitoramento |
| `GET` | `/api/monitoramento` | Consulta o estado atual |
| `DELETE` | `/api/monitoramento` | Solicita a parada |

## Estrutura

```text
src/efvm_monitor/
├── checker.py   # catálogos públicos, consulta e classificação
├── cli.py       # execução única/contínua e logs
├── config.py    # leitura e validação do .env
├── monitor.py   # ciclo em segundo plano e estado em memória
├── network.py   # HTTPS verificado com certificados do sistema
├── notifier.py  # alerta local e webhook opcional
├── web.py       # servidor e rotas locais
├── static/      # estilos e interação do formulário
└── templates/   # página HTML
tests/
├── test_checker.py
├── test_monitor.py
└── test_web.py
```

## Limitações conhecidas

- o portal pode alterar URLs, campos ou respostas;
- disponibilidade pode desaparecer entre o alerta e a compra manual;
- `TEM_VAGA` significa que o portal retornou ao menos uma opção naquele instante, não uma
  reserva garantida;
- uma resposta inesperada é classificada como `ERRO`, nunca como ausência de vaga;
- o monitor depende da janela de venda informada dinamicamente pelo portal.
- somente um monitoramento pode ficar ativo por processo;
- o monitoramento não é retomado automaticamente após fechar o servidor.

## Uso responsável

Este projeto é independente e não possui afiliação com a Vale. Consulte com baixa frequência,
não execute várias instâncias para a mesma pesquisa e use sempre o site ou aplicativo oficial
para qualquer compra.
