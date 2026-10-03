# System overview

Status: approved foundation for Module 1

```mermaid
flowchart LR
    TG[Telegram users and group chat] --> BOT[Telegram webhook]
    WEB[Telegram WebApp] --> API[FastAPI REST API]
    BOT --> API

    API --> PG[(PostgreSQL)]
    API --> REDIS[(Redis)]
    API --> R2[(S3-compatible storage)]
    API --> QUEUE[Celery queues]

    QUEUE --> WORKER[Celery workers]
    WORKER --> DS[DeepSeek API]
    WORKER --> PG
    WORKER --> R2
    WORKER --> TGAPI[Telegram Bot API]

    API --> OBS[Logs, errors, health and metrics]
    WORKER --> OBS

    CONTRACT[OpenAPI contract] --> API
    CONTRACT --> CLIENT[Generated TypeScript client]
    CLIENT --> WEB
```

## Deployment topology for the free pilot

```mermaid
flowchart TB
    GH[GitHub repository and Actions]
    OWNER[Product owner approval]
    PAGES[Cloudflare Pages frontend]
    VM[Oracle Cloud Always Free VM]
    R2[Cloudflare R2]

    OWNER --> GH
    GH --> PAGES
    GH --> VM

    subgraph VM
      PROXY[HTTPS reverse proxy]
      API[FastAPI]
      WORKERS[Celery workers and scheduler]
      PG[(PostgreSQL)]
      REDIS[(Redis)]
      PROXY --> API
      API --> PG
      API --> REDIS
      WORKERS --> PG
      WORKERS --> REDIS
    end

    API --> R2
    WORKERS --> R2
    PG -. encrypted backups .-> R2
```

## Trust boundaries

- Telegram WebApp identity is verified by the backend; client-supplied identity is never trusted directly.
- Telegram webhook authenticity and secret path/header are checked before ingestion.
- DeepSeek receives only the minimum context required for the task.
- Payment provider callbacks are verified and idempotent.
- Object-storage access is private by default and uses time-limited access where needed.
- Production databases and Redis are not exposed publicly.
