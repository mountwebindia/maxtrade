# Compact Dashboard

The dashboard uses a wider desktop workspace, compact typography and metrics,
wrapping mobile columns, and persistent mobile bottom navigation. Chart display
settings and past signal records are collapsed by default; saved preferences
remain supported. New chart sessions default to Light. Price labels use two
decimals above one unit and eight below; underlying data and exports are unchanged.
Authentication, PAPER automation, kill-switch settings and risk checks are unchanged.

# Shadow Training

The research service can enable `--training-registry /app/data/training/models.sqlite3`.
It trains a fixed scikit-learn logistic classifier on checksum-verified, completed
1200-day Coinbase BTC/ETH USD datasets. Features are causal; labels are five-day
long net returns with a one-day entry delay, 10bps fees and 5bps slippage per side.
Three expanding chronological evaluation windows purge labels crossing train/test
boundaries. Training and scaling use training rows only. Candidate hyperparameters
are fixed; retrospective metrics do not establish improved accuracy.

The owner-only SQLite registry stores immutable model versions, dataset hashes,
weights, evaluation metrics and first-recorded forecasts. Identical datasets reuse
the model; forecasts are scored only after their outcomes mature. Daily forecasts
overlap, so their Brier score is descriptive, not an independent significance test.
New completed daily datasets produce new shadow candidates on the six-hour refresh.
Training errors are isolated and reported to the existing research monitor.

Training never modifies the PAPER ledger, approvals, risk limits, prompts or Azure
model weights. Azure remains the reviewer; Claude stays disabled until configured.
Models do not approve/veto trades and are not automatically promoted. The artifacts
`deployment/training.Dockerfile` and `deployment/training-research.override.yml`
update only the research container; worker and dashboard releases are independent.
Off-host backups remain deferred; the model registry is VM-local persistent data.

# Publishing and hosting MaxTrade

## GitHub source repository

Repository: https://github.com/mountwebindia/maxtrade

GitHub stores the source; it does not run this Python web app. GitHub Pages cannot serve Streamlit. The Actions workflow runs deterministic tests on pushes and pull requests; it does not deploy the app.

Never commit `.streamlit/secrets.toml`, `.env`, API credentials, or `data/`. The example secrets file intentionally contains empty values.

## Dashboard login

The dashboard now fails closed: without a valid username and password hash it displays a locked screen, not charts, scans, history or downloads. Configure these on the server, never in GitHub or chat.

1. Run `.venv/bin/python scripts/create_login_hash.py` in your own terminal. Enter a unique password of at least 16 characters at the hidden prompts. The output is a salted PBKDF2-SHA256 hash, not your password.
2. In Streamlit Community Cloud, open the app's **Manage app > Settings > Secrets**. Add `MAXTRADE_USERNAME = "your-chosen-username"` and the generated `MAXTRADE_PASSWORD_HASH` line. For local development use the ignored `.streamlit/secrets.toml` file instead.
3. Save secrets, restart the app if required, and sign in. Test incorrect credentials and sign-out in a separate browser session. No default username/password exists.

Sessions last one hour and are browser-session-only; reloads may require login. Sign-out clears session snapshots. Credential rotation invalidates existing sessions on their next request/poll. Chart polling checks access before fetching data. Five failed attempts impose a one-minute **session-local** cooldown; a new browser session can bypass it. This shared owner login is not a multi-user identity system, and the cooldown is not an internet-wide brute-force defense. Prefer private-app access restrictions or a rate-limited identity proxy before exposing account data; authenticated exchange access stays disabled. Previously downloaded data cannot be revoked. SQLite history is still shared among authenticated users of this account.

Publishing before setting these secrets deliberately locks the app. Password hashing does not configure the live secrets automatically; only the app owner can finish that step privately.

## Azure OpenAI backend configuration

### Claude outage fallback and specialist manager

