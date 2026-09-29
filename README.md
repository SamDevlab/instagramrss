# Instagram Stories RSS

Microserviço FastAPI para cadastrar contas a partir do link de um Story ativo, consultar Stories diretamente com uma sessão persistente do Instagram, armazenar as mídias localmente e publicar JSON/Media RSS sem depender de URLs CDN temporárias.

## Arquitetura

```text
permalink de Story
      ↓
source
      ↓
owner_id + auth_policy
      ↓
AuthConnectionService
      ↓
AuthResolver
      ↓
ProviderFactory
      ↓
MobileInstagramProvider
      ↓
Stories ativas + IDs reais
      ↓
download local + SHA-256
      ↓
snapshot persistente
      ↓
JSON / Media RSS / Viewer
```

O pipeline de coleta, download, SHA-256, snapshot, catálogo, state, RSS e scheduler continua sendo único. O SaveClip não faz parte do provider de produção.

## Cadastro

A entrada principal é o link completo de um Story:

```text
https://www.instagram.com/stories/usuario/123456789/
```

O permalink serve para onboarding. Depois que o serviço resolve o `instagram_user_id`, os refreshes usam o ID persistido.

Criar uma source a partir de um link de Story:

```bash
curl -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"story_url":"https://www.instagram.com/stories/nasa/123456789/","refresh":true}'
```

Uma source nova usa `PREFER_OWNER_WITH_SHARED_FALLBACK`. O `owner_id` identifica o tenant/cliente no fluxo administrativo atual; ele não é autenticação da aplicação:

```json
{
  "story_url": "https://www.instagram.com/stories/nasa/123456789/",
  "owner_id": "client_A",
  "refresh": true
}
```

Para fixar explicitamente uma conexão, use `PINNED`:

```json
{
  "story_url": "https://www.instagram.com/stories/nasa/123456789/",
  "owner_id": "client_A",
  "auth_policy": "PINNED",
  "auth_connection_id": "auth_xxx",
  "refresh": true
}
```

## Endpoints

- `GET /`
- `GET /viewer`
- `GET /health`
- `POST /sources`
- `GET /sources`
- `GET /sources/{source_id}`
- `POST /sources/{source_id}/refresh`
- `GET /sources/{source_id}/stories`
- `GET /sources/{source_id}/rss.xml`
- `GET /media/{source_id}/{filename}`
- `GET /auth-connections`
- `GET /auth-connections/{id}`
- `DELETE /auth-connections/{id}`

Aliases antigos continuam disponíveis para fontes já cadastradas:

- `GET /stories/{username}`
- `GET /stories?profile=@usuario`
- `GET /rss/stories/{username}`
- `GET /rss/stories?profile=@usuario`

Os aliases não fazem nova coleta. Eles leem o snapshot persistido.

## Persistência

Por padrão:

```text
data/
  sources/
    {source_id}/
      source.json
      state.json
      current.json
      catalog.json
      media/
        {sha256}.jpg
        {sha256}.mp4
  auth/
    connections.json
    credentials.json  # ciphertext AES-GCM; nunca contém plaintext
```

`provider_story_id` é a identidade lógica. SHA-256 é a identidade física do arquivo.

`source.json` guarda os metadados da source, a política de seleção e, quando aplicável, a referência `auth_connection_id` de uma conexão PINNED. Session IDs, cookies e tokens ficam no credential store separado e criptografado.

Os campos de seleção ficam separados: `auth_policy` define a política, `last_auth_connection_id` registra a conexão efetivamente escolhida no último ciclo e `last_auth_selection_reason` registra `OWNER_AUTH`, `SHARED_FALLBACK`, `PINNED` ou `LEGACY_FALLBACK`.

Em falhas, o último snapshot `COMPLETE` é preservado. Um retorno vazio após um snapshot não vazio vira `SUSPICIOUS_EMPTY_SNAPSHOT`. Quedas maiores que 50% exigem confirmação em um segundo ciclo equivalente.

## Conexões Instagram

Copie `.env.example` para `.env`. Para persistir uma conexão `INSTAGRAM_SESSION`, configure uma chave AES em base64 ou hexadecimal:

```env
AUTH_CREDENTIAL_MASTER_KEY=gere_uma_chave_de_32_bytes_em_base64
```

O serviço não aceita sessionid ou cookies pela API. O bootstrap inicial é uma operação explícita na máquina autorizada:

```powershell
python scripts/create_instagram_session.py --browser chrome
python scripts/authorize_instagram_connection.py \
  --username sua_conta_autorizada \
  --session-file .\session\instagram.session \
  --owner-id client_A \
  --scope private
```

Uma conexão operacional pode ser compartilhada somente por escolha explícita:

```powershell
python scripts/authorize_instagram_connection.py `
  --username conta_operacional `
  --session-file .\session\shared.session `
  --owner-id operator `
  --scope shared
```

O segundo comando grava a sessão no credential store criptografado e imprime somente os metadados públicos da conexão. Gere a chave, por exemplo, com:

```powershell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Sources novas usam `PREFER_OWNER_WITH_SHARED_FALLBACK`; `auth_connection_id` só é necessário para `PINNED`. Sources antigas sem política explícita recebem defaults conservadores durante a leitura e continuam podendo usar temporariamente o fallback legado:

```env
INSTAGRAM_USERNAME=conta_legada_do_servidor
INSTAGRAM_SESSION_FILE=./session/instagram.session
INSTAGRAM_PROVIDER=mobile
INSTAGRAM_LEGACY_SESSION_ENABLED=true
INSTAGRAM_LEGACY_OWNER_ID=legacy_operator

