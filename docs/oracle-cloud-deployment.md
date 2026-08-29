# Implantação Oracle Cloud — Fase 7

Este documento descreve a implantação leve do EFVM Monitor na instância Oracle Cloud
`efvminstance`. A aplicação continua apenas consultando disponibilidade e emitindo alertas;
ela não compra, reserva ou seleciona passagens.

## Arquitetura

```text
Internet
  → efvm-monitor-rhayner.duckdns.org (TCP 80/443)
  → Caddy (HTTPS e renovação automática)
  → 127.0.0.1:8000
  → Uvicorn, FastAPI e um único MonitoringManager
  → /var/lib/efvm-monitor/efvm-monitor.db
```

A porta `8000` permanece restrita ao loopback. O processo da aplicação usa um único worker
Uvicorn, requisito necessário para não duplicar o `MonitoringManager` sobre o mesmo SQLite.

## Caminhos de produção

| Conteúdo | Caminho |
| --- | --- |
| Checkout Git | `/opt/efvm-monitor/app` |
| Ambiente virtual | `/opt/efvm-monitor/venv` |
| Configuração secreta | `/etc/efvm-monitor/efvm-monitor.env` |
| SQLite | `/var/lib/efvm-monitor/efvm-monitor.db` |
| Backups SQLite | `/var/lib/efvm-monitor/backups` |
| Caddyfile | `/etc/caddy/Caddyfile` |

O arquivo de ambiente pertence a `root:efvm`, usa modo `0640` e nunca deve ser copiado para o
Git. O banco, backups, chaves VAPID, tokens e números privados também não devem ser versionados.

## Serviços

```bash
sudo systemctl status efvm-monitor.service
sudo systemctl status caddy.service
sudo systemctl status efvm-monitor-backup.timer
```

O serviço `efvm-monitor` inicia no boot e usa `Restart=on-failure`. O Caddy gerencia o
certificado público e redireciona HTTP para HTTPS. O timer executa diariamente um backup
consistente pela API de backup do SQLite e conserva os arquivos locais por 14 dias.

Para consultar somente eventos recentes:

```bash
sudo journalctl -u efvm-monitor.service --since "30 minutes ago" --no-pager
sudo journalctl -u caddy.service --since "30 minutes ago" --no-pager
```

Não registre o conteúdo do arquivo de ambiente nem o ambiente completo do processo nos logs.

## Healthcheck

O endpoint público não exige autenticação e verifica também a abertura do SQLite:

```bash
curl --fail https://efvm-monitor-rhayner.duckdns.org/healthz
```

Resposta saudável:

```json
{"status":"ok","database":"ok"}
```

Para diagnóstico operacional, solicite os detalhes do manager:

```bash
curl --fail 'https://efvm-monitor-rhayner.duckdns.org/healthz?details=true'
```

O bloco `manager` compara monitores ativos no SQLite com workers registrados e vivos. Ele informa
`active_monitors`, `registered_workers`, `running_workers`, `stalled_workers` e
`orphaned_workers`. Banco indisponível, worker ausente ou worker sem monitor ativo produzem HTTP
`503` e estado `degraded`. O endpoint não expõe usuário, trajeto, histórico ou credenciais.

## Logs estruturados

O unit systemd define `EFVM_LOG_FORMAT=json`. Cada linha da aplicação contém timestamp, nível,
logger e mensagem, além de campos operacionais conhecidos quando aplicáveis:

- `event`;
- `monitoring_id`;
- `user_id` interno;
- `result`;
- `duration_ms`;
- `channel`, `attempts` e `status_code`.

O formatador não serializa o ambiente do processo nem campos arbitrários. Senhas, cookies,
tokens, chaves VAPID privadas e credenciais de providers nunca devem ser adicionados às
mensagens. Para filtrar eventos recentes:

```bash
sudo journalctl -u efvm-monitor.service --since "30 minutes ago" -o cat --no-pager
```

## Recuperação e limites defensivos

Uma falha inesperada de cliente, rede ou persistência é registrada como `ERRO`; o worker espera o
intervalo configurado, recria o cliente e tenta novamente. A falha de um monitor continua isolada
dos demais. O healthcheck detalhado permite detectar divergências que não se recuperaram.

Por padrão, cada usuário pode manter até 10 monitoramentos visíveis. Remover logicamente um
monitor libera espaço. O valor pode ser ajustado entre 1 e 100 por
`EFVM_MAX_MONITORS_PER_USER`, sem reduzir o intervalo mínimo de consulta de 60 segundos.
Tentativas de login, criação repetitiva e testes de Web Push também usam janelas temporárias em
memória. Respostas limitadas usam HTTP `429` e `Retry-After`; nenhum bloqueio é permanente.

## Atualização da aplicação

Faça atualização somente depois que `main` estiver validada e publicada no GitHub:

```bash
sudo -u efvm git -C /opt/efvm-monitor/app pull --ff-only origin main
sudo -u efvm /opt/efvm-monitor/venv/bin/pip install -e /opt/efvm-monitor/app
sudo systemctl restart efvm-monitor.service
curl --fail http://127.0.0.1:8000/healthz
```

