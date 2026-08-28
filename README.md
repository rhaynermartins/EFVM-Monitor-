# EFVM Monitor

Prova de conceito em Python para consultar a disponibilidade de passagens do Trem de
Passageiros da Estrada de Ferro Vitória a Minas (EFVM) e emitir um alerta. O projeto
**não compra passagem** e não acessa etapas de login, CPF, reserva, assento ou pagamento.

## Escopo atual — Fases 1, 2, 3, 4, 4.1 e 4.2

- configurar origem, destino, data, classe e quantidade de passageiros em `.env`;
- consultar somente interfaces públicas usadas antes do fluxo de compra;
- informar claramente `TEM_VAGA`, `SEM_VAGA` ou `ERRO`;
- registrar logs no terminal e em `logs/efvm-monitor.log`;
- emitir alerta no terminal e, opcionalmente, por webhook;
- executar uma vez ou repetir a consulta em intervalo controlado.
- escolher origem, destino, data e classe em uma interface local;
- iniciar, acompanhar e parar um único monitoramento pelo navegador;
- instalar a interface como PWA e receber Web Push padrão mesmo com a página fechada;
- persistir a configuração e o estado em SQLite local;
- registrar o histórico de `TEM_VAGA`, `SEM_VAGA` e `ERRO`;
- recuperar o último monitor salvo e retomar automaticamente o que estava ativo.
- usar Web Push com VAPID como canal principal gratuito;
- manter SMS/Twilio e WhatsApp Cloud API como canais opcionais, desativados por padrão;
- evitar mensagens repetidas enquanto a disponibilidade continuar `TEM_VAGA`;
- registrar tentativas, sucessos e falhas dos alertas no SQLite;
- manter o monitor funcionando mesmo quando um canal estiver indisponível.

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
- instalar a PWA em navegadores compatíveis;
- ativar Web Push explicitamente neste dispositivo, sem pedido automático de permissão;
- testar ou desativar o Web Push pela própria tela;
- ativar SMS e WhatsApp como alternativas opcionais, quando configurados;
- consultar as verificações recentes persistidas no painel.

O fluxo visual segue a ordem de uso: primeiro aparece **PASSO 1 — Configure a viagem** e,
logo abaixo no mobile ou ao lado no desktop, **PASSO 2 — Acompanhe o estado**. O card de
acompanhamento nunca é movido para antes da configuração.

O servidor escuta apenas em `127.0.0.1` e não fica exposto à rede local. Para usar outra
porta, altere `EFVM_WEB_PORT` no `.env`.

Em desktop, `localhost`/`127.0.0.1` é aceito pelos navegadores como contexto seguro de
desenvolvimento. Para instalar e testar em um telefone físico, o aparelho precisa acessar o
servidor por uma URL HTTPS válida. A publicação contínua e sua configuração de HTTPS ficam
fora desta fase; não exponha o servidor de desenvolvimento diretamente na internet.

O estado é persistido no caminho definido por `EFVM_DATABASE_PATH`, cujo padrão é
`data/efvm-monitor.db`. Ao reiniciar o servidor, o último monitor é recuperado. Se ele estava
ativo, a consulta é retomada automaticamente; se estava pausado, permanece parada.

## Persistência local

O SQLite é inicializado automaticamente por uma migration idempotente. A inicialização usa
somente `CREATE TABLE IF NOT EXISTS` e `CREATE INDEX IF NOT EXISTS`: não há `DROP`, `DELETE`
ou limpeza automática de dados.

Tabelas atuais:

- `monitoring_jobs`: configuração, estado, último resultado e datas relevantes;
- `check_history`: uma linha para cada verificação concluída;
- `monitoring_notification_preferences`: preferência do WhatsApp e nomes exibidos no alerta;
- `notification_deliveries`: tentativas, resultado, canal e situação dos envios;
- `push_subscriptions`: endpoint e chaves públicas de cada navegador/dispositivo;
- `monitoring_push_subscriptions`: vínculo entre dispositivo e monitoramento, sem broadcast;
- `schema_migrations`: versões de schema já aplicadas.

O status do monitor é numérico no banco:

- `0 = pausado`;
- `1 = ativo`.

O status da entrega também é numérico:

- `0 = pendente`;
- `1 = enviado`;
- `2 = falhou`.

O SQL fica centralizado em `database.py` e nos arquivos de `migrations/`. Os valores recebidos
do usuário são enviados ao SQLite por parâmetros, sem concatenação na consulta.

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

Para enviar uma mensagem de teste sem consultar disponibilidade:

```bash
efvm-monitor test-whatsapp
```

Ou diretamente pelo módulo:

