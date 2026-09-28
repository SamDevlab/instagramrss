# Instagram Stories RSS

Microserviço FastAPI para cadastrar contas a partir do link de um Story ativo, consultar Stories diretamente com uma sessão persistente do Instagram, armazenar as mídias localmente e publicar JSON/Media RSS sem depender de URLs CDN temporárias.

## Arquitetura

```text
permalink de Story
      ↓
username
      ↓
Instagram user_id
      ↓
DirectInstagramProvider (sessão persistente)
      ↓
Stories ativas + IDs reais
      ↓
download local + SHA-256
      ↓
snapshot persistente
      ↓
JSON / Media RSS / Viewer
```

O SaveClip não faz parte do provider de produção. A ferramenta `saveclip_protocol_probe.py`, quando usada, serve somente para observação sanitizada durante a comparação de comportamento.

## Cadastro

A entrada principal é o link completo de um Story:

```text
https://www.instagram.com/stories/usuario/123456789/
```

O permalink serve para onboarding. Depois que o serviço resolve o `instagram_user_id`, os refreshes usam o ID persistido.

Criar uma fonte:

```bash
curl -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"story_url":"https://www.instagram.com/stories/nasa/123456789/","refresh":true}'
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
```

`provider_story_id` é a identidade lógica. SHA-256 é a identidade física do arquivo.

Em falhas, o último snapshot `COMPLETE` é preservado. Um retorno vazio após um snapshot não vazio vira `SUSPICIOUS_EMPTY_SNAPSHOT`. Quedas maiores que 50% exigem confirmação em um segundo ciclo equivalente.

## Sessão do Instagram

Copie `.env.example` para `.env`.

```env
INSTAGRAM_USERNAME=sua_conta_de_consulta
INSTAGRAM_PASSWORD=
INSTAGRAM_SESSION_FILE=./session/instagram.session
INSTAGRAM_PROVIDER=direct

DATA_DIR=./data
PUBLIC_BASE_URL=http://localhost:8000
STORY_REFRESH_MINUTES=15
STORY_STALE_MINUTES=35
STORY_SCHEDULER_ENABLED=true
```

Para criar a sessão inicial, use temporariamente a senha e execute:

```powershell
python scripts/create_instagram_session.py
```

Ou importe uma sessão de navegador já autenticado:

```powershell
python scripts/create_instagram_session.py --browser chrome
```

Nunca versione senha, sessão, cookies ou dados do diretório `data/`.

## Provider direto

O provider padrão usa o Instaloader já presente no projeto:

1. carrega a sessão persistente;
2. resolve username para user ID numérico;
3. consulta Stories atuais desse user ID;
4. normaliza ID real, horário, tipo e URL temporária;
5. a URL temporária é usada somente durante o download;
6. RSS/JSON de produção usam URLs locais.

Erros de rate limit, login, challenge, checkpoint e sessão inválida são convertidos em estados explícitos e não substituem o último snapshot válido.

## Scheduler

Com:

```env
STORY_SCHEDULER_ENABLED=true
STORY_REFRESH_MINUTES=15
```

o processo percorre as fontes cadastradas. Há lock por fonte para impedir refresh simultâneo da mesma conta.

`STORY_STALE_MINUTES` define quando uma fonte sem novo `COMPLETE` passa a `STALE`, sem apagar o snapshot.

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
python -m compileall .
```

Os testes automatizados usam providers/downloaders falsos e não dependem de Instagram, SaveClip, Chrome ou internet.

## Limitações operacionais

A interface não oficial do Instagram pode mudar e o Instagram pode aplicar rate limit ou exigir challenge. O serviço não tenta contornar esses mecanismos. Quando isso acontece, o estado anterior permanece publicado e o erro é registrado para diagnóstico.
