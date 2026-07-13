# MiniEval for Slack — Setup & Run Guide

MiniEval checks whether an AI-generated Slack summary is actually faithful to
the conversation it's supposed to summarize, using a local NLI (Natural
Language Inference) model. It's invoked on demand — via a message shortcut or
a slash command — because Slack's native "Summarize" feature renders privately
to the user who clicked it and is never posted into the channel, so there's no
message event to listen for.

This guide covers: prerequisites, `.env` setup, creating the Slack app from
the manifest, running the agent, testing all three entry points, and testing
the standalone MCP server with the MCP Inspector.

---

## 1. Prerequisites

- **Python 3.10+** (the project was built/tested against Python 3.14)
- **Node.js + npm** (only needed for `npx @modelcontextprotocol/inspector` in
  step 7 — not needed to run the Slack agent itself)
- A Slack workspace where you can install apps (owner/admin, or an workspace
  where app installs by members are allowed)
- ~500MB free disk for the NLI model weights (`cross-encoder/nli-deberta-v3-small`),
  downloaded automatically on first run and cached by Hugging Face afterward

Install Python dependencies from the project root:

```powershell
cd slack_minieval
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 2. Configure `.env`

Create `slack_minieval\.env` (same folder as `app_manifest.yml`) with:

```ini
# Required — from the Slack app you create in step 3
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...

