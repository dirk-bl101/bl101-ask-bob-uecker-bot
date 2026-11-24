# Ask Bob Uecker (BL101 Slack bot)

Answers retail teammates' store questions from approved documents in `manuals/`.

Files: `app.py` (Slack), `core.py` (logic), `engine.py` (Claude calls), `prompt.md` (instructions), `topics.json` (topic to file map), `manuals/` (documents), `test_logic.py` (offline tests), `test_questions.md` (questions for HR/store review).

Render settings: Python 3, Build `pip install -r requirements.txt`, Start `python app.py`, Starter instance.
Environment variables: `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.
Optional: `ESCALATION_CONTACT` (text the bot gives when it cannot answer, for example "Email Amanda or ask in the Retail CX channel"). `PYTHON_VERSION=3.12.3` if the build fails.

On start the log shows a line beginning `manuals loaded:` listing every document. To update a document, replace its file in `manuals/` and commit.
To add a document, add the file to `manuals/` and list it in `topics.json`.
The log holds topic names and timing only, never questions, answers or names.
