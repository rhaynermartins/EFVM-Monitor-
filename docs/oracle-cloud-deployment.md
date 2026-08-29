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

Uma indisponibilidade do banco produz HTTP `503` e estado `degraded`.

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
