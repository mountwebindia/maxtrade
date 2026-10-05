# Publishing and hosting MaxTrade

## GitHub source repository

Repository: https://github.com/mountwebindia/maxtrade

GitHub stores the source; it does not run this Python web app. GitHub Pages cannot serve Streamlit. The Actions workflow runs deterministic tests on pushes and pull requests; it does not deploy the app.

Never commit `.streamlit/secrets.toml`, `.env`, API credentials, or `data/`. The example secrets file intentionally contains empty values.

## Current Hostinger shared hosting

This app requires a persistent Python process and WebSocket support. Ordinary shared/WordPress hosting is not an appropriate runtime for Streamlit. Confirm specific capabilities with Hostinger before assuming otherwise. Uploading the repository into `public_html` will not run it.

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