The research manager assembles data quality, technical/regime, news, sentiment,
derivatives, historical, AI review, performance and challenger reports. These are
software specialists using existing evidence collectors, not independently trained
models. Historical/quality monitoring and performance analysis remain shadow-only;
no agent changes production rules or model weights. Missing event-calendar and
spot-depth coverage are explicit limitations. Risk approval is independent.

Configure `ANTHROPIC_API_KEY` and an account-accessible `ANTHROPIC_MODEL` privately.
Use Settings > Test Claude connection to verify a paid structured response, without
submitting a trade. Enable `MAXTRADE_CLAUDE_FALLBACK = true` to try Claude once after
an Azure timeout, connection failure, HTTP 429 or HTTP 5xx. Each cycle starts with
Azure again. Authentication errors, refusals, malformed responses, VETO and
UNCERTAIN never trigger another provider to seek a different answer.

Fallback reviews initially carry `shadow_only=true` and cannot approve PAPER
entries. Only explicit `MAXTRADE_CLAUDE_PAPER_ENABLED = true` permits a validated
Claude CLEAR/no-concerns review through the unchanged independent risk gates.
Compare forward outcomes before opting in; this setting does not certify accuracy.
Both-provider failure blocks new entries while reconciliation continues. Reviews
archive provider, model, fallback reason, total latency and returned token counts;
no monetary cost is inferred. No Claude key is bundled or automatically configured.
Changes must be deployed together to dashboard and worker before live use.

The configured non-secret defaults target the supplied `gpt-6-astra-2` deployment through Responses v1. Add the API key privately in Streamlit Community Cloud's **Manage app > Settings > Secrets**, preserving existing login settings, or as a server environment variable. Environment values take precedence. Do not paste the API key into chat or commit it. Endpoint and deployment can be overridden:

```toml
AZURE_OPENAI_ENDPOINT = "https://neilbisht.services.ai.azure.com/openai/v1/responses"
AZURE_OPENAI_DEPLOYMENT = "gpt-6-astra-2"
AZURE_OPENAI_API_KEY = "YOUR-PRIVATE-KEY"
```

Only the API key is needed when using these defaults. Responses v1 needs no dated API version: remove an existing `AZURE_OPENAI_API_VERSION`, or set it to `v1`. Requests use the deployment name as `model`, strict JSON schema output, a 4,096 output-token cap (including reasoning), and `store=false`. Incomplete, refused, blocked or malformed responses fail closed. Azure deployment guardrails apply automatically; the app does not change DefaultV2, deployment type, upgrade policy or rate limits. The supplied 250 RPM/250,000 TPM limits do not guarantee request availability; throttling blocks new entries rather than retrying indefinitely.

Legacy Chat Completions remains supported with an HTTPS resource root endpoint, deployment name, dated `AZURE_OPENAI_API_VERSION` and key. Restart after changing configuration. Configuration presence alone does not verify Azure authentication or deployment availability; sign in and use **Settings > Test Azure connection**, then save **Azure-assisted** mode. Keys are excluded from configuration representations. No live connection has been verified without the private key.

Settings now exposes an AI research mode selector and an explicit connection-test button. Azure-assisted worker cycles send public evidence to the configured deployment and require a validated structured review. VETO, UNCERTAIN, concerns, missing credentials or request failures block new autonomous paper entries. A CLEAR review cannot override deterministic risk checks and is not comprehensive event clearance. Azure requests incur provider charges. No resource provisioning or private credential setup is automatic.

## Autonomous paper operation

### Current Azure VM worker

The dashboard and independent `maxtrade-paper-worker` container share
`/app/data/scan_history.sqlite3` on the Azure VM. The host directory is
`/data/coolify/applications/benhw4xqxobdmkkr3c7t1qmx/maxtrade-data`.
The worker manifest is [deployment/worker.compose.yml](deployment/worker.compose.yml),
installed privately at `/opt/maxtrade-worker/compose.yml` on the VM.
On 2026-10-08, dashboard, paper worker, monitor and research worker were deployed
with `maxtrade-worker:manager-20261008`. This overlays the tested Python modules
on the existing `34388ef` runtime without dependency or trading-policy changes.
The source-only build uses [deployment/runtime.Dockerfile](deployment/runtime.Dockerfile).
The image overrides are [deployment/runtime-worker.override.yml](deployment/runtime-worker.override.yml)
and [deployment/runtime-dashboard.override.yml](deployment/runtime-dashboard.override.yml).

