"""engine.py - talks to Claude. Nothing else in the bot imports the Anthropic SDK.

Reply parsing lives in extract_text(). It reads every text block in the reply
instead of assuming the first block is text. This is the fix for the
"Something went wrong" error seen on the Compliance Bot's first run.
"""
import os
import time
import logging

log = logging.getLogger("ask-bob-uecker")

import anthropic

ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "claude-haiku-5-5")
ANSWER_MODEL = os.environ.get("ANSWER_MODEL", "claude-sonnet-5-5")

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=3, timeout=60.0
        )
    return _client


def extract_text(response):
    """Join all text blocks of a Messages API reply. Never raises on odd shapes."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def route(system, messages):
    """Fast model call used to pick topics. Returns raw text."""
    r = _create(
        model=ROUTER_MODEL, max_tokens=200, system=system, messages=messages
    )
    return extract_text(r)


def _create(**kw):
    """Call the API with extended thinking switched off (hidden thinking tokens count
    against max_tokens and can cut an answer off mid-sentence). If this API version
    rejects the parameter, retry without it."""
    try:
        return client().messages.create(thinking={"type": "disabled"}, **kw)
    except anthropic.BadRequestError:
        log.warning("thinking parameter rejected, retrying without it")
        return client().messages.create(**kw)


def answer(system_blocks, messages):
    """Answer model call. system_blocks may carry cache_control on the documents."""
    t0 = time.time()
    r = _create(model=ANSWER_MODEL, max_tokens=1500, system=system_blocks, messages=messages)
    u = getattr(r, "usage", None)
    log.info("answer stop_reason=%s output_tokens=%s cache_read=%s",
             getattr(r, "stop_reason", None), getattr(u, "output_tokens", None),
             getattr(u, "cache_read_input_tokens", None))
    return extract_text(r), round(time.time() - t0, 2)
