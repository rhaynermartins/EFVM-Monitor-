# Segurança

## Versões suportadas

O EFVM Monitor ainda não publica versões com suporte de longo prazo. Correções de segurança são
aplicadas na branch `main`, que representa a versão mantida do projeto.

## Como relatar uma vulnerabilidade

Não publique credenciais, dados pessoais ou detalhes exploráveis em uma issue pública. Use o
recurso privado **Report a vulnerability** do repositório no GitHub, quando disponível. Caso ele
não esteja habilitado, abra uma issue sem detalhes sensíveis solicitando um canal privado de
contato com o mantenedor.

Inclua, quando possível:

- componente e versão ou commit afetado;
- impacto observado;
- passos mínimos para reprodução, sem dados reais de usuários;
- sugestões de mitigação;
- uma forma segura de contato.

O mantenedor deve confirmar o recebimento, avaliar impacto e combinar a divulgação responsável
antes que detalhes técnicos sejam publicados. Não há prazo de resposta ou programa de recompensa
garantido.

## Modelo de segurança

- senhas recebem hash `scrypt` com salt aleatório e não são armazenadas em texto puro;
- sessões usam token opaco em cookie `HttpOnly`, `SameSite=Lax` e `Secure` em produção; somente o
  hash do token é persistido;
- operações de escrita exigem token CSRF vinculado à sessão;
- monitoramentos, histórico e dispositivos Web Push são filtrados pelo usuário no backend;
- limites defensivos protegem login, criação de monitores e testes de notificação;
- respostas web usam CSP e outros cabeçalhos de proteção do navegador;
- secrets são fornecidos pelo ambiente e não devem entrar no Git, SQLite, logs ou frontend;
- a aplicação não automatiza login na Vale, CPF, reserva, assento, pagamento ou compra.

## Configuração segura

Em produção:

1. mantenha `EFVM_COOKIE_SECURE=true` e exponha a aplicação somente por HTTPS;
2. preserve `.env`, banco SQLite, backups, logs e chave VAPID fora do checkout Git;
3. restrinja permissões dos arquivos e o acesso SSH à VM;
4. mantenha uma única instância do Uvicorn/MonitoringManager para este desenho com SQLite;
5. atualize dependências após testes e revise alertas do GitHub/Dependabot;
6. guarde cópias do SQLite em local privado e criptografado, conforme o guia de operação.

Consulte [docs/oracle-cloud-deployment.md](docs/oracle-cloud-deployment.md) para implantação,
backup, recuperação e validação operacional.

## Escopo conhecido

O projeto não oferece recuperação de senha, confirmação de e-mail, OAuth, painel administrativo
ou exclusão automática de conta. A remoção de um monitor é lógica para preservar histórico e
auditoria. Essas limitações devem ser consideradas antes de ampliar o serviço para um público
maior.
