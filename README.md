# Ladi — NeuralVillage Official AI

Standalone, embeddable customer-support service using the repository's `u.json` knowledge base and Google Gemini. It uses only Python's standard library and SQLite FTS5.

## Cost controls

- Reindexing reads JSON, applies an explicit path allowlist, and updates SQLite. It never calls Gemini.
- Chat retrieves approved public snippets locally, then asks Gemini to write a conversational answer grounded in them. Approved FAQs and NeuralSpace navigation notes are context, not canned replies. Unmatched questions and medical-advice requests do not call Gemini.
- Repeat generic questions may reuse a cached Gemini-generated answer. Follow-up turns bypass the answer cache so Gemini can interpret the current session context. Questions that look personal are not cached; their text is never persisted.
- Up to six recent Q/A turns stay in browser memory and are sent with follow-ups for in-session context. They are not written to SQLite or used for cross-user training. Personal identifiers, possessive account questions, and medical content suppress history forwarding.
- `GEMINI_DAILY_CALL_LIMIT` places a persistent daily ceiling on model calls. The default is 200; set it to `0` to disable Gemini calls.
- Prompts are limited to approved public snippets and 256 output tokens. Chat text is not logged.

Gemini calls can still incur charges until the daily limit is reached. Set a Gemini API billing quota as a separate provider-side backstop.

Gemini is required for customer-facing answer generation. Without a key, the API returns a safe unavailable/unknown fallback rather than a raw KB answer. Set `GEMINI_API_KEY` in `supportbot/.env` or the process environment and restart the service. The service uses up to six recent turns only within the current page session; it does not train or persist learning across customers.

## Hosted knowledge and scoped groups

Production supports one PostgreSQL database with pgvector for vector and lexical knowledge retrieval. Install `supportbot/requirements-hosted.txt`, set `DATABASE_URL`, and enable the Postgres `vector` extension. Ladi stores public knowledge in `public` scope and private community knowledge in tenant/group-specific scopes.

Configure `WEB_SCOPE_MAP` as JSON mapping exact website origins to `{tenant_id, scope_id}` objects. Configure `CHANNEL_SCOPE_MAP` as JSON mapping `whatsapp:phone-or-group-id`, `messenger:page-id`, or `instagram:page-id` to scopes. Unmapped integrations receive public scope only. Never map an entire chat platform to a private scope without verifying that every recipient belongs in it.

Feedback is stored in `ladi_feedback`; it is never added to the vector knowledge automatically. An administrator lists and approves corrections through authenticated `/admin/feedback` and `/admin/feedback/approve` routes. Approved corrections are indexed only into the listed tenant/scope.

## Local setup

From the repository root, copy `supportbot/.env.example` to the private, gitignored `supportbot/.env`, or set variables in your shell/deployment secret manager. The service reads `.env` files but never `.env.example`; process environment variables take precedence. Do not put keys in `.env.example`, `u.json`, source code, or a browser widget. At minimum configure `GEMINI_API_KEY`, `ALLOWED_ORIGINS`, and a strong `SUPPORTBOT_ADMIN_TOKEN` before exposing the service.

PowerShell example:

```powershell
$env:GEMINI_API_KEY = 'YOUR_KEY'
$env:ALLOWED_ORIGINS = 'https://www.neuralvillage.org'
$env:SUPPORTBOT_ADMIN_TOKEN = 'USE_A_LONG_RANDOM_SECRET'
python -m supportbot.refresh
python -m supportbot
```

The local server listens on `http://127.0.0.1:8080`. Open `http://127.0.0.1:8080/demo` to view and test the working chat UI. `GET /health` checks availability. For production, run `supportbot.app:application` under a production WSGI server behind HTTPS and a reverse proxy.

## Embed on a website

Add this snippet to the site. Serve the bot from its own HTTPS origin and set that exact site origin in `ALLOWED_ORIGINS`:

```html
<script
  src="https://support.example.com/widget.js"
  data-endpoint="https://support.example.com/api/chat"
  data-title="NeuralVillage Support">
</script>
```

