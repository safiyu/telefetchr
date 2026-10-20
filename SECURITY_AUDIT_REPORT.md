# Telefetchr — Security Audit (re-verified against current code)

Date: 2026-10-20 · Scope: **Telefetchr app only** (FastAPI + Telethon file fetcher,
`main.py`, `app/`, `docker-compose.yaml`, `dockerfile`, frontend).

This supersedes the earlier report, which was mis-scoped to the `jellytizen-docker`
homelab and described code that has since been hardened. Every finding below was
re-verified line-by-line against the working tree.

**Status: all previously-critical/high findings are fixed** in the uncommitted
working-tree changes (on top of `46dd709`). One residual foot-gun was fixed during
this pass (see F-1). Test suite: **17/17 passing**.

---

## Resolved (verified in code)

| # | Finding | Fix | Where |
|---|---------|-----|-------|
| R-1 | `X-Forwarded-For` spoofing → trusted-subnet auth bypass | `TRUSTED_PROXIES` now defaults to `127.0.0.1,::1` (was the whole RFC-1918 space). Forwarding headers are honored **only** when the direct socket peer is a listed proxy; the XFF chain is walked right-to-left and the first *untrusted* hop is used; if every hop is a trusted proxy the **left-most** hop is returned (defeats pinning a trusted-subnet IP). | `app/config.py:125`, `app/utils/auth_dependencies.py:80` |
| R-2 | Default JWT secret shipped in compose (`telefetchr-secure-dev-key-12345`) → token forgery | Known-weak keys rejected at startup; when `SECRET_KEY` is unset, a random 384-bit key is generated and persisted to `sessions/.secret_key` (mode 0600) so tokens survive restarts. No secret in the image or compose file. | `app/config.py:11`, `:20`, `:98` |
| R-3 | Default admin creds (`admin`/`admin123`) | `ADMIN_PASSWORD` is now **required**; empty/weak/default values (incl. `admin123`) refuse to boot. Compose no longer sets credentials; `env_file: .env` + `.env.example` template provided; `.env` is git-ignored. | `app/config.py:17`, `:125` |
| R-4 | No rate limiting on login / Telegram code endpoints (brute force + flood-ban) | Sliding-window limiter per resolved client IP: login 5 failed/15 min, `request-code` 5 total/15 min (every call sends a real code), verify/2FA 10 failed/15 min. Rejected attempts don't extend lockout; stale keys swept. | `app/utils/rate_limiter.py`, wired in `app/api/routes.py:59`, `:204`, `:224`, `:254` |
| R-5 | Wildcard CORS with credentials | `ALLOWED_ORIGINS` defaults to empty → same-origin only; explicit allowlist via env var. | `main.py:137` |
| R-6 | Dashboard XSS from Telegram-controlled names | `escapeHtml()` (text/attrs), `jsStr()` (values inside inline `onclick` JS strings — HTML metachars encoded as JS unicode escapes so they survive HTML-attribute decoding), and `progressElId()` (sanitized DOM ids) applied across file list, progress cards, queue, history, resume banner, user info, changelog. Toast messages rendered via `textContent`. Log viewer escapes timestamp/level/module/message. | `app/static/js/script.js:46`, `:53`, `:68`, `:84`; `app/static/logs.html:296` |
| R-6b | No CSP / missing `X-Content-Type-Options` | CSP meta on all three pages (`frame-ancestors 'self'`, `base-uri 'self'`, `form-action 'self'`, self-only `connect-src`); app-wide middleware adds `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` to every response. | `app/static/*.html:7`, `main.py:159` |
| R-7 | Path traversal on file serve | `basename` + canonical `resolve()` + `is_relative_to(SAVE_PATH)` jail; response forced to `Content-Disposition: attachment` with RFC 5987 percent-encoded filename (hostile names can't switch into inline HTML) + `nosniff`. | `app/api/routes.py:509` |
| R-8 | Cleartext Telegram session on host-mounted volume | `ensure_directories()` now calls `harden_session_permissions()`: sessions dir `0700`, `.session`/`.session-journal` `0600` (failures logged, non-fatal for host-owned volumes). | `app/config.py:150` |
| R-9 | Container ran as root | Multi-stage Dockerfile creates `appuser` (PUID/PGID overridable), `USER appuser`, no secrets in image. | `dockerfile:38`, `:60` |

## Fixed in this pass

**F-1 — `.env.example` placeholder password booted the app (LOW → closed).**
`ADMIN_PASSWORD=change-me-generate-a-strong-password` in `.env.example` was *not* in
the weak-password blocklist, so a verbatim `cp .env.example .env` would start the app
with a password readable in the repository — the exact class of bug the blocklist
exists to prevent. Added to `_WEAK_ADMIN_PASSWORDS`; startup now refuses it.
Verified: missing password → refused; placeholder → refused; real random password → boots.

## Remaining notes (accepted trade-offs / hardening backlog, all LOW)

- **N-1 — CSP `script-src 'unsafe-inline'`.** Required while the UI generates
  `onclick="…"` attributes and `login.html` carries an inline script. It weakens
  the CSP's XSS mitigation (the escaping in R-6 is the actual defense). Full
  removal requires rewriting handlers to `addEventListener` and externalizing the
  login script. Backlog.
- **N-2 — JWT in `localStorage`.** Standard trade-off for this SPA; safe only while
  no XSS exists (see N-1). A `HttpOnly` cookie would need CSRF tokens and is not
  warranted for a single-admin LAN app. Accepted.
- **N-3 — In-memory rate limiter** resets on restart and is per-process. Documented
  as acceptable for a single-process deployment; a restart is the only bypass and
  the Telegram side is additionally protected by Telegram's own flood control.
- **N-4 — `TRUSTED_SUBNETS=192.168.1.0/24` default in compose** is a LAN auth-bypass:
  every device on that subnet is admin without credentials. Deliberate convenience
  feature, commented to keep it narrow. Remove it if the box is internet-exposed
  (the compose `ports` mapping exposes 9868 on all interfaces).
- **N-5 — `/debug/*` endpoints** (state dump incl. first 500 bytes of the state
  file) are auth-gated but remain in production. Consider a `DEBUG=0` flag if you
  want them gone entirely.

## Verification performed

- Re-read every security-critical module: `config.py`, `main.py`, `routes.py`,
  `auth_dependencies.py`, `rate_limiter.py`, `auth_service.py`, `script.js` (all
  `innerHTML`/`insertAdjacentHTML`/`onclick` sinks), `login.html`/`view.html`/
  `logs.html` (CSP, token handling), `logs.html` renderer, `dockerfile`,
  `docker-compose.yaml`, `.env.example`, `.gitignore`.
- Grepped `script.js` for unescaped `${…}` interpolations: remaining ones are
  developer-controlled constants, numeric values, or already-sanitized
  (`escapeHtml`/`jsStr`/`progressElId`) outputs.
- `pytest tests/` → **17 passed**.
- Startup-guard checks: unset `ADMIN_PASSWORD` → `ValueError`; placeholder →
  `ValueError`; auto-generated `SECRET_KEY` persisted with 0600.

## Recommended next step

Commit the hardening as its own change (the tree currently mixes it with the
v2 state-manager work) and rotate: `cp .env.example .env`, set a fresh
`ADMIN_PASSWORD` (`python3 -c "import secrets; print(secrets.token_urlsafe(16))"`),
and — if any instance ever ran with the old compose defaults — treat previously
issued JWTs and the Telegram session as compromised and re-login.