Effective owner-only manifests are persisted at `/opt/maxtrade-worker/compose.yml`
and `/data/coolify/applications/benhw4xqxobdmkkr3c7t1qmx/docker-compose.yaml`.
Release artifacts and original manifests (`worker-before.yml`,
`dashboard-before.yml`) are protected under
`/opt/maxtrade-worker/releases/manager-20261008`. For code rollback, restore the
appropriate original manifest and run Compose with project `maxtrade-worker` or
`benhw4xqxobdmkkr3c7t1qmx`, scoped to its MaxTrade services. Keep the live ledger;
do not restore a pre-deployment database over subsequent trading records.
The old runtime image remains available for rollback.

The pre-deployment online backup `manager-predeploy-20261008T062900Z.sqlite3`
passed integrity checking and isolated restore/count verification for all 12
tables. After rollout, dashboard and worker health passed, fresh BTC/ETH reports
contained nine specialist/challenger records and Azure reviews, and the worker
reported zero failures. Automation remained ON, kill switch OFF. Claude was not
configured; fallback and Claude PAPER approval remained disabled.

The owner selected GitHub release branch `release/manager-20261008` for Coolify
publication, keeping `main` unchanged because the existing Streamlit Cloud app
tracks that branch. Do not merge this release into `main` until Cloud automatic
updates have been addressed. Coolify must track the release branch before a
source rebuild; rebuilding the old branch would replace the runtime overlay
with older code. Dashboard redeployments do not automatically update the worker
image. The worker remains pinned to the verified manager runtime.

The worker runs BTC/ETH public research every 15 minutes, requires private Azure
and Telegram configuration, and enforces Azure-assisted paper review. Secrets
are mounted read-only from `/app/data/private/secrets.toml`. No host ports are
published. Docker `unless-stopped` provides restart supervision, and container
logs rotate at three 10 MB files. The heartbeat health check requires a successful
watch cycle within 35 minutes; unhealthy status alone does not restart Docker
containers or send an external outage alert.

On 2026-10-07, the owner explicitly chose to keep paper automation ON with the
kill switch OFF. Completed watch cycles reported zero failures; the shared
ledger integrity and database-specific worker lock were verified. Real orders
remain disabled. A fresh SQLite online backup was restored into an isolated
memory database and passed integrity checking. Same-VM backups are not disaster
recovery. On 2026-10-07 the owner deferred off-host backups, with Cloudflare R2
planned for a later setup. The imported ledger came from an authorized local
backup, not Cloud. Cloud deletion remains deferred until an off-host backup and
restore test have passed.

The separate `maxtrade-worker-monitor` container checks the read-only heartbeat
every minute and sends Telegram notifications on unhealthy/recovered transitions
for both paper cycles and the six-hour research refresh. Research is unhealthy
when its report is missing, eight hours old, future-dated, or reports failures.
Successful delivery is acknowledged in private persisted state; failed delivery
is retried. It cannot detect or report a whole-VM or network outage while that
same host is unavailable. External outage monitoring remains pending.

Coolify administration is available at
https://coolify.20.127.223.248.sslip.io/ with a trusted TLS certificate.
[deployment/coolify-admin.yaml](deployment/coolify-admin.yaml) routes only that
host to the admin service and its `/app/` WebSocket path to realtime port 6001.
The secure WebSocket handshake was verified. Existing port 8000 access remains
unchanged; this setup does not restrict public admin access or replace login.

