# mengjing-2api gateway

Wraps the text-to-image capability of the "AI梦境画画师" app as an **OpenAI-compatible**
HTTP API, backed by Alibaba DashScope `qwen_image3`. Ships with an account pool
(auto-register, auto-refill, session keep-alive), API-key management, and a web admin console.

- Pure Python standard library, **zero third-party dependencies** — tiny Docker image, fast startup, low footprint
- OpenAI compatible: `POST /v1/images/generations`
- Admin console: API keys, account pool, and all parameters configurable online
- Default password `admin123` — change it right after the first sign-in

---

## Quick start

### Option 1: Docker Compose

```bash
docker compose up -d --build
```

### Option 2: Docker

```bash
docker build -t mengjing-2api .
docker run -d --name mengjing-2api -p 8788:8788 -v $PWD/data:/data mengjing-2api
```

### Option 3: Run directly (requires Python 3.10+)

```bash
PORT=8788 DATA_DIR=./data python3 main.py
```

Open <http://localhost:8788> → sign in with the default password `admin123`.

---

## Deploy to Coolify

1. Create a Resource → pick your Git repo (or point directly at this directory).
2. Build Pack: choose **Dockerfile** (the project ships one).
3. Expose port `8788` (or set the `PORT` env var).
4. Volumes: mount the persistent data directory at container path `/data` (holds `config.json`, `pool.json`).
5. After deploy, the site root `/` is the admin console.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8788` | Listen port |
| `HOST` | `0.0.0.0` | Listen address |
| `DATA_DIR` | `./data` | Config + account-pool persistence directory |

---

## Using the API

First create a key under "API Keys" in the console.

```bash
curl -X POST "http://localhost:8788/v1/images/generations" \
  -H "Authorization: Bearer sk-mj-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a white cat on a windowsill, sunlight streaming in","aspect_ratio":"16:9","style":"Anime"}'
```

Response:

```json
{
  "created": 1730000000,
  "data": [{ "url": "https://....aliyuncs.com/....png", "b64_json": null }],
  "task_id": "659826c5-...",
  "elapsed_s": 58.0,
  "upstream": "qwen_image3 (dashscope)",
  "model": "qwen-image3",
  "size": "1792x1024",
  "aspect_ratio": "16:9",
  "n": 1
}
```

### Parameters

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | yes | Prompt text (content-filtered before submission) |
| `model` | no | Model name, default `qwen-image3` |
| `style` | no | Style, default `Anime`. See `GET /v1/styles` |
| `aspect_ratio` | no | `1:1` / `9:16` / `16:9` — takes precedence over `size` |
| `size` | no | `1024x1024` (default) / `1024x1792` (portrait) / `1792x1024` (landscape) / `512x512` |
| `n` | no | Number of images, 1–4 (sequential, one credit each) |
| `response_format` | no | `url` (default) or `b64_json` |

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/images/generations` | API Key | Text to image |
| GET | `/v1/models` | none | Model list (public) |
| GET | `/v1/styles` | none | Style list (public) |
| GET | `/v1/pool` | API Key | Account pool status |
| GET | `/health` | none | Health check |
| GET | `/` | session | Admin console |
| GET | `/docs` | optional | API docs |

---

## How it works

```
Client ──/v1/images/generations──► Gateway ──► acquire account ──► upstream app API ──► Alibaba DashScope qwen_image3
                                    │
                                    └── content filter → submit task → poll result → return image URL
```

Upstream protocol (all POST multipart, session based on the `JSESSIONID` cookie):

1. `/yh!tempUser.action` register a temporary account → returns `id` / `mima`
2. `/yh!dlUser.action` login → issues `JSESSIONID`
3. `/ctext!isweigui.action` content-filter check
4. `/qwenpic!zhizuo.action` submit generation task → returns `taskid`
5. `/qwenpic!jieguo.action` poll result → returns the image OSS URL

The account pool tracks the free quota per account (5 images by default); once exhausted it
auto-registers new accounts, and it keeps idle sessions alive.

---

## Project layout

```
.
├── main.py               # entrypoint
├── app/
│   ├── __init__.py       # version
│   ├── config.py         # config load/persist
│   ├── security.py       # password hashing, sessions, API keys
│   ├── upstream.py       # upstream protocol client
│   ├── pool.py           # account pool
│   ├── server.py         # HTTP server, routing, admin API
│   └── web/              # console + docs pages
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

The data directory (`DATA_DIR`) contains:

- `config.json`: config, admin password hash, API keys
- `pool.json`: account-pool persistence

---

## FAQ

- **429 pool busy**: not enough available accounts right now — wait a moment or click "Refill" in the console.
- **502 upstream error**: upstream service flakiness — just retry.
- **400 prompt hit the content filter**: rephrase the prompt.
- **Image won't open**: upstream image links are valid for ~24 hours — save them promptly.

---

## Disclaimer

This project is for study and API research only. Please comply with the relevant platform's
terms of service and applicable laws, and do not use it for any commercial or illegal purpose.