# Optional — sensible defaults already baked into config.py
NLI_MODEL=cross-encoder/nli-deberta-v3-small
HALLUCINATION_THRESHOLD=0.40
VERIFIED_THRESHOLD=0.70
DB_PATH=uploads/minieval.db
LOG_LEVEL=INFO
CHANNEL_HISTORY_FALLBACK_LIMIT=200
```

`config.py` raises immediately on import if `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`,
or `SLACK_SIGNING_SECRET` are missing — everything else has a working default.

---

## 3. Create the Slack app from the manifest

1. Go to **[api.slack.com/apps](https://api.slack.com/apps)** → **Create New App** → **From an app manifest**.
2. Pick your workspace, then paste the contents of `slack_minieval\app_manifest.yml`.
3. Review and click **Create**.

The manifest already declares everything the app needs:

| Feature | Purpose |
|---|---|
| Message shortcut `check_summary_shortcut` | "Check this summary with MiniEval" — right-click any message |
| Slash command `/minieval-check` | Opens the same check modal, blank (for channel-level recaps) |
| Slash command `/minieval-stats` | Posts the channel's trust score / hallucination rate |
| Socket Mode + interactivity enabled | No public Request URL needed — everything tunnels over the websocket `app.py` opens |

**Bot token scopes** (all in the manifest — nothing to add manually unless you
edited the manifest yourself):

- `commands` — slash commands
- `chat:write` — post warning / verified / stats messages
- `channels:history`, `groups:history`, `im:history` — read the original
  conversation MiniEval scores the summary against
- `channels:read`, `groups:read` — resolve channel names for the modal's
  channel picker
- `canvases:write` — create/update the per-channel MiniEval Canvas dashboard
  (`canvas_manager.py`). **This scope was missing from earlier versions of the
  manifest** — if you're re-pasting an old manifest into an *existing* app
  instead of creating a new one, double check this scope is present under
  **OAuth & Permissions**, or Canvas creation will fail with `missing_scope`.

## 4. Install the app and collect tokens

1. **OAuth & Permissions** → **Install to Workspace** → **Allow**.
2. Copy the **Bot User OAuth Token** (`xoxb-...`) → `SLACK_BOT_TOKEN`.
3. **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**
   → add scope `connections:write` → **Generate**. Copy the `xapp-...` token
   → `SLACK_APP_TOKEN`.
4. **Basic Information** → **App Credentials** → copy **Signing Secret** →
   `SLACK_SIGNING_SECRET`.
5. Confirm **Socket Mode** is toggled on under **Socket Mode** in the sidebar
   (the manifest sets `socket_mode_enabled: true`, but it's worth eyeballing).

If you add `canvases:write` (or any scope) to an app that's already installed,
Slack won't pick it up until you reinstall: **OAuth & Permissions** → **Reinstall to Workspace**.

## 5. Invite the bot to a channel

In Slack, open the channel(s) you want to test in and run:

```
/invite @MiniEval
```

(The bot needs to be a channel member to read history via
`conversations_history` / `conversations_replies` and to post messages.)

---

## 6. Run the agent

From `slack_minieval/src` (the imports in `app.py` are bare — e.g. `import config`
— so it must be run with `src/` as the working directory, not the project root):

```powershell
cd slack_minieval\src
python app.py
```

Expected output:

```
INFO - Loading NLI model: cross-encoder/nli-deberta-v3-small
INFO - NLI model loaded.
INFO - SlackEventHandler initialized with registered handlers
INFO - MiniEval for Slack is ready!
```

First run downloads the model weights (a minute or two depending on
connection); subsequent runs load from the local Hugging Face cache in a few
seconds.

---

## 7. Test the live flow

### a) Message shortcut (Entry Point 1)
1. Post or find a message containing an AI-style summary in a channel the bot is in.
2. Click **⋯** on the message → **Check this summary with MiniEval**.
3. A modal opens pre-filled with that message's text. Confirm/select the
   source channel, optionally paste a specific thread permalink, click **Evaluate**.
4. Within a few seconds you should see either:
   - ⚠️ a red **"Heads up — this AI summary may not be accurate"** card (score < 40%), or
   - ✅ a green **"MiniEval verified"** card (score ≥ 70%), or
   - nothing posted + an ephemeral note if the score landed in the 40–70% uncertain band.
5. Open the channel's **Canvas** tab — it should now show (or have just
   created) the **🛡️ MiniEval — AI Trust Dashboard**, updated with this
   evaluation's numbers.

### b) Slash command — `/minieval-check`
Run `/minieval-check` in any channel the bot is in. Same modal, opened blank —
useful for checking a channel-level recap that isn't a single message.

### c) Slash command — `/minieval-stats`
Run `/minieval-stats` in a channel that already has evaluations logged. Posts
a Block Kit card with trust score, hallucination rate, and total evaluations
for that channel over the last 30 days.

---

## 8. Test the MCP server with the MCP Inspector

`mcp_server.py` is a **separate, standalone process** — it is not in the live
Slack hot path (the live path calls `evaluator_bridge.evaluate()` directly).
It exists so an external MCP client (Claude Desktop, the MCP Inspector, etc.)
can call MiniEval's evaluation engine directly.

From `slack_minieval/src`:

```powershell
cd slack_minieval\src
npx @modelcontextprotocol/inspector python mcp_server.py
```

This opens the Inspector in your browser, connected over stdio to
`mcp_server.py`. From there you can call:

- **`evaluate_summary(summary, context)`** → faithfulness score, label, NLI sub-scores
- **`get_channel_stats(channel, days=30)`** → hallucination rate / trust score
- **`get_recent_evaluations(limit=10)`** → recently logged evaluations across all channels

> **Note:** `scripts/run_mcp_server.py` is a stale script from an earlier,
> class-based MCP implementation (`MiniEvalMCPServer`, `MiniEvalAdapter`) that
> no longer exists in the codebase — running it will raise an `ImportError`.
> Use the Inspector command above instead; it talks to the current
> `mcp_server.py` (built on the real `mcp` SDK, stdio transport).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `KeyError` / crash on `import config` | Missing `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, or `SLACK_SIGNING_SECRET` in `.env` |
| Shortcut/slash command does nothing in Slack | `python app.py` isn't running, or Socket Mode / App-Level Token is misconfigured |
| Modal opens but "Evaluate" hangs or silently does nothing | Check the terminal running `app.py` for a traceback — evaluation runs in a background thread after `ack()`, so errors won't surface in Slack directly |
| Canvas doesn't appear / `missing_scope` in logs | `canvases:write` scope not installed — reinstall the app after confirming the scope is present (step 4) |
| `/minieval-stats` returns all zeros right after a successful check | `DB_PATH` mismatch between processes — confirm you're not running two copies of the app with different working directories/`.env` files |
| First evaluation takes several seconds, later ones are instant | Expected — the NLI model is loaded once (`lru_cache`) at first use, not per request |
| `ModuleNotFoundError: mcp` | `pip install -r requirements.txt` didn't pick up the `mcp` SDK — check you're in the venv you installed into |

## Known limitations (don't re-investigate these)

- No passive message listener by design — see the top of this doc.
- The NLI model returns generic `LABEL_0/1/2` labels on some checkpoints;
  `evaluator_bridge.py` handles this, but the label-position mapping's offline
  accuracy against the HaluEval **QA** subset is a known open issue (40.9%,
  below chance). This does **not** affect the live Slack demo or the
  hand-built `demo/example_summaries.json` set, which currently scores 90%
  accuracy — it's specific to that one external dataset's evaluation script.