The independent `maxtrade-research-worker` runs continuously with Docker restart
supervision and refreshes completed UTC candles every six hours. It ingests
1,200 daily days and the latest 30 hourly days for BTC/ETH into the shared
`/app/data/historical.sqlite3`, with journals in the same directory. Its paper
ledger file is mounted read-only. Provider failures are isolated by market and
interval. The first deployed cycle on 2026-10-07 reported complete coverage,
zero missing bars and zero failures in all four requested ranges; daily shadow
contexts were `AVAILABLE` with `execution_enabled=False`.

The private persisted report is
`/opt/maxtrade-worker/research-state/research-status.json`. It includes coverage,
regime and cost-aware forward evaluation of recorded predictions. The first
cycle reported `WAITING FOR MATURE COST-AWARE SAMPLES`. This is evidence
collection and shadow evaluation, not model training or automatic strategy
improvement. Rules and model weights remain unchanged. Recent complete hourly
coverage does not resolve the original one-year gaps or certify CoinDCX execution.
Previously inspected holdout data cannot become an untouched test by reuse.
The existing two-day daily-context freshness veto still applies during outages.

Run these commands on the VM:

```sh
sudo -n docker compose -f /opt/maxtrade-worker/compose.yml up -d --wait
docker inspect --format '{{.State.Health.Status}}' maxtrade-paper-worker
sudo -n docker compose -f /opt/maxtrade-worker/compose.yml stop
```

Stopping the worker also stops open-position reconciliation. Use dashboard
**Pause paper automation** to block new entries while keeping research and
reconciliation running. Do not run cron or a second watch process alongside this
worker. The Mac and Cloud ledgers are separate and have not been retired.
Before updating the worker, take a fresh online backup, pin the tested image,
validate the Compose manifest, and verify a new completed heartbeat after launch.

Settings exposes **Start paper automation**, **Pause paper automation**, and a one-shot research cycle. Starting switches the paper kill switch off; pausing switches it on and cancels pending entries, not open positions. The persisted autonomous-paper-v1 policy removes human approval only for paper automation. BTC/ETH USDT spot, fresh 1h/4h LONG alignment, provider freshness/liquidity, one occupied position, 1% risk, 25% allocation and a 3% realized daily-loss veto remain enforced. Headlines are limited market context, not comprehensive macro/event clearance. Real orders remain disabled.

For unattended operation run this on a managed, always-on backend with the same persistent database as the dashboard:

```sh
MAXTRADE_DATABASE=/srv/maxtrade/data/scan_history.sqlite3 /srv/maxtrade/.venv/bin/python -m maxtrade.worker --watch
```

The process checks every 15 minutes with existing slot deduplication. Configure service supervision, startup, backups and monitoring externally. Streamlit Cloud cannot share this local file with another server; migrate both services to shared durable storage/hosting before claiming continuous hosted automation. Dashboard-only cycles stop when no operator runs them. Azure secrets configured in Streamlit Cloud are not automatically available to an external worker; set backend environment variables separately.

Paper performance uses all closed ledger positions, positive net P&L after modeled costs as wins, and excludes pending/open/cancelled positions. Breakeven is a non-win. Daily grouping uses UTC close date; drawdown is realized-only. Small samples do not establish reliability.

### Temporary local operation

The macOS LaunchAgent installer provides login startup and process restart:

```sh
.venv/bin/python scripts/install_worker.py install
.venv/bin/python scripts/install_worker.py status
```

Before activating cycles, privately create `.streamlit/secrets.toml` from the example and add the same Azure and Telegram values used on Cloud. Include local login settings for the local dashboard. Never send credentials through chat. Run `chmod 600 .streamlit/secrets.toml`, then `.venv/bin/python scripts/install_worker.py restart`. The installed service requires both integrations, reloads private configuration every cycle, and forces Azure-assisted review. Missing/invalid configuration prevents the entire cycle, including open-position reconciliation; do not rely on monitoring until a completed heartbeat is verified. It does not automatically change the paper automation or kill-switch setting.