```bash
python -m efvm_monitor.cli test-whatsapp
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

No modo contínuo, o alerta é emitido quando o estado muda para `TEM_VAGA`. Enquanto continuar
`TEM_VAGA`, nenhuma nova mensagem é enviada. Depois de `TEM_VAGA → SEM_VAGA → TEM_VAGA`, um
novo alerta pode ser enviado.

## Web Push — canal principal gratuito

A Fase 4.2 usa os padrões Service Worker, Push API, Notifications API e VAPID por meio do
`pywebpush`. Não usa Firebase e não envia a chave privada ao navegador.

Gere um par de chaves uma única vez:

```bash
efvm-monitor generate-vapid-keys
```

Copie o resultado para seu `.env` e informe um contato do responsável pelo servidor:

```dotenv
WEB_PUSH_ENABLED=true
VAPID_PUBLIC_KEY=chave_publica_gerada
VAPID_PRIVATE_KEY=chave_privada_gerada
VAPID_SUBJECT=mailto:seu-email@example.com
WEB_PUSH_MAX_ATTEMPTS=3
WEB_PUSH_TIMEOUT_SECONDS=15
```

Nunca publique `VAPID_PRIVATE_KEY`. O endpoint `/api/push/config` entrega somente a chave
pública. A inscrição só ocorre depois do clique em **Ativar alertas**. Cada dispositivo recebe
um ID local não sensível e sua subscription fica no SQLite; ao iniciar um monitoramento, apenas
os dispositivos vinculados a ele recebem o alerta.

No Android/Chrome, use **Instalar aplicativo** quando o navegador oferecer essa ação. No
iPhone/iPad, abra no Safari, use **Compartilhar → Adicionar à Tela de Início**, abra o aplicativo
instalado e então toque em **Ativar alertas**. Web Push no iOS depende desse modo instalado.

Os estados da tela distinguem canal pronto, ativo, bloqueado, não suportado, sem HTTPS e
instalação necessária. O botão **Enviar teste** valida o dispositivo sem consultar passagens.
Endpoints expirados com HTTP `404` ou `410` são desativados. HTTP `429` e respostas `5xx` usam
tentativas limitadas; nenhuma falha de push encerra o monitor.

## SMS via Twilio — opcional

O SMS é uma alternativa opcional e pode gerar custos. A integração usa a API REST oficial da Twilio por meio do
`httpx` já instalado, sem dependência adicional. Crie uma conta no
[Twilio Console](https://console.twilio.com/), obtenha um número habilitado para SMS e preencha:

```dotenv
SMS_ENABLED=false
SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=seu_token
TWILIO_FROM_NUMBER=+15551234567
SMS_RECIPIENT_PHONE=+5531999999999
SMS_DRY_RUN=false
```

Os telefones devem usar E.164: sinal `+`, código do país, DDD e número, sem prefixos locais.
Contas de teste podem exigir que o destinatário seja previamente verificado. SMS pode gerar
custos e mensagens longas podem ser cobradas em múltiplos segmentos.

Teste sem depender de passagem disponível:

```bash
efvm-monitor test-sms
```

Para habilitar o canal, altere `SMS_ENABLED=true`. Antes do primeiro envio real, use
`SMS_DRY_RUN=true`. Nesse modo a mensagem, validação,
deduplicação e persistência são exercitadas, mas a Twilio não é chamada. Para desativar o canal,
use `SMS_ENABLED=false`.

O destinatário é exibido e salvo apenas de forma mascarada. O Auth Token nunca é persistido nem
registrado em log. Falhas de autenticação, saldo, HTTP, timeout ou indisponibilidade ficam
registradas como falha de entrega e não encerram o monitor.

## WhatsApp Cloud API — opcional

O canal complementar usa exclusivamente a
[WhatsApp Cloud API oficial da Meta](https://developers.facebook.com/docs/whatsapp/cloud-api/).
Não usa WhatsApp Web, navegador aberto, leitura de QR Code ou automação visual.

Crie uma aplicação na Meta, configure um número remetente e preencha no `.env`:

```dotenv
WHATSAPP_ENABLED=true
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_RECIPIENT_PHONE=5531999999999
WHATSAPP_API_VERSION=v26.0
```

O destinatário deve usar o formato internacional, somente com números. A versão da Graph API
fica configurável porque a Meta atualiza suas versões periodicamente.

Para mensagens iniciadas pela aplicação fora da janela de atendimento, configure um template
aprovado no WhatsApp Manager:

```dotenv
WHATSAPP_TEMPLATE_NAME=alerta_disponibilidade_efvm
WHATSAPP_TEMPLATE_LANGUAGE=pt_BR
```

O template deve possuir sete parâmetros de corpo, nesta ordem: origem, destino, data, classe,
passageiros, horário da detecção e link oficial. Sem `WHATSAPP_TEMPLATE_NAME`, a integração
envia texto livre, adequado apenas quando as regras vigentes da conta e da janela de conversa
permitirem.

Falhas temporárias de conexão, HTTP `429` e respostas `5xx` usam tentativas limitadas com
espera crescente. Uma conexão interrompida depois que o envio pode ter começado não é repetida
automaticamente, reduzindo o risco de mensagem duplicada. Toda falha é registrada e nunca
interrompe o monitoramento.

O `.env` já está protegido pelo `.gitignore`. Nunca copie token, número privado ou credencial
para `.env.example`, código, commit, captura de tela ou relatório de teste.

## Webhook complementar

Defina `ALERT_WEBHOOK_URL` para receber um `POST` JSON quando houver vaga:

```dotenv
ALERT_WEBHOOK_URL=https://seu-servico.example/alerta
```

O corpo contém apenas status, trajeto, data, classe, quantidade de opções e o link do portal.
Não contém CPF, credenciais, dados de pagamento ou token de compra. O webhook foi preservado
por compatibilidade, mas não é o canal principal.

## Testes e qualidade

```bash
pytest
ruff check .
```

Os testes locais verificam a classificação dos três estados, migrations idempotentes,
persistência, histórico, retomada após reinicialização, deduplicação de alertas, retry,
continuidade após falha de notificação, subscriptions Web Push, invalidação `404`/`410`,
proteção da chave VAPID privada, service worker, serviço em segundo plano, rotas e validações
do formulário. Eles não fazem chamadas ao portal nem enviam mensagens reais.

## Rotas locais

| Método | Rota | Função |
| --- | --- | --- |
| `GET` | `/` | Exibe a interface |
| `GET` | `/api/catalogo` | Lista estações, classes e janela de venda |
| `POST` | `/api/monitoramento` | Inicia um monitoramento |
| `GET` | `/api/monitoramento` | Consulta o estado atual |
| `GET` | `/api/monitoramento/historico` | Consulta o histórico do monitor atual |
| `DELETE` | `/api/monitoramento` | Solicita a parada |
| `GET` | `/api/push/config` | Entrega apenas a configuração pública do Web Push |
| `GET` | `/api/push/status` | Consulta o vínculo do dispositivo atual |
| `POST` | `/api/push/subscribe` | Salva e vincula uma subscription |
| `POST` | `/api/push/unsubscribe` | Desativa uma subscription sem apagar histórico |
| `POST` | `/api/push/test` | Envia uma notificação de teste ao dispositivo |
| `GET` | `/manifest.webmanifest` | Entrega o manifesto da PWA |
| `GET` | `/service-worker.js` | Entrega o service worker no escopo raiz |

## Estrutura

```text
src/efvm_monitor/
├── checker.py   # catálogos públicos, consulta e classificação
├── cli.py       # execução única/contínua e logs
├── config.py    # leitura e validação do .env
├── database.py  # camada exclusiva de acesso ao SQLite
├── migrations/  # evolução idempotente do schema local
├── monitor.py   # ciclo em segundo plano, persistência e retomada
├── network.py   # HTTPS verificado com certificados do sistema
├── notifier.py  # SMS/Twilio, WhatsApp, retry e canais complementares
├── web_push.py  # VAPID, payload, retry e invalidação de subscriptions
├── web.py       # servidor e rotas locais
├── static/      # manifesto, service worker, ícones, estilos e interação
└── templates/   # página HTML
tests/
├── test_checker.py
├── test_cli.py
├── test_database.py
├── test_monitor.py
├── test_notifier.py
├── test_sms.py
├── test_web.py
├── test_web_push.py
└── test_web_push_api.py
```

## Limitações conhecidas

- o portal pode alterar URLs, campos ou respostas;
- disponibilidade pode desaparecer entre o alerta e a compra manual;
- `TEM_VAGA` significa que o portal retornou ao menos uma opção naquele instante, não uma
  reserva garantida;
- uma resposta inesperada é classificada como `ERRO`, nunca como ausência de vaga;
- o monitor depende da janela de venda informada dinamicamente pelo portal.
- somente um monitoramento pode ficar ativo por processo;
- uma configuração salva cuja data já expirou é recuperada como `ERRO` e não é iniciada;
- múltiplos monitoramentos simultâneos continuam reservados para a Fase 5.
- o envio real depende de uma conta Meta válida, destinatário permitido e, quando aplicável,
  template aprovado;
- o Web Push depende do suporte do navegador, de permissão explícita e de HTTPS fora do
  ambiente local de desktop;
- validação em aparelhos físicos não pode ser substituída pelos testes automatizados e deve
  ser realizada quando houver uma URL HTTPS acessível ao iPhone ou Android;

## Uso responsável

Este projeto é independente e não possui afiliação com a Vale. Consulte com baixa frequência,
não execute várias instâncias para a mesma pesquisa e use sempre o site ou aplicativo oficial
para qualquer compra.
