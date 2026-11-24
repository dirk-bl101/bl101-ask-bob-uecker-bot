"""core.py - everything that needs neither Slack nor Claude.

Loads the documents, picks topics, builds the prompts, formats replies and
writes the minimal log. The Claude calls are passed in (see app.py), so this
file can be tested offline.
"""
import json
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
MANUALS = os.path.join(BASE, "manuals")
log = logging.getLogger("ask-bob-uecker")

DEFAULT_CONTACT = (
    "Ask your Store Manager, who can bring in your Director of Retail "
    "(Slack or the Retail CX channel)."
)


def load_topics(path=None):
    with open(path or os.path.join(BASE, "topics.json"), encoding="utf-8") as f:
        return json.load(f)


def _find_files(wanted, root):
    """Find each wanted filename under root, wherever GitHub put it. Prefers a
    manuals/ folder, then the top level, then any subfolder."""
    found = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for name in files:
            if name in wanted:
                path = os.path.join(folder, name)
                rank = 0 if os.path.basename(folder) == "manuals" else (1 if folder == root else 2)
                if name not in found or rank < found[name][0]:
                    found[name] = (rank, path)
    return {n: p for n, (_, p) in found.items()}


def load_manuals(topics_cfg, folder=None):
    """Return {filename: text} for every file the topics reference."""
    wanted = {n for t in topics_cfg["topics"] for n in t["files"]}
    paths = _find_files(wanted, folder or BASE)
    docs = {}
    for name, path in paths.items():
        with open(path, encoding="utf-8") as f:
            docs[name] = f.read()
    log.info("manuals loaded: %s", sorted(docs))
    for name, path in sorted(paths.items()):
        if os.path.dirname(path) != (folder or MANUALS):
            log.warning("manual found outside manuals/: %s", os.path.relpath(path, folder or BASE))
    missing = sorted(wanted - set(docs))
    if missing:
        log.warning("manuals missing: %s (looked under %s)", missing, folder or BASE)
    return docs