The service uses the existing local `data/scan_history.sqlite3`, not the Cloud ledger. A database-specific OS lock prevents duplicate CLI workers. Logs are in `data/worker.stdout.log` and `data/worker.stderr.log`; monitor disk usage and arrange log rotation for prolonged operation. `scripts/install_worker.py stop` unloads the service; `install` loads it again. Do not run manual watch mode alongside the service. Login startup is not boot-before-login startup; sleep, logout, shutdown and loss of connectivity interrupt operation. Keep the Mac awake/online, or move both dashboard and worker to a managed VPS for Mac-independent 24/7 service. No hosting purchase or Cloud-to-local credential transfer is performed.

Until the VPS is available, run the dashboard and worker on the same Mac and database. From the repository root, start the dashboard with its existing VS Code task and run the worker in a separate terminal:

```sh
MAXTRADE_DATABASE="$PWD/data/scan_history.sqlite3" .venv/bin/python -m maxtrade.worker --watch
```

Enable **Start paper automation** in the local dashboard Settings. Do not run a second worker. Keep the Mac awake, online and the worker terminal open; this setup does not survive sleep, shutdown or terminal closure automatically. Local positions and statistics do not appear in the separate Streamlit Cloud database. Use **Pause paper automation** to block new entries and cancel pending entries; keep the worker running to reconcile open positions. Ctrl+C stops the worker, including open-position monitoring.

For the Hostinger migration, use a VPS rather than shared hosting, migrate the ledger with SQLite's online backup API, and give both services the same absolute database path. Configure a non-root service account, automatic restart, HTTPS/WebSocket reverse proxy, private credentials, off-host backups and monitoring before enabling the hosted policy. Never run watch mode and cron together.

## Current Hostinger shared hosting

This app requires a persistent Python process and WebSocket support. Ordinary shared/WordPress hosting is not an appropriate runtime for Streamlit. Confirm specific capabilities with Hostinger before assuming otherwise. Uploading the repository into `public_html` will not run it.

For access from an existing Hostinger website, add a navigation link to `https://maxtrade-mountwebindia.streamlit.app/`, or use hPanel's domain redirect feature to redirect a chosen subdomain/path to that URL. A redirect changes the browser address to Streamlit; it does not host the app on Hostinger or bypass its login. Avoid iframe embedding because it can complicate authentication and mobile navigation. No Hostinger configuration or DNS changes have been made.

To keep an address such as `trade.yourdomain.com` while actually running the app on Hostinger, use a VPS with an HTTPS reverse proxy, WebSocket forwarding, managed Python service and durable storage. A shared-hosting MySQL database alone cannot run the dashboard or its background agents. Confirm your exact plan with Hostinger before buying or changing DNS.

### Fastest live-link option: Streamlit Community Cloud

1. Sign in at https://share.streamlit.io/ with the GitHub account that can access this repository.
2. Create an app from `mountwebindia/maxtrade`, branch `main`, entrypoint `app.py`.
3. Choose an available app URL and deploy. Cloud installs `requirements.txt`.
4. Keep the first deployment public-market-data only. Do not add CoinDCX credentials to a publicly accessible app.
5. Test spot/futures scans and downloads from the generated HTTPS URL.

You can put a normal link to the generated URL on a website hosted by Hostinger. A redirect from a Hostinger-hosted page is another option. Custom-domain support depends on the chosen app host; DNS alone does not turn shared hosting into a Python server.

**History limitation:** the SQLite file is local to the running instance. Cloud restarts/redeployments may lose it, and it is not suitable for multiple replicas. Use an external database and access controls before promising durable hosted history.

## Hostinger VPS / own-domain alternative

A VPS can run Streamlit behind an HTTPS reverse proxy with WebSocket forwarding. Keep Streamlit bound to loopback, run it under a non-root managed service, protect access, configure a persistent data directory and backups, and point your domain/subdomain to the VPS. No VPS or DNS changes have been made by this project.

Before enabling account credentials remotely: implement authentication/access control, secret management, read-only account access, and audit logging. Order execution remains disabled.

## Research worker and persistent storage

