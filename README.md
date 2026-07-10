# Aiditor

Aiditor is a natural-language multimedia editor for images, video, and audio. A user uploads source media, describes the desired result in a chat, and Aiditor uses Google generative models to plan an edit, runs the generated Python in a dedicated container, and returns the rendered files.

This repository is the public snapshot of the original product. The hosted experience at [aiditor.ai](https://aiditor.ai) is an archived showcase: new sign-ups are closed, but the landing page and two-minute product demo remain available. The full application source is preserved here for study and self-hosting.

## Highlights

- One conversational workflow for image, video, and audio editing
- Gemini-powered edit planning and Python generation
- Containerized execution with FFmpeg and common creative-processing libraries
- Image generation, video generation, transcription, subtitles, voice-over, and format conversion workflows
- Email/password and Google OAuth authentication
- Redis-backed users, chats, task status, and file metadata
- S3 object storage, SES transactional email, and Paddle billing integrations
- Responsive, framework-free HTML/CSS/JavaScript interface

## Architecture

Aiditor is a lightweight modular FastAPI application supported by Redis and a long-running Python execution container.

```text
Browser
  │
  ├── static UI (HTML, CSS, JavaScript)
  │
  └── FastAPI routes
        ├── authentication and users ── Redis
        ├── chats and task state ────── Redis
        ├── uploads and outputs ─────── Amazon S3
        ├── billing ─────────────────── Paddle
        ├── email ───────────────────── Amazon SES
        └── LLM orchestrator ────────── Gemini / Vertex AI
                    │
                    └── generated Python ── Docker sandbox ── media output
```

The backend is organized by responsibility:

```text
app/
├── api/          HTTP endpoints for auth, chats, files, users, billing, and admin
├── config/       environment-backed configuration and LLM instruction files
├── db/           Redis models and persistence operations
├── llm/          model client, prompt preparation, execution, and orchestration
├── services/     authentication, file storage, and container execution
├── tasks/        background metadata and thumbnail processing
└── utils/        logging, email, media inspection, security, and formatting
static/            browser application and archived landing pages
tests/             focused model, security, and repository checks
```

## Prerequisites

- Docker Engine with Docker Compose v2
- A Google Cloud project with Vertex AI enabled and a service-account key
- Redis, provided by the Compose stack
- An S3 bucket and AWS credentials; SES is required for password-reset email
- Paddle sandbox or production credentials if billing routes are used

Python 3.11 is used by both application images. Running the complete stack through Docker Compose is the supported local path.

## Setup

1. Clone the repository and enter it:

   ```bash
   git clone https://github.com/sathwik-mamidi/aiditor.git
   cd aiditor
   ```

2. Create local configuration from the public templates:

   ```bash
   cp .env.example .env
   cp gcp-credentials.json.template gcp-credentials.json
   ```

3. Replace every placeholder in `.env` and `gcp-credentials.json` with credentials from your own accounts. Both files are ignored by Git and must never be committed.

4. Build and start the stack:

   ```bash
   docker compose up --build
   ```

5. Open [http://localhost:3000](http://localhost:3000). The OpenAPI document is available at `http://localhost:3000/api/openapi.json`.

The application waits for Redis to become healthy. The first build of the media sandbox is intentionally substantial because it includes FFmpeg, OpenCV, Whisper, background-removal models, and related native libraries.

## Configuration

`.env.example` is the canonical configuration template.

| Group | Variables | Purpose |
| --- | --- | --- |
| Application | `APP_BASE_URL`, `NODE_ENV`, `PORT`, `LOG_LEVEL` | Public URL, runtime mode, server port, and logging verbosity |
| Redis | `REDIS_URL`, `TASK_STATUS_EXPIRY_SECONDS` | Connection string and background-task retention |
| Vertex AI | `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` | Google model authentication and region |
| Gemini | `GEMINI_MODEL_NAME`, `GEMINI_GENERATION_PROFILE`, `GEMINI_TEMPERATURE`, `GEMINI_TOP_P`, `GEMINI_TOP_K`, `GEMINI_API_KEY` | Model selection and generation controls; the API key is only used outside Vertex AI mode |
| Google OAuth | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Google sign-in credentials and callback URL |
| Sessions | `JWT_SECRET_KEY`, `REFRESH_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_SECONDS`, `REFRESH_TOKEN_EXPIRE_SECONDS`, `ADMIN_USER_IDS` | Token signing, expiry, and administrator allowlist |
| AWS | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`, `S3_PRESIGNED_URL_EXPIRY_SECONDS`, `SENDER_EMAIL_ADDRESS` | Media storage, signed downloads, and password-reset email |
| Paddle | `PADDLE_API_BASE_URL`, `PADDLE_API_KEY`, `PADDLE_CLIENT_TOKEN`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRO_PLAN_PRICE_ID`, `PADDLE_CREDITS_*_PRICE_ID` | Checkout, customer portal, webhook verification, and product mapping |

Use long, independently generated values for both token secrets. Set `NODE_ENV=production` behind HTTPS so authentication cookies are marked secure. The Google OAuth callback and Paddle webhook URLs must match `APP_BASE_URL` and the values configured with those providers.

## Commands

```bash
# Start the complete development stack
docker compose up --build

# Start it in the background
docker compose up -d --build

# Follow application logs
docker compose logs -f aiditor

# Stop containers while preserving Redis data
docker compose down

# Validate Python syntax
python -m compileall -q main.py app tests

# Run the focused test suite after installing requirements
python -m unittest discover -s tests -v

# Build the archived static site into dist/
./scripts/build-pages.sh
```

For a local Python environment, create a Python 3.11 virtual environment and install `requirements.txt`. Redis and the sandbox container are still required for end-to-end editing.

## Static showcase deployment

`scripts/build-pages.sh` copies only the landing page, legal pages, and their assets into `dist/`, rewriting FastAPI-specific asset paths for a flat static host. This is the build used for the archived showcase; it does not expose authentication or editing APIs.

For Cloudflare Pages, use:

- Build command: `bash scripts/build-pages.sh`
- Build output directory: `dist`

## Operational and security notes

- The Compose stack mounts the host Docker socket into the FastAPI container so it can copy and execute generated programs in `python-sandbox`. Access to the Docker socket is effectively host-level control. Run Aiditor only on a dedicated, trusted development host—not on a shared workstation or multi-tenant server.
- Generated code is inherently untrusted. The sandbox is separated from the web container and no longer receives the Docker socket or elevated Linux capabilities, but it is not a hardened multi-tenant isolation boundary.
- User uploads and generated files are persisted in S3; Redis stores application metadata and session state in the `aiditor-redis-data` volume.
- Model, storage, email, and billing integrations can incur external charges. Use sandbox/test accounts and provider budgets while evaluating the project.
- Rotate any credential that has ever been committed or shared. The repository contains templates only, not usable service credentials.
- The public hosted build is archival and is not accepting accounts. Self-hosted operators are responsible for provider configuration, privacy obligations, abuse controls, backups, and monitoring.

## About

Aiditor was designed and built by [Sathwik Mamidi](https://sathwikmamidi.com) as an exploration of a simple idea: creative software can be conversational without hiding the real media-processing tools underneath. Its interface coordinates Gemini, Imagen, Veo, Lyria, FFmpeg, and Python-based media libraries behind a single chat while preserving concrete, downloadable outputs.

The project is now published as a polished historical snapshot. Visit [aiditor.ai](https://aiditor.ai) to see the original product story and demo, or browse the source to study the orchestration and rendering pipeline.

## Contributing

This is an archived product snapshot, so broad feature development is not planned. Focused fixes for security, correctness, documentation, and reproducibility are welcome through GitHub issues and pull requests. Please include a concise rationale and the checks you ran.

## License

Aiditor is available under the [MIT License](LICENSE).