def load_prompt(path=None):
    with open(path or os.path.join(BASE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


def render_prompt(template, contact=None, now=None):
    now = now or datetime.now(ZoneInfo("America/New_York"))
    contact = (contact or os.environ.get("ESCALATION_CONTACT") or DEFAULT_CONTACT).strip()
    return template.replace("{{ESCALATION_CONTACT}}", contact).replace(
        "{{TODAY}}", now.strftime("%A, %B %d, %Y")
    )


# ---------- routing ----------

def router_system(topics_cfg, docs):
    lines = [
        "You pick which BL101 document topics are needed to answer a retail teammate's question.",
        "Reply with JSON only, like {\"topics\": [\"topic-id\"]}. Pick at most "
        f"{topics_cfg.get('max_topics_per_question', 2)} topics, most relevant first.",
        "Use the whole conversation, so a follow-up stays on the topic already being discussed.",
        "If nothing fits, reply {\"topics\": []}.",
        "Topics:",
    ]
    for t in topics_cfg["topics"]:
        if any(f in docs for f in t["files"]):
            lines.append(f"- {t['id']}: {t['title']}")
    return "\n".join(lines)


def parse_router_reply(text, valid_ids, limit):
    """Pull topic ids out of the router reply. Tolerates fences and extra words."""
    m = re.search(r"\{.*\}", text or "", re.S)
    ids = []
    if m:
        try:
            data = json.loads(m.group(0))
            ids = [i for i in data.get("topics", []) if isinstance(i, str)]
        except (ValueError, AttributeError):
            ids = []
    if not ids:  # last resort: any valid id mentioned in the text
        ids = [i for i in valid_ids if i in (text or "")]
    seen, out = set(), []
    for i in ids:
        if i in valid_ids and i not in seen:
            seen.add(i)
            out.append(i)
    return out[:limit]


def keyword_topics(question, topics_cfg, docs):
    """Fallback when the router fails or finds nothing. Scores keyword hits."""
    q = question.lower()
    scored = []
    for t in topics_cfg["topics"]:
        if not any(f in docs for f in t["files"]):
            continue
        score = sum(len(k) for k in t["keywords"] if k.lower() in q)
        if score:
            scored.append((score, t["id"]))
    scored.sort(reverse=True)
    return [i for _, i in scored][: topics_cfg.get("max_topics_per_question", 2)]


def files_for(topic_ids, topics_cfg):
    by_id = {t["id"]: t for t in topics_cfg["topics"]}
    files = []
    for i in topic_ids:
        for f in by_id[i]["files"]:
            if f not in files:
                files.append(f)
    return files


# ---------- conversation ----------

def clean_user_text(text, bot_user_id=None):
    text = re.sub(r"<@[A-Z0-9]+>", "", text or "")
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def build_messages(thread, bot_user_id):
    """thread: Slack messages oldest first -> Claude messages (alternating roles).

    Keeps the last 8 turns. Messages with no text are skipped.
    """
    msgs = []
    for m in thread:
        text = clean_user_text(m.get("text", ""), bot_user_id)
        if not text:
            continue
        role = "assistant" if (m.get("user") == bot_user_id or m.get("bot_id")) else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    msgs = msgs[-8:]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def transcript(messages):
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def answer_system_blocks(prompt_text, doc_names, docs):
    """System blocks for the answer call. The documents block is cache-marked so
    repeat questions on the same topics cost much less."""
    doc_text = "\n\n".join(f"##### DOCUMENT FILE: {n}\n{docs[n]}" for n in doc_names)
    return [
        {"type": "text", "text": prompt_text},
        {
            "type": "text",
            "text": "DOCUMENTS\n\n" + doc_text,
            "cache_control": {"type": "ephemeral"},
        },
    ]


# ---------- output ----------

def to_slack(text):
    """Claude sometimes uses markdown. Convert to Slack formatting."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
    return text.strip()


def log_event(topic_ids, seconds, outcome):
    """Minimal log: topics, timing, outcome. Never the question, answer or user."""
    log.info("answered topics=%s seconds=%s outcome=%s", ",".join(topic_ids) or "none", seconds, outcome)


# ---------- the whole flow ----------

NO_TOPIC_REPLY = (
    "I don't have that in my documents yet. {contact}"
)
ERROR_REPLY = (
    "Sorry, something went wrong on my end. Please try again in a minute. "
    "If it keeps happening, tell your Store Manager."
)


def respond(thread, bot_user_id, ctx, route_fn, answer_fn):
    """ctx = {topics, docs, prompt}. route_fn(system, messages)->str,
    answer_fn(system_blocks, messages)->(str, seconds). Returns the Slack reply."""
    topics_cfg, docs = ctx["topics"], ctx["docs"]
    messages = build_messages(thread, bot_user_id)
    if not messages:
        return "Ask me a store question and I'll look it up in the BL101 documents."
    valid = [t["id"] for t in topics_cfg["topics"] if any(f in docs for f in t["files"])]
    limit = topics_cfg.get("max_topics_per_question", 2)

    topic_ids = []
    try:
        reply = route_fn(router_system(topics_cfg, docs), [{"role": "user", "content": transcript(messages)}])
        topic_ids = parse_router_reply(reply, valid, limit)
    except Exception:
        log.exception("router failed, using keyword fallback")
    if not topic_ids:
        topic_ids = keyword_topics(transcript(messages), topics_cfg, docs)

    prompt_text = render_prompt(ctx["prompt"])
    if not topic_ids:
        log_event([], 0, "no_topic")
        contact = render_prompt("{{ESCALATION_CONTACT}}")
        return NO_TOPIC_REPLY.format(contact=contact)

    names = files_for(topic_ids, topics_cfg)
    text, secs = answer_fn(answer_system_blocks(prompt_text, names, docs), messages)
    if not text:
        log_event(topic_ids, secs, "empty")
        return ERROR_REPLY
    log_event(topic_ids, secs, "ok")
    return to_slack(text)
