# Política de Privacidade do EFVM Monitor

Última atualização: 29 de agosto de 2026.

Esta política descreve de forma direta os dados usados pelo EFVM Monitor. O projeto é
independente e não é afiliado, patrocinado ou operado pela Vale.

## Dados armazenados

Quando uma pessoa cria e usa uma conta, o serviço pode armazenar:

- nome e endereço de e-mail;
- hash da senha, nunca a senha em texto puro;
- hashes de sessão, token CSRF, datas de criação, uso e expiração da sessão;
- origem, destino, data, classe, número de passageiros e intervalo dos monitoramentos;
- estado dos monitores e histórico das consultas (`TEM_VAGA`, `SEM_VAGA` ou `ERRO`), incluindo
  horários e mensagens operacionais;
- endpoint, chaves públicas de entrega, identificador local e agente do navegador necessários ao
  Web Push;
- tentativas de notificação, canal, resultado, horários e mensagens de falha;
- logs operacionais sem senha, cookie de sessão, chave VAPID privada ou tokens de provedores.

O EFVM Monitor não solicita nem armazena CPF, dados de pagamento, credenciais da Vale ou seleção
de assento.

## Por que esses dados são usados

Os dados são usados somente para:

- autenticar a conta e separar os recursos de cada usuário;
- consultar a disponibilidade da viagem configurada;
- retomar monitoramentos após reinicializações;
- mostrar estado e histórico;
- entregar e diagnosticar notificações solicitadas;
- aplicar limites defensivos e manter o serviço funcionando com segurança.

Não há publicidade, venda de dados ou ferramenta de analytics de terceiros no projeto.

## Cookies, dispositivos e notificações

Um cookie estritamente necessário mantém a sessão autenticada. Em produção ele é enviado com
`Secure`, `HttpOnly` e `SameSite=Lax`. O EFVM Monitor não usa cookie publicitário.

O Web Push só é ativado após ação da pessoa no dispositivo. Para entregar a mensagem, o serviço
envia o conteúdo do alerta ao endpoint de push fornecido pelo navegador. Esse endpoint pertence
ao serviço de push do navegador ou do sistema operacional e está sujeito às regras desse
fornecedor. A inscrição pode ser desativada pela própria interface ou nas configurações do
dispositivo.

SMS e WhatsApp são integrações opcionais de instalações privadas e ficam desativados por padrão.
Quando habilitados pelo operador, os respectivos provedores recebem o número e o conteúdo
necessários à entrega conforme suas próprias políticas.

## Compartilhamento e consultas externas

O backend consulta interfaces públicas do portal de passagens para verificar disponibilidade.
Origem, destino, data, classe e quantidade de passageiros podem integrar essa consulta. A Vale e
os provedores de rede podem observar o endereço IP do servidor, como ocorre em uma requisição web
normal.

Fora as entregas de notificação e a consulta de disponibilidade, os dados não são compartilhados
com terceiros pelo EFVM Monitor.

## Armazenamento, retenção e backups

Na implantação atual, contas, monitores, histórico, sessões e inscrições Web Push ficam em um
banco SQLite persistente na VM. Backups operacionais também podem conter esses dados e devem ser
mantidos em armazenamento privado.

Sessões expiram ou podem ser revogadas. Monitores removidos ficam marcados como removidos, com o
histórico preservado. Como ainda não existe exclusão automática de conta, os demais dados são
mantidos enquanto necessários à operação ou até exclusão manual segura pelo mantenedor. Backups
locais usam a retenção documentada no guia de operação.

## Controle e solicitações

A pessoa pode pausar ou remover seus monitores, desativar o Web Push e encerrar a sessão pela
interface. Para solicitar acesso, correção ou exclusão completa dos dados de uma conta na
instância pública, contate o mantenedor pelo repositório do projeto, sem publicar dados pessoais
em uma issue aberta. A execução local do projeto permanece sob controle do próprio operador.

## Segurança e alterações

As principais proteções e o canal para relatos estão em [SECURITY.md](SECURITY.md). Nenhum serviço
conectado à internet elimina todos os riscos; por isso, a implantação para grupos maiores deve
manter atualizações, backups e revisão de acesso.

Esta política pode mudar se o funcionamento real do projeto mudar. Alterações relevantes devem
ser registradas no repositório com nova data de atualização.
