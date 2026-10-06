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

Create an Azure OpenAI resource and a model deployment in Azure. Add the following values privately in Streamlit Community Cloud's **Manage app > Settings > Secrets**, or as server environment variables. Environment values take precedence. Do not paste the API key into chat or commit it.

```toml
AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
AZURE_OPENAI_DEPLOYMENT = "YOUR-DEPLOYMENT-NAME"
AZURE_OPENAI_API_VERSION = "2024-10-21"
AZURE_OPENAI_API_KEY = "YOUR-PRIVATE-KEY"
```

Use the deployment name, not the model name, and an API version supported by that deployment. Restart after changing configuration. Configuration presence alone does not verify Azure authentication or deployment availability; use the explicit connection test. All four values are required if any are provided. Keys are excluded from configuration representations.

Settings now exposes an AI research mode selector and an explicit connection-test button. Azure-assisted worker cycles send public evidence to the configured deployment and require a validated structured review. VETO, UNCERTAIN, concerns, missing credentials or request failures block new autonomous paper entries. A CLEAR review cannot override deterministic risk checks and is not comprehensive event clearance. Azure requests incur provider charges. No resource provisioning or private credential setup is automatic.

## Autonomous paper operation

Settings exposes **Start paper automation**, **Pause paper automation**, and a one-shot research cycle. Starting switches the paper kill switch off; pausing switches it on and cancels pending entries, not open positions. The persisted autonomous-paper-v1 policy removes human approval only for paper automation. BTC/ETH USDT spot, fresh 1h/4h LONG alignment, provider freshness/liquidity, one occupied position, 1% risk, 25% allocation and a 3% realized daily-loss veto remain enforced. Headlines are limited market context, not comprehensive macro/event clearance. Real orders remain disabled.

For unattended operation run this on a managed, always-on backend with the same persistent database as the dashboard:

```sh
MAXTRADE_DATABASE=/srv/maxtrade/data/scan_history.sqlite3 /srv/maxtrade/.venv/bin/python -m maxtrade.worker --watch
```

The process checks every 15 minutes with existing slot deduplication. Configure service supervision, startup, backups and monitoring externally. Streamlit Cloud cannot share this local file with another server; migrate both services to shared durable storage/hosting before claiming continuous hosted automation. Dashboard-only cycles stop when no operator runs them. Azure secrets configured in Streamlit Cloud are not automatically available to an external worker; set backend environment variables separately.

Paper performance uses all closed ledger positions, positive net P&L after modeled costs as wins, and excludes pending/open/cancelled positions. Breakeven is a non-win. Daily grouping uses UTC close date; drawdown is realized-only. Small samples do not establish reliability.

### Temporary local operation

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

History displays the latest 50 alerts and 20 reports; older rows remain stored. Review disk usage monthly and establish an explicit retention policy rather than silently deleting audit evidence. Back up with SQLite's online backup API/CLI `.backup`, not a raw file copy during writes; encrypt backups, store off-host and test restoration with the app stopped. External notifications require a chosen destination and separately configured credentials; only internal alerts are implemented here.

Before using paper outcomes operationally, verify scheduler uptime, shared path, source quotas/terms, backup restoration and forward evaluation. No profitability or live-trading readiness is implied by software tests.
