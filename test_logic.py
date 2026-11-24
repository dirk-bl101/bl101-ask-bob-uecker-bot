"""Offline tests. They need neither Slack nor Claude. Run: python3 -m unittest -v test_logic"""
import json
import logging
import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import core

TOPICS = core.load_topics()
DOCS = core.load_manuals(TOPICS)
BOT = "UBOT"


class Manuals(unittest.TestCase):
    def test_every_topic_file_exists(self):
        for t in TOPICS["topics"]:
            for f in t["files"]:
                self.assertIn(f, DOCS, f"{f} missing from manuals/")

    def test_every_manual_is_used_by_a_topic(self):
        used = {f for t in TOPICS["topics"] for f in t["files"]}
        self.assertEqual(used, set(os.listdir(core.MANUALS)))

    def test_no_held_back_files(self):
        for name in os.listdir(core.MANUALS):
            self.assertNotIn("draft", name.lower())
            self.assertNotIn("contacts", name.lower())
            self.assertNotIn("hiring", name.lower())

    def test_no_stray_phone_numbers_except_cx_line(self):
        import re
        for name, text in DOCS.items():
            for m in re.findall(r"\(?\d{3}\)?[ -]\d{3}[ -]\d{4}", text):
                self.assertEqual((name, m), ("bfcm-cx-info-sheet.txt", "631-649-7133"))


class Routing(unittest.TestCase):
    valid = [t["id"] for t in TOPICS["topics"]]

    def test_plain_json(self):
        self.assertEqual(core.parse_router_reply('{"topics": ["bfcm-customer-rules"]}', self.valid, 2), ["bfcm-customer-rules"])

    def test_fenced_and_chatty(self):
        txt = 'Sure!\n```json\n{"topics": ["store-operations", "guest-experience", "holiday-calendar"]}\n```'
        self.assertEqual(core.parse_router_reply(txt, self.valid, 2), ["store-operations", "guest-experience"])

    def test_invalid_and_duplicate_ids_dropped(self):
        txt = '{"topics": ["nope", "letters-to-santa", "letters-to-santa"]}'
        self.assertEqual(core.parse_router_reply(txt, self.valid, 2), ["letters-to-santa"])

    def test_garbage_gives_empty(self):
        self.assertEqual(core.parse_router_reply("I cannot help", self.valid, 2), [])
        self.assertEqual(core.parse_router_reply("", self.valid, 2), [])
        self.assertEqual(core.parse_router_reply(None, self.valid, 2), [])

    def test_id_mentioned_in_prose(self):
        self.assertEqual(core.parse_router_reply("use letters-to-santa", self.valid, 2), ["letters-to-santa"])

    def test_keyword_fallback(self):
        self.assertEqual(core.keyword_topics("Will we price match a customer?", TOPICS, DOCS)[0], "bfcm-customer-rules")
        self.assertEqual(core.keyword_topics("what do I do with the Santa mailbox", TOPICS, DOCS)[0], "letters-to-santa")
        self.assertEqual(core.keyword_topics("zzz qqq", TOPICS, DOCS), [])

    def test_files_for_dedupes(self):
        self.assertEqual(core.files_for(["signing-youth", "signing-youth"], TOPICS), ["signing-cheat-sheet-intro-and-youth.txt"])


class Conversation(unittest.TestCase):
    def test_strips_mentions_and_alternates(self):
        thread = [
            {"user": "U1", "text": "<@UBOT> can we price match?"},
            {"user": "UBOT", "text": "No. Source: CX sheet"},
            {"user": "U1", "text": "<@UBOT> what about online?"},
            {"user": "U1", "text": "sorry, online sale"},
        ]
        m = core.build_messages(thread, BOT)
        self.assertEqual([x["role"] for x in m], ["user", "assistant", "user"])
        self.assertEqual(m[0]["content"], "can we price match?")
        self.assertIn("online sale", m[2]["content"])

    def test_drops_leading_assistant_and_empty(self):
        thread = [{"user": "UBOT", "text": "hi"}, {"user": "U1", "text": "  "}, {"user": "U1", "text": "hello"}]
        m = core.build_messages(thread, BOT)
        self.assertEqual(m, [{"role": "user", "content": "hello"}])

    def test_bot_id_messages_are_assistant(self):
        m = core.build_messages([{"user": "U1", "text": "q"}, {"bot_id": "B1", "text": "a"}], BOT)
        self.assertEqual(m[1]["role"], "assistant")

    def test_truncates_to_recent_and_starts_with_user(self):
        thread = []
        for i in range(10):
            thread.append({"user": "U1", "text": f"q{i}"})
            thread.append({"user": "UBOT", "text": f"a{i}"})
        m = core.build_messages(thread, BOT)
        self.assertLessEqual(len(m), 8)
        self.assertEqual(m[0]["role"], "user")

    def test_slack_link_cleanup(self):
        self.assertEqual(core.clean_user_text("see <https://x.com|the page> &amp; <@U123> go"), "see the page & go")