DATA_DIR=./data
PUBLIC_BASE_URL=http://localhost:8000
STORY_REFRESH_MINUTES=15
STORY_STALE_MINUTES=35
STORY_SCHEDULER_ENABLED=true
```

Para criar a sessão legada inicial, use temporariamente a senha e execute:

```powershell
python scripts/create_instagram_session.py
```

Ou importe uma sessão de navegador já autenticado:

```powershell
python scripts/create_instagram_session.py --browser chrome
```

Nunca versione senha, sessão, cookies ou dados do diretório `data/`.

### Estados de conexão

- `ACTIVE`: pode ser usada pelo provider selecionado para a source.
- `RECONNECT_REQUIRED`: o Instagram rejeitou a sessão ou exigiu login/challenge/checkpoint. A coleta é interrompida até nova autorização.
- `REVOKED`: a conexão foi desconectada por `DELETE /auth-connections/{id}`.
- `ERROR`: ocorreu uma falha persistente no credential store.

Desconectar uma conexão revoga e remove a credencial criptografada, mas preserva sources, `instagram_user_id`, catálogo, mídia e RSS histórico.

Para reconectar, autorize uma nova conexão com o script. Uma source `PINNED` recebe o novo `auth_connection_id` no `POST /sources`; uma source preferencial passa a encontrá-la automaticamente quando o `owner_id` e a capability forem compatíveis. O user ID já persistido será reaproveitado e o pipeline não repetirá `story_info`.

### Políticas de seleção

`PREFER_OWNER_WITH_SHARED_FALLBACK` resolve a conexão nesta ordem:

1. conexões `ACTIVE` do próprio `owner_id` com a capability necessária;
2. conexões `SHARED` `ACTIVE` compatíveis;
3. sessão legada, se habilitada.

Dentro de cada grupo, a seleção é determinística por `last_validated_at`, `updated_at` e `id`. Uma conexão `PRIVATE` de outro owner nunca entra no pool automático.

`PINNED` respeita exatamente `auth_connection_id`. Se ela ficar inválida, a source retorna `RECONNECT_REQUIRED` ou o estado da conexão, preserva o snapshot e não muda silenciosamente para uma shared.

O sistema ainda não possui login de usuários do próprio `instagramrss`; `owner_id` é uma identificação de tenant usada no bootstrap/admin. Um header arbitrário não é tratado como autenticação.

## Provider mobile

O provider padrão usa o Instaloader já presente no projeto:

1. carrega a sessão persistente;
2. resolve username para user ID numérico;
3. consulta Stories atuais desse user ID;
4. normaliza ID real, horário, tipo e URL temporária;
5. a URL temporária é usada somente durante o download;
6. RSS/JSON de produção usam URLs locais.

O `MobileInstagramProvider` recebe uma credencial já resolvida pelo `ProviderFactory`; ele não acessa `auth_connections.json` nem conhece o credential store. Cada refresh cria um contexto de cliente separado para a conexão da source.

Erros de rate limit, login, challenge, checkpoint e sessão inválida são convertidos em estados explícitos. Falhas de autenticação marcam a conexão como `RECONNECT_REQUIRED` e não substituem o último snapshot válido.

## Scheduler

Com:

```env
STORY_SCHEDULER_ENABLED=true
STORY_REFRESH_MINUTES=15
```

o processo percorre as fontes cadastradas. Há lock por fonte para impedir refresh simultâneo da mesma conta.

`STORY_STALE_MINUTES` define quando uma fonte sem novo `COMPLETE` passa a `STALE`, sem apagar o snapshot.

Durante cada ciclo, o scheduler resolve a conexão individualmente para cada source. A falha de `auth_A` não impede o refresh de uma source vinculada a `auth_B`.

Em sources preferenciais, a conexão usada é reavaliada a cada ciclo. Se a conexão própria ficar `RECONNECT_REQUIRED`, o próximo ciclo pode usar uma `SHARED`; uma falha de acesso ao target mantém a conexão `ACTIVE`. Falhas de autenticação marcam somente a conexão que respondeu.

## Meta OAuth

`META_OAUTH` está reservado no contrato de autenticação e é rejeitado explicitamente pelo factory enquanto não houver adapter Graph API neste projeto. Ele não é tratado como substituto universal do provider mobile: suas capabilities devem limitar a coleta às contas profissionais autorizadas.

## Rodando localmente

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
copy .env.example .env
python scripts/create_instagram_session.py
uvicorn app:app --reload
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
python scripts/create_instagram_session.py
uvicorn app:app --reload
```

## Docker

```bash
docker build -t instagram-stories-rss .
docker run --rm -p 8000:8000 --env-file .env \
  -v "$(pwd)/session:/app/session" \
  -v "$(pwd)/data:/app/data" \
  instagram-stories-rss
```

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q .
pip check
```

Os testes automatizados usam providers/downloaders falsos e não dependem de Instagram, SaveClip, Chrome ou internet.

## Limitações operacionais

A interface não oficial do Instagram pode mudar e o Instagram pode aplicar rate limit ou exigir challenge. O serviço não tenta contornar esses mecanismos. Quando isso acontece, o estado anterior permanece publicado e o erro é registrado para diagnóstico.
