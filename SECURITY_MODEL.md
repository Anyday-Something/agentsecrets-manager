# Threat model: Anyday-Something portfolio

Shared threat model for the Anyday-Something repositories `agentos`, `foopoomoo`, `matchmii`, `matchmii-nhost` and `agentsecrets-manager`. The same file sits at the root of each repo so every security scan starts from it; keep the copies in sync. Everything below comes from reading the code, so it is **inferred** unless it is marked *(user)*. Edit it whenever an assumption is wrong.

## Components

| Component | Code it covers |
|---|---|
| **MatchMii Web Client** | `matchmii/src/`, `matchmii-nhost/src/`, Capacitor shells (`android/`, `ios/`), `nginx/`, `Dockerfile` |
| **MatchMii Appwrite Backend** | `matchmii/functions/*` (mainly `activity-ops`), `matchmii/appwrite.config.json`, `matchmii/cloudflare/*` (mail-reply, api-cache, maintenance workers), `matchmii/infra/`, `matchmii/scripts/` |
| **MatchMii Nhost Backend** | `matchmii-nhost/api/`, `matchmii-nhost/db/` (Postgres RLS, Hasura metadata), `matchmii-nhost/functions/`, `matchmii-nhost/infra/`, `matchmii-nhost/cloudflare/` |
| **FooPooMoo App** | `foopoomoo/src/`, `foopoomoo/shared/`, `foopoomoo/server/` (Node dev/web server), `ios/`, `android/` |
| **FooPooMoo AI Proxy** | `foopoomoo/worker/` (Cloudflare Worker + D1) |
| **AgentOS** | `agentos/apps/*` (server, worker, cli, web), `agentos/packages/*`, `agentos/infra/` |
| **AgentSecrets Menu** | `agentsecrets-manager/agentsecrets_gui.py`, `ui.html`, `menubar/main.swift` |

## System overview and trust boundaries

- **MatchMii** (production, matchmii.com / matchmii.mynu.me): a React PWA plus iOS/Android shells, talking to self-hosted Appwrite 1.9.6 at `api.matchmii.com` on Coolify. The client SDK can only grant document permissions to itself. Every cross-user write and every row used for authorisation or fan-out goes through the `activity-ops` function. Inputs are allowlisted in `src/fields.js`, and organiser checks use `activity-access.js`. Most functions have `execute: any`. `activity-ops` rejects callers without a JWT, except for `GUEST_ACTIONS` (`modules.invite`, `invite.resolve`, `peek`, `notify.unsubscribe`, `mail.inbound`, `roster.project`, `reservedSpots.resolve`, `search.key`). Cloudflare Workers handle reply-by-mail (`reply.matchmii.com` → signed reply tokens → `mail.inbound`), API caching and maintenance. Payments: Stripe Connect direct charges (`stripe.js`, `online-pay.js`, a webhook relayed through a Cloudflare Worker), Enable Banking bank checks (`enable-banking.js`, `bank.js`) and IBANs (`iban.js`).
- **MatchMii Nhost**: a Postgres/Hasura/Nhost Auth port of the same app. `api/` (Hono) verifies JWTs only in `auth.ts` (JWKS), runs requests as `app_user` under RLS, and runs the worker/cron as `app_worker` (only the `outbox_*` functions). Its Coolify stack binds to the Tailscale IP `100.83.0.42`, so it's probably staging *(inferred)*.
- **FooPooMoo**: on iOS all health data stays on the device (SQLite, no iCloud backup). The only internet-facing piece is the AI proxy Worker: `POST /v1/analyze` (no user auth, an `X-Install-Id` UUID, per-device/IP limits, daily/monthly USD budgets, CORS only for `capacitor://localhost`) holding `GEMINI_API_KEY`, and `/admin` (basic auth with `ADMIN_PASSWORD`, compared as an HMAC digest). The Node `server/` exposes FHIR endpoints with no auth. The README says production would need auth, so it's probably local/dev only *(inferred)*.
- **AgentOS**: pre-M0. The Syscall API (`apps/server`) uses a single bearer token (`AGENTOS_TOKEN`, constant-time compare). Staging is Tailscale-only with no public domain (`infra/coolify/README.md`). The design makes the Proxy the only secret decryptor and runs every external call through the Guard. Prompt injection into agent steps is in the design's threat surface.
- **AgentSecrets Menu**: a local macOS tool that serves `127.0.0.1:8787` and drives the `agentsecrets` CLI through subprocess. `_allowed()` checks the Host header (DNS rebinding). POSTs need an `X-Token` that is templated into `/`. GETs to `/api/*` also need the token.

## Entry points and who can reach them
- Anonymous internet: MatchMii web app, Appwrite public endpoints, the `activity-ops` guest actions, invite links/codes, unsubscribe links, the inbound mail Worker, the Stripe webhook relay, the calendar feed (token URLs), the FooPooMoo AI proxy `/v1/analyze` and `/admin`.
- Authenticated MatchMii users: every `activity-ops` action (chat, DMs, communities, tournaments, payments, profile, erase). Organisers, co-organisers, trainers, club admins and staff/superadmins add more power.
- Tailnet only: AgentOS staging, MatchMii Nhost staging, the Coolify UI.
- Localhost / any website in the user's browser: the AgentSecrets GUI.

## Assets and data sensitivity
- MatchMii: user PII (email, phone, emergency contacts, encrypted `profile_private`), private groups and chats, location, reliability scores, IBANs and bank-check results, Stripe Connect accounts and charges, the Appwrite server API key, Stripe/Enable Banking/SMTP2GO/FCM/VAPID secrets.
- FooPooMoo: health data (GDPR Art. 9, mostly on the device), the Gemini key and its spend budget, hashed usage stats.
- AgentOS: model provider keys, secrets (`sec://`), the ledger and budgets, workspace isolation.
- AgentSecrets: every secret value in the user's AgentSecrets workspaces, plus the domain allowlist.

## Attacker profiles (draft)
1. An anonymous internet attacker against the public MatchMii and FooPooMoo AI proxy surfaces.
2. A logged-in MatchMii user going after other users' data, private groups, organiser powers or payments (IDOR, broken access control in `activity-ops`).
3. A malicious website or local process attacking the AgentSecrets localhost server.
4. Prompt injection through user content into the AI flows (FooPooMoo talk bar, AgentOS agents).
5. Cost abuse: running up the Gemini or model budget.

## Existing controls
The server-written-rows rule plus field allowlists, `isActivityOrganizer`, collection permissions pushed by script and drift-checked, encrypted Appwrite attributes, signed mail-reply and unsubscribe tokens, Stripe webhook signature checks. Nhost: RLS roles with no bypass, enforced by `db/tests/schema_rules.sql`. FooPooMoo proxy: rate limits, budgets, body-size cap, MIME allowlist, HMAC-hashed ids. AgentOS: constant-time bearer token, Tailscale-only staging, an origin allowlist for probes. AgentSecrets: Host-header check, a per-run token, no values in the UI.

## Out of scope / accepted risk
*None stated by the user yet.*

## Open questions
- Which assets matter most (payments, PII/chats, health data/AI budget, secrets)?
- Which attackers count? Is staff/superadmin abuse accepted?
- Is `matchmii-nhost` staging only? Is `agentos` undeployed? Is `foopoomoo/server` dev only?