Não use force push, reset destrutivo ou cópia de um banco local sobre o banco de produção.
As migrations existentes são idempotentes e executadas pela aplicação durante a inicialização.

## Backup e recuperação

Para executar um backup adicional sem interromper a aplicação:

```bash
sudo systemctl start efvm-monitor-backup.service
sudo systemctl status efvm-monitor-backup.service
```

Antes de qualquer recuperação, pare e confirme o arquivo, a integridade e a data desejada. A
substituição do banco é uma operação manual potencialmente destrutiva e não deve ser executada
automaticamente.

O timer local protege contra falhas lógicas e alterações acidentais, mas não contra perda total do
disco da VM. Pelo menos semanalmente, copie o backup íntegro mais recente para um armazenamento
externo privado e criptografado sob controle do operador. Não envie bancos para o GitHub e não
use links públicos. Confirme a cópia recebida com `PRAGMA integrity_check` sem substituir o banco
em produção.

Uma simulação de recuperação deve ser feita somente em arquivo temporário e fora do caminho de
produção. A recuperação real exige checkpoint manual, serviço parado, backup adicional do banco
atual e confirmação explícita do arquivo escolhido. O projeto não executa restauração automática.

## Checklist operacional

Após deploy ou reinicialização:

1. confirme `efvm-monitor.service`, `caddy.service` e o timer de backup como ativos;
2. consulte `/healthz?details=true` e verifique `stalled_workers=0`;
3. confirme exatamente um processo Uvicorn com `--workers 1`;
4. execute `PRAGMA quick_check` no SQLite;
5. confira espaço em disco, RAM e swap;
6. revise logs por `monitor_worker_recovering`, `rate_limit_exceeded` e falhas de notificação;
7. confirme que a quantidade de subscriptions e monitoramentos não mudou inesperadamente.

## Rede e DNS

O firewall da VM e a Security List da subnet permitem entrada apenas nas portas `22`, `80` e
`443`. A porta `8000` não deve ser adicionada à Security List ou ao UFW.

O IPv4 atual é efêmero. O registro DuckDNS aponta atualmente para `137.131.137.188`, mas uma
parada seguida de nova inicialização da instância pode alterar esse endereço. Depois desse tipo
de evento:

1. consulte o novo IPv4 público no Console Oracle;
2. atualize `efvm-monitor-rhayner.duckdns.org` pelo DuckDNS;
3. confirme que o DNS resolve para o IPv4 atual;
4. valide o healthcheck HTTPS.

Uma atualização automática futura precisa usar o token DuckDNS somente em arquivo protegido na
VM. Nunca coloque esse token em script versionado, unit systemd, documentação ou linha de comando
que possa permanecer no histórico.

## Cadeia TLS do portal da Vale

Em 29 de agosto de 2026, `tremdepassageiros.vale.com` apresentava somente o certificado leaf e
omitia o intermediário declarado em seu AIA. `curl`, OpenSSL, o contexto padrão do Python e o
`truststore` falhavam corretamente com `unable to get local issuer certificate`.

A cadeia pública verificada é:

```text
tremdepassageiros.vale.com
→ DigiCert Global G2 TLS RSA SHA256 2020 CA1
→ DigiCert Global Root G2
```

O projeto inclui somente o intermediário público obtido do repositório oficial da DigiCert. Seu
SHA-256 esperado é:

```text
C8:02:5F:9F:C6:5F:DF:C9:5B:3C:A8:CC:78:67:B9:A5:
87:B5:27:79:73:95:79:17:46:3F:C8:13:D0:B6:25:A9
```

Esse intermediário é carregado exclusivamente pelo cliente HTTP da Vale. O trust store global da
VM e os clientes de Web Push, WhatsApp, SMS e webhook não são modificados. O contexto continua
com validação de hostname e `CERT_REQUIRED`; uma divergência do fingerprint interrompe a criação
do cliente em vez de reduzir a segurança.

Para diagnosticar a cadeia apresentada pelo servidor sem desativar TLS:

```bash
openssl s_client \
  -connect tremdepassageiros.vale.com:443 \
  -servername tremdepassageiros.vale.com \
  -showcerts \
  -verify_return_error </dev/null
```

Se a Vale passar a entregar a cadeia completa ou trocar de autoridade intermediária, reavalie o
arquivo somente contra a fonte oficial da CA. Nunca substitua essa configuração por
`verify=False`, `CERT_NONE`, `check_hostname=False` ou uma CA obtida de fonte não oficial.

## PWA e Web Push no iPhone

O teste físico deve ser feito no Safari com a URL HTTPS:

1. crie a conta ou entre na conta correta;
2. use **Compartilhar → Adicionar à Tela de Início**;
3. abra o EFVM Monitor pelo ícone instalado;
4. toque em **Ativar alertas** e aceite a permissão do iOS;
5. use **Enviar teste**;
6. feche a PWA e confirme que a notificação ainda chega;
7. toque na notificação e confirme que ela abre a própria aplicação.

Não use uma conta ou dispositivo de outra pessoa nesse teste. Subscriptions Web Push ficam
vinculadas ao usuário autenticado e não devem receber alertas de outros usuários.