class Output(unittest.TestCase):
    def test_to_slack(self):
        self.assertEqual(core.to_slack("**Bold** and\n## Head\n[l](https://a.com)"), "*Bold* and\n*Head*\nl (https://a.com)")

    def test_prompt_placeholders_filled(self):
        now = datetime(2026, 11, 24, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        p = core.render_prompt(core.load_prompt(), contact="Call Pat", now=now)
        self.assertNotIn("{{", p)
        self.assertIn("Call Pat", p)
        self.assertIn("Tuesday, November 24, 2026", p)

    def test_default_contact_used(self):
        os.environ.pop("ESCALATION_CONTACT", None)
        self.assertIn("Store Manager", core.render_prompt(core.load_prompt()))

    def test_documents_block_is_cached(self):
        blocks = core.answer_system_blocks("PROMPT", ["store-operations.txt"], DOCS)
        self.assertEqual(blocks[0]["text"], "PROMPT")
        self.assertIn("cache_control", blocks[1])
        self.assertIn("store-operations.txt", blocks[1]["text"])


class EngineParsing(unittest.TestCase):
    def test_extract_text_shapes(self):
        try:
            import engine
        except ImportError:
            self.skipTest("anthropic not installed here")
        T = lambda t: type("B", (), {"type": "text", "text": t})()
        N = type("B", (), {"type": "tool_use"})()
        R = lambda c: type("R", (), {"content": c})()
        self.assertEqual(engine.extract_text(R([T("a"), T("b")])), "a\nb")
        self.assertEqual(engine.extract_text(R([N, T("x")])), "x")
        self.assertEqual(engine.extract_text(R([])), "")
        self.assertEqual(engine.extract_text(R(None)), "")


class Flow(unittest.TestCase):
    ctx = {"topics": TOPICS, "docs": DOCS, "prompt": core.load_prompt()}
    thread = [{"user": "U1", "text": "<@UBOT> will we price match?"}]

    def test_happy_path_sends_only_routed_docs(self):
        seen = {}
        def route(system, msgs): return '{"topics": ["bfcm-customer-rules"]}'
        def answer(blocks, msgs):
            seen["blocks"] = blocks; return ("**No.** Source: CX sheet", 1.2)
        out = core.respond(self.thread, BOT, self.ctx, route, answer)
        self.assertEqual(out, "*No.* Source: CX sheet")
        self.assertIn("bfcm-cx-info-sheet.txt", seen["blocks"][1]["text"])
        self.assertNotIn("signing-cheat-sheet", seen["blocks"][1]["text"])

    def test_router_failure_uses_keywords(self):
        def route(system, msgs): raise RuntimeError("down")
        got = {}
        def answer(blocks, msgs): got["ok"] = True; return ("fine", 0.1)
        self.assertEqual(core.respond(self.thread, BOT, self.ctx, route, answer), "fine")
        self.assertTrue(got["ok"])

    def test_no_topic_gives_contact_and_skips_answer_call(self):
        def route(system, msgs): return '{"topics": []}'
        def answer(blocks, msgs): raise AssertionError("must not call Claude")
        out = core.respond([{"user": "U1", "text": "zzz qqq"}], BOT, self.ctx, route, answer)
        self.assertIn("don't have that", out)

    def test_empty_answer_gives_error_reply(self):
        out = core.respond(self.thread, BOT, self.ctx, lambda s, m: '{"topics":["bfcm-customer-rules"]}', lambda b, m: ("", 0.1))
        self.assertEqual(out, core.ERROR_REPLY)

    def test_empty_thread(self):
        out = core.respond([{"user": "U1", "text": "<@UBOT>"}], BOT, self.ctx, None, None)
        self.assertIn("Ask me a store question", out)

    def test_log_never_contains_question_or_user(self):
        secret = "SECRETQUESTION-Jordan-pay-rate"
        with self.assertLogs("ask-bob-uecker", level="INFO") as cm:
            core.respond([{"user": "U9ABC", "text": secret}], BOT, self.ctx,
                         lambda s, m: '{"topics":["bfcm-customer-rules"]}', lambda b, m: ("ok answer text", 0.3))
        joined = "\n".join(cm.output)
        self.assertNotIn(secret, joined)
        self.assertNotIn("U9ABC", joined)
        self.assertNotIn("ok answer text", joined)
        self.assertIn("topics=bfcm-customer-rules", joined)


if __name__ == "__main__":
    unittest.main()
