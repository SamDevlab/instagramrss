# Instagram Stories RSS

Microserviço focado exclusivamente em transformar **Stories ativos do Instagram** em JSON e Media RSS.

> Este projeto não coleta posts, feed, hashtags ou Reels. Ele usa uma sessão autenticada do Instagram via Instaloader e foi pensado inicialmente para testes/protótipos. O Instagram pode alterar endpoints, exigir desafios de login ou aplicar rate limits.

## Fluxo

```text
URL ou @username
      ↓
Instaloader + sessão autenticada
      ↓
Stories ativos
      ↓
JSON / Media RSS
      ↓
NeoNews ou outro leitor RSS
```

## Endpoints

- `GET /health`
- `GET /stories/{username}`
- `GET /stories?profile=https://instagram.com/usuario`
- `GET /rss/stories/{username}`
- `GET /rss/stories?profile=https://instagram.com/usuario`

Exemplo:

```bash
curl http://localhost:8000/stories/nasa
curl http://localhost:8000/rss/stories/nasa
```

## Configuração

Copie `.env.example` para `.env`.

```env
INSTAGRAM_USERNAME=sua_conta_de_consulta
INSTAGRAM_PASSWORD=
INSTAGRAM_SESSION_FILE=./session/instagram.session
```

A opção recomendada é reutilizar uma sessão salva. Se o arquivo de sessão não existir e `INSTAGRAM_PASSWORD` estiver configurado, o serviço tenta fazer login uma vez e salva a sessão no caminho configurado.

Nunca versione a sessão nem a senha.

## Rodando localmente

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app:app --reload
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

A API ficará em `http://localhost:8000`.

## Docker

```bash
docker build -t instagram-stories-rss .
docker run --rm -p 8000:8000 --env-file .env -v "$(pwd)/session:/app/session" instagram-stories-rss
```

## Exemplo JSON

```json
{
  "username": "nasa",
  "count": 2,
  "stories": [
    {
      "id": "123456789",
      "type": "image",
      "media_url": "https://...",
      "created_at": "2026-09-16T20:00:00+00:00",
      "expires_at": "2026-09-17T20:00:00+00:00"
    }
  ]
}
```

## Media RSS

Cada Story vira um `<item>` com `guid`, `pubDate` e `media:content`. Imagens usam `image/jpeg`; vídeos usam `video/mp4`.

Nesta primeira versão o RSS aponta para a URL de mídia entregue pelo Instagram. Essas URLs podem expirar. Uma evolução natural é adicionar cache/proxy de mídia próprio antes de usar isso em produção.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```
