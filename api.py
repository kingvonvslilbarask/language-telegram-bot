"""HTTP surface of the bot service (one process, one port).

  POST /telegram/webhook            <- Telegram delivers updates here
                                       (validated with the secret-token header)
  POST /internal/payment-confirmed  <- our web backend calls these
  POST /internal/class-scheduled       (validated with the X-Api-Key header)
  POST /internal/teacher-message
  GET  /healthz                     <- uptime checks

Why not `application.run_webhook()`? It only serves Telegram's webhook. We also
need the internal endpoints above, so we run PTB with `updater(None)` and feed
its update queue from our own web server - the pattern PTB documents for
custom webhook setups. The bot still runs continuously as a single service.

The bot token and both secrets come from environment variables. The two
secrets are compared in constant time. Interactive API docs are disabled so
the API surface isn't advertised.
"""
import hmac
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from telegram import BotCommand, Update
from telegram.ext import Application
from telegram.request import HTTPXRequest

from .backend_client import WebBackendClient
from .config import Settings
from .handlers import register_handlers
from .ledger import DeliveryLedger
from .notifications import Notifier
from .schemas import ClassScheduled, PaymentConfirmed, TeacherMessage
from .sender import GuideVideo

logger = logging.getLogger(__name__)


def _ipv4_only_request(connect_timeout: float, read_timeout: float, write_timeout: float, pool_timeout: float) -> HTTPXRequest:
    """Force outgoing connections to api.telegram.org over IPv4.

    On some Windows setups, the machine has a non-functional IPv6 route.
    httpx/asyncio then tries the IPv6 address first, gets no response, and
    burns the whole connect_timeout before it would even try IPv4 - which
    looks exactly like `telegram.error.TimedOut`, even though a plain `curl`
    (which skips the broken IPv6 path) connects instantly. Binding the local
    socket to an IPv4 address makes an IPv6 connection impossible, so httpx
    goes straight to IPv4 and skips the hang entirely.
    """
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return HTTPXRequest(
        connect_timeout=connect_timeout, read_timeout=read_timeout,
        write_timeout=write_timeout, pool_timeout=pool_timeout,
        httpx_kwargs={"transport": transport},
    )


def create_app(settings: Settings) -> FastAPI:
    request = _ipv4_only_request(connect_timeout=10, read_timeout=20, write_timeout=60, pool_timeout=10)
    ptb = (
        Application.builder()
        .token(settings.bot_token)
        .updater(None)                    # we feed updates in ourselves (webhook)
        .concurrent_updates(True)
        .request(request)                 # IPv4-only transport, see _ipv4_only_request() above
        .get_updates_request(request)
        .build()
    )
    ledger = DeliveryLedger(settings.ledger_path)
    backend = WebBackendClient(settings.web_backend_url, settings.web_backend_api_key)
    video = GuideVideo(settings.guide_video_path, settings.guide_video_file_id, settings.guide_video_url)
    register_handlers(ptb, settings, backend)
    ptb.bot_data["video"] = video          # lets /start send the intro video after linking
    notifier = Notifier(ptb.bot, settings, ledger, video)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with ptb:                                   # initialises the bot (calls getMe)
            await ptb.start()
            webhook_url = f"{settings.webhook_base_url}/telegram/webhook"
            await ptb.bot.set_webhook(
                url=webhook_url,
                secret_token=settings.webhook_secret,     # Telegram echoes this in a header on every call
                allowed_updates=["message"],
                max_connections=40,
            )
            await ptb.bot.set_my_commands([
                BotCommand("start", "Connect your account"),
                BotCommand("myclasses", "See your upcoming classes"),
                BotCommand("id", "Show this chat's ID"),
                BotCommand("help", "What I can do"),
            ])
            logger.info("Bot @%s is running; webhook set to %s", ptb.bot.username, webhook_url)
            try:
                yield
            finally:
                await ptb.stop()
        await backend.aclose()
        ledger.close()

    app = FastAPI(title="Azela Telegram Bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        supplied = (x_api_key or "").encode()
        if not hmac.compare_digest(supplied, settings.internal_api_key.encode()):
            raise HTTPException(status_code=401, detail="invalid api key")

    async def guarded(name: str, coro):
        try:
            return await coro
        except Exception:
            logger.exception("Internal handler %s crashed", name)
            raise HTTPException(status_code=500, detail="internal error") from None

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request):
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").encode()
        if not hmac.compare_digest(supplied, settings.webhook_secret.encode()):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid json") from None
        update = Update.de_json(data, ptb.bot)
        if update is not None:
            await ptb.update_queue.put(update)            # handled asynchronously; reply to Telegram fast
        return Response(status_code=200)

    @app.post("/internal/payment-confirmed", dependencies=[Depends(require_api_key)])
    async def payment_confirmed(body: PaymentConfirmed):
        return await guarded("payment_confirmed", notifier.payment_confirmed(body))

    @app.post("/internal/class-scheduled", dependencies=[Depends(require_api_key)])
    async def class_scheduled(body: ClassScheduled):
        return await guarded("class_scheduled", notifier.class_scheduled(body))

    @app.post("/internal/teacher-message", dependencies=[Depends(require_api_key)])
    async def teacher_message(body: TeacherMessage):
        return await guarded("teacher_message", notifier.teacher_message(body))

    return app