The worker supports one-shot or watch operation and is limited to BTC/ETH USDT spot. It reconciles paper positions, saves research and records internal alerts. When autonomous paper mode is explicitly started, it can submit eligible simulated entries without human review. It never submits real orders. Run dashboard and worker against the same local, persistent SQLite path, with one dashboard replica:

```sh
export MAXTRADE_DATABASE=/srv/maxtrade/data/scan_history.sqlite3
/srv/maxtrade/.venv/bin/python -m maxtrade.worker
```

Use an absolute database path, restrict filesystem access to the service account, and set that environment variable on the dashboard service too. `--database` overrides only the worker path; avoid accidentally splitting ledgers. Ordinary Streamlit Community Cloud storage is not a durable backend. No VPS, cron job, external database or DNS configuration has been activated.

On a provisioned VPS, an operator can install this crontab entry after adjusting paths and creating a service-owned log directory:

```cron
*/15 * * * * cd /srv/maxtrade && MAXTRADE_DATABASE=/srv/maxtrade/data/scan_history.sqlite3 /srv/maxtrade/.venv/bin/python -m maxtrade.worker >> /srv/maxtrade/logs/worker.log 2>&1
```

Jobs claim each symbol once per UTC 15-minute slot; concurrent/repeated runs skip claimed slots. Failed slots are retried only on the next scheduled slot. Each provider request has a timeout and news downloads are bounded. Configure log rotation, disk-space and nonzero-exit monitoring. A paper reconciliation failure is logged while research continues; missing bars require recovery before advancing an open position. This is not a continuously running exchange watcher.

History displays the latest 50 alerts and 20 reports; older rows remain stored. Each completed worker cycle saves a heartbeat and creates the first verified SQLite online backup of the UTC day in `data/backups/paper-YYYY-MM-DD.sqlite3`. Settings warns when the last completed cycle is over 30 minutes old. Same-disk backups are not disaster recovery, and Streamlit Cloud may lose both primary and backup files. Review disk usage, encrypt and replicate backups off-host, and test restoration with the app stopped.

Aligned autonomous technical signals enter the forward-accuracy ledger even when paper risk blocks them. Worker cycles score unresolved outcomes from completed hourly candles. Net paper win rate uses closed trades after modeled costs; technical target accuracy uses gross signal scenarios. Daily reports contain a next-day provisional result and a 48-hour update. All dates use UTC; reports run on the first available cycle, not a guaranteed wall-clock delivery.

## Telegram paper alerts

1. Create a bot privately through Telegram's verified **@BotFather** using `/newbot`. Keep its token private.
2. Open your bot and send `/start`. Obtain the numeric chat ID privately from Telegram Bot API `getUpdates`; group IDs may be negative. Never paste the token or token-bearing request URL into chat, screenshots or logs.
3. Preserve existing secrets and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as quoted strings in Streamlit **Manage app > Settings > Secrets**. Set the same variables in the independent worker's private service environment. Cloud secrets do not propagate to a Mac or VPS.
4. Use **Settings > Test Telegram delivery**. Configuration presence alone does not verify delivery.

Messages include PAPER ONLY research decisions, queued/open/closed/cancelled positions and daily reports. Attempts and acknowledgements persist per alert and destination. Failed sends retry after five minutes on a subsequent cycle. At most 20 messages are sent per cycle; only alerts created in the last 24 hours are eligible, while older records remain saved. Delivery is at-least-once: a crash after Telegram accepts a message but before local acknowledgement can cause a duplicate. Outages can prevent or delay delivery.

Credentials are not configured by publishing source. Do not grant the bot exchange permissions. Ordinary Telegram chats are not end-to-end encrypted; choose a private destination. A managed persistent host, off-host backups and private service configuration remain prerequisites for Mac-independent 24/7 operation.

Before using paper outcomes operationally, verify scheduler uptime, shared path, source quotas/terms, backup restoration and forward evaluation. No profitability or live-trading readiness is implied by software tests.
