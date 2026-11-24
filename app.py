"""app.py - Slack side of Ask Bob Uecker (Socket Mode, no public URL needed).

Needs three environment variables: ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, SLACK_APP_TOKEN.
Optional: ESCALATION_CONTACT (who the bot names when it cannot answer).
"""
import logging
import os
import sys

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import core
import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("ask-bob-uecker")

for var in ("ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
    if not os.environ.get(var):
        sys.exit(f"Missing environment variable {var}. Add it in Render under Environment.")

app = App(token=os.environ["SLACK_BOT_TOKEN"])
_topics = core.load_topics()
CTX = {"topics": _topics, "docs": core.load_manuals(_topics), "prompt": core.load_prompt()}
BOT_ID = app.client.auth_test()["user_id"]
SKIP_SUBTYPES = {"message_changed", "message_deleted", "bot_message", "channel_join", "file_share_hidden"}


def thread_messages(client, channel, thread_ts, current_ts):
    """The thread so far, oldest first. Falls back to nothing if Slack refuses."""
    try:
        r = client.conversations_replies(channel=channel, ts=thread_ts, limit=20)
        return [m for m in r.get("messages", []) if m.get("ts") <= current_ts]
    except Exception:
        log.exception("could not read thread history")
        return []


def dm_history(client, channel, current_ts):
    """Recent DM conversation, oldest first (last 6 messages, up to 2 hours old)."""
    try:
        oldest = str(float(current_ts) - 7200)
        r = client.conversations_history(channel=channel, oldest=oldest, latest=current_ts, inclusive=True, limit=6)
        return list(reversed(r.get("messages", [])))
    except Exception:
        log.exception("could not read DM history")
        return []


def handle(event, client, is_dm):
    channel = event["channel"]
    ts = event["ts"]
    if is_dm and not event.get("thread_ts"):
        # DMs: answer in the main conversation so follow-ups keep their context.
        thread = dm_history(client, channel, ts) or [event]
        reply_ts = None
    else:
        reply_ts = event.get("thread_ts") or ts
        thread = thread_messages(client, channel, reply_ts, ts) or [event]
    kwargs = {"thread_ts": reply_ts} if reply_ts else {}
    placeholder = client.chat_postMessage(channel=channel, text="Looking that up...", **kwargs)
    try:
        reply = core.respond(thread, BOT_ID, CTX, engine.route, engine.answer)
    except Exception:
        log.exception("failed to answer")
        reply = core.ERROR_REPLY
    client.chat_update(channel=channel, ts=placeholder["ts"], text=reply)


@app.event("app_mention")
def on_mention(event, client):
    if event.get("bot_id") or event.get("user") == BOT_ID:
        return
    handle(event, client, is_dm=False)


@app.event("message")
def on_message(event, client):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("user") == BOT_ID or event.get("subtype") in SKIP_SUBTYPES:
        return
    handle(event, client, is_dm=True)


if __name__ == "__main__":
    log.info("Ask Bob Uecker starting")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
