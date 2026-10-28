> **Deploying online (free)?** See `DEPLOY_ONLINE.md` in the website package, which covers Render + Vercel + Supabase step by step.

# Azela Telegram Bot

Messaging service for the live-class system. **The website/backend is the source of truth; this bot only delivers messages.** It never stores course, student or payment data (only a tiny "already delivered" ledger so retries don't send duplicates).

```
Student pays ─► Web backend ──POST /internal/payment-confirmed──► THIS BOT ──► student (welcome + video)
Teacher posts Zoom link ────► Web backend ──POST /internal/class-scheduled──►      ├─► teacher + admins ("Student X has enrolled in Course Y")
Teacher writes a message ───► Web backend ──POST /internal/teacher-message──►      └─► every student, one by one, by chat ID
Telegram ──POST /telegram/webhook (secret header)──► THIS BOT   (/start, /help, /id, /myclasses)
```

## 1. Security first

- **Your bot token was pasted into a chat.** Treat it as compromised: open **@BotFather → `/revoke`** (choose your bot) to get a new token, and only ever put the new one in your `.env` / host settings. The token is not in any file in this project.
- Three secrets, all from environment variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` (proves a request came from Telegram), `INTERNAL_API_KEY` (proves a request came from your web backend). Both secrets are compared in constant time.
- **Call the bot from server code only** (Supabase Edge Function, your API server). Never put `INTERNAL_API_KEY` in the Vite/React browser code.
- The bot's logs never contain the token (`httpx` logging is silenced because Telegram URLs embed the token).

## 2. Setup

Requires Python 3.10+.

1. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`), then `pip install -r requirements.txt`
2. `cp .env.example .env` and fill it in. Generate each secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Put your onboarding video at `assets/guide.mp4` (H.264 mp4, under 50 MB).
4. Telegram only calls **public HTTPS** URLs. Locally, expose port 8080 with a tunnel (`cloudflared tunnel --url http://localhost:8080` or `ngrok http 8080`) and put that address in `WEBHOOK_BASE_URL`.
5. Run: `python -m bot.main` — it registers the webhook with Telegram automatically and keeps running.
6. **Staff setup (once):** each admin and teacher opens the bot, presses **Start**, sends `/id`, and gives you the number. Put admin numbers in `ADMIN_CHAT_IDS`; the web backend stores each teacher's number on their profile.

Deploy with the included `Dockerfile` (mount a volume at `/app/data` so the ledger survives restarts) or any host that runs a long-lived Python process behind HTTPS (VPS + systemd + Caddy/nginx, Render, Railway, Fly.io).

## 3. How students get their chat ID (secure linking)

Telegram bots **cannot** message someone by username, and only after that person has pressed Start. So:

1. Website creates a one-time token for the student (1–64 chars `A-Za-z0-9_-`, expiring) and shows the button `https://t.me/<BOT_USERNAME>?start=<TOKEN>` (the payment page's confirmation screen does this when given `?bot=` and `?token=`).
2. Student taps it → Telegram opens the bot → student presses **Start** → bot receives `/start <TOKEN>`.
3. Bot calls your backend (`POST {WEB_BACKEND_URL}/telegram/link`, see §5). The backend verifies the token and saves the `chat_id` on that user.
4. From then on your backend passes that `chat_id` in every request below.

If a student has no `chat_id` yet, the bot doesn't fail: that recipient is reported as `skipped: not_connected` and staff are told the student hasn't connected Telegram.

## 4. Internal API (web backend → bot)

Every request needs header `X-Api-Key: <INTERNAL_API_KEY>` and `Content-Type: application/json`. Responses are always per-recipient so you can store **notification status separately from payment/enrollment status** — a failed Telegram message must never make a paid enrollment look failed.

**`POST /internal/payment-confirmed`** — call after the payment is verified server-side.

```json
{
  "enrollment_id": "enr_123",
  "course_title": "English — VIP",
  "student": { "name": "Abebe Kebede", "chat_id": 123456789 },
  "teacher": { "name": "Sarah Bekele", "chat_id": 987654321 },
  "schedule_text": "Mon, Wed, Fri, Sat at 7:00 PM",
  "amount": 100, "currency": "USD"
}
```
Sends the student a welcome message + the guide video, and the teacher and every admin *"Student Abebe Kebede has enrolled in English — VIP."* (admins also see teacher and amount). Student and staff messages go out concurrently, so staff hear immediately even while the video uploads. Response: `student.welcome`, `student.video`, `teacher`, `admins[]` (each `{ok, error_code, error, skipped, deduplicated}`) and `all_delivered`.

**`POST /internal/class-scheduled`** — when a teacher schedules a class or posts/changes a Zoom link.

```json
{
  "course_title": "English — VIP",
  "starts_at": "2026-09-28T19:00:00+03:00",
  "duration_minutes": 60,
  "zoom_url": "https://zoom.us/j/123456789",
  "topic": "Past tense practice",
  "is_update": false,
  "recipients": [ { "name": "Abebe", "chat_id": 123456789 }, { "name": "Hana", "chat_id": 555 } ],
  "group_chat_id": null,
  "idempotency_key": "session_77:v1"
}
```
Each student gets the class time (in school time), a **Join Zoom** button and the link. Use a **new** `idempotency_key` when the teacher changes the link (e.g. `session_77:v2`, `is_update: true`) so students get the update; resending the *same* key never duplicates.

**`POST /internal/teacher-message`** — custom text, links and documents from the teacher dashboard.

```json
{
  "sender_name": "Sarah Bekele",
  "text": "Homework for Wednesday is attached.",
  "links": ["https://example.com/reading"],
  "attachments": [ { "url": "https://files.example.com/hw.pdf", "filename": "homework.pdf" } ],
  "recipients": [ { "name": "Abebe", "chat_id": 123456789 } ],
  "idempotency_key": "msg_501"
}
```
Sent to each student individually by chat ID (optionally also once to a Telegram `group_chat_id` the bot was added to — use `/id` inside the group to get it). Attachments are fetched by Telegram from the URL (max ~20 MB by URL; the URL must be publicly reachable). Response: `{total, delivered, duplicates, failed, skipped, details[]}` with a status and error code per person.

Try it:
```bash
curl -X POST "$BOT_URL/internal/class-scheduled" -H "X-Api-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"course_title":"Test","starts_at":"2026-09-28T19:00:00+03:00","zoom_url":"https://zoom.us/j/1","recipients":[{"name":"Me","chat_id":YOUR_CHAT_ID}]}'
```

Calling it from a Supabase Edge Function (server side):
```ts
await fetch(`${Deno.env.get("BOT_URL")}/internal/payment-confirmed`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Api-Key": Deno.env.get("BOT_INTERNAL_API_KEY")! },
  body: JSON.stringify({ enrollment_id, course_title, student: { name, chat_id }, teacher, schedule_text, amount, currency: "USD" }),
});
```

### Delivery guarantees
- Blocked the bot / chat not found → logged, reported, **not retried**. Flood control → waits the time Telegram asks, retries. Timeouts/network errors → retries with backoff (3 attempts). A retry after a timeout that Telegram had already accepted can rarely cause one duplicate; that's the deliberate trade-off (a duplicate beats a missing class link).
- Each message is recorded under a key (`payment:<enrollment_id>:student:welcome`, `<idempotency_key>:<chat_id>`, …). Re-sending a request only sends what wasn't delivered before.
- Broadcasts pace themselves (~20 msgs/s, under Telegram's ~30/s limit).

## 5. What the bot needs from your web backend

Both calls send `X-Api-Key: <WEB_BACKEND_API_KEY>`.

- `POST {WEB_BACKEND_URL}/telegram-link` with `{"token","chat_id","username","first_name"}` → `200 {"display_name":"Abebe","role":"student|teacher|admin"}`. Unknown / expired / used token → `404` or `410` (the bot tells the user the link expired). Tokens should be single-use.
- `GET {WEB_BACKEND_URL}/telegram-classes?chat_id=123` → `{"classes":[{"title":"English — VIP","next_session":"Mon 7:00 PM"}]}` (only for `/myclasses`; optional).

Leave `WEB_BACKEND_URL` empty to disable both; the notification endpoints don't need it.

## 6. Commands

`/start [token]` (greeting, or link account) · `/help` · `/id` (chat ID; works in groups) · `/myclasses`. Other private text gets a short polite reply.

## 7. Tests — and what was and wasn't verified

- `python tests/test_core.py` — config validation, ledger persistence, time-zone formatting, message wording (6 tests, stdlib only).
- Sender/notifier logic (blocked users, timeouts, flood control, idempotent retries, broadcasts, skipped recipients) was exercised against a stand-in Telegram bot: all 16 checks passed.
- **Not run against real Telegram**: the build environment had no internet and no `python-telegram-bot` install. Do a live smoke test after deploying: `/id` → curl `class-scheduled` to yourself → a full `payment-confirmed` with your own chat ID as student, teacher and admin.

## 8. Demo mode (screen-share)

`demo.py` stands in for the web backend, so on Windows you don't need curl/JSON:

```
python demo.py payment
python demo.py class
python demo.py message "Homework is attached"
```
Set `DEMO_CHAT_ID` (and optionally `DEMO_TEACHER_CHAT_ID`) in `.env`. Add `--dry-run` to print the request without sending. Each run uses a fresh idempotency key so you can repeat it freely.

## 9. Using it with the Supabase web backend

Set `WEB_BACKEND_URL=https://<your-project>.supabase.co/functions/v1` and `WEB_BACKEND_API_KEY` to the same value as the Supabase secret `BOT_INTERNAL_API_KEY`. The bot then calls the `telegram-link` and `telegram-classes` Edge Functions. In the other direction, Supabase calls this bot using the secrets `BOT_URL` (this service's public https URL) and `BOT_INTERNAL_API_KEY` (= this bot's `INTERNAL_API_KEY`).
