# Security

## Running it safely

OnYourTips is built to run on your own machine for one person.

- **No authentication.** `oyt serve` listens on your local network (`0.0.0.0:8000`) so your
  phone can reach it. Anyone on that network can read your stories and change your saved list.
  Run it only on a network you trust, and don't expose it to the internet. Put it behind a
  reverse proxy with authentication if you need remote access.
- **Plain HTTP.** There's no TLS. Use it on your home network, or a VPN such as Tailscale.
- **Fetched pages are untrusted.** Article text is extracted from arbitrary websites and passed
  to a local model. The app renders it as text, never as HTML, and summaries are validated
  against a schema before they're shown.
- **Secrets.** The only optional secret is `GITHUB_TOKEN` (a higher GitHub rate limit), read from
  the environment or `backend/.env`. `.env` and `backend/data/` are git-ignored; keep it that way.

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/sanjana-302/onYourTips/security/advisories/new)
for this repository. You'll get a reply within a week.