The widget uses a closed Shadow DOM, renders messages with `textContent`, stores no browser history, and warns users not to submit credentials or private health details. It keeps at most six recent turns in page memory for follow-up context; this context is cleared when the page is closed or refreshed.

## Refresh knowledge

1. Update `u.json` and review `supportbot/approved_paths.json`.
2. Add only exact JSON paths whose content has been approved for public support. Never allowlist `kb_meta`, `customer_support_ai_safety`, `repository_verified_facts`, HR, account, health-record, or unverified claim fields.
3. Run `python -m supportbot.refresh`, or send `POST /admin/reindex` with `Authorization: Bearer <SUPPORTBOT_ADMIN_TOKEN>`.

Refresh runs no Gemini analysis. The public allowlist in `approved_paths.json` plus `knowledge/neuralspace_workflows.json` provide 24 approved facts and 29 deep NeuralSpace workflow documents. Support records are not automatically made customer-visible just because they exist in `u.json`.

## Deep NeuralSpace knowledge

The workflow library covers account setup, Dashboard, MySpace, Profile, Brain Lab CSV import, scan-score calculation, repeated scan trends, Wellness tasks and scoring, mental-health check-ins, NeuroHealth Coverage, EMR uploads and paid access, the wallet, appointments, clinical care, medical tourism, Neurolearn, GreenSpace, notifications, privacy, and non-diagnostic limits. It is indexed locally during startup and refresh. Hosted PostgreSQL/pgvector mode uses the same scoped document model by tenant and channel scope.

## WhatsApp, Messenger, and Instagram

The `GET` and `POST /webhooks/meta` adapter supports WhatsApp text messages and Meta page/Instagram text events. Configure a Meta app webhook with the public HTTPS callback URL `https://support.example.com/webhooks/meta`, the `META_VERIFY_TOKEN`, and subscribed message events. Set `META_APP_SECRET`, `META_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `MESSENGER_PAGE_ID`, and/or `INSTAGRAM_USER_ID` as applicable. Set `META_GRAPH_VERSION` to a version currently supported by Meta. In Meta's dashboard, configure the same verify token and app secret.

Incoming POST signatures are verified with `X-Hub-Signature-256`. Attachments are ignored. Meta account approval, permissions, templates, opt-in rules, and channel policies remain the operator's responsibility. The adapter does not store inbound message content.

## Other channels

Any app, CRM, or messaging provider can call `POST /api/chat` from an allowed browser origin or integrate a server-side adapter that calls `ChatService.answer(message)`. Keep provider credentials on the server. Do not expose Gemini, Meta, or admin tokens to clients.

## Security and operations

- Use HTTPS, exact allowed origins, a reverse-proxy request-size limit, and provider-side quotas.
- The included in-memory rate limiter is per process; multiple workers require a shared limiter such as Redis at the edge.
- `Origin` checks are browser protections, not authentication. Apply edge rate limits and abuse monitoring before public launch.
- The daily call limit uses SQLite. For multiple workers, use one shared persistent database or a central budget service.
- Restrict admin endpoints with a long random token; never expose it in browser code.
- The starter KB allowlist excludes internal verification metadata, private Notion notes, repository analysis, and unapproved product claims.
- Customer messages are sent to Google Gemini only on cache misses with matching approved facts. Do not send sensitive or patient-specific data; confirm provider retention and privacy terms before deployment.
- Gemini HTTP 429 responses are surfaced safely as a quota-limited provider status. Other provider failures return a temporary-unavailable status. Raw provider errors and secret values are not written to application logs.

## API

`POST /api/chat` JSON body: `{"message":"How do I contact support?"}`. Response: `{"answer":"...","sources":["contact_and_support.general_email"],"cached":false}`.

`GET /admin/status` reports document count, live cache count, and today's model-call count. `POST /admin/reindex` refreshes the local index. Both require the admin bearer token.

Run tests from the repository root:

```powershell
python -m unittest discover -s supportbot/tests -v
```
#   s u p p o r t _ n e u r a l  
 #   s u p p o r t _ n e u r a l  
 # support_neural
