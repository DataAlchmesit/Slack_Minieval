# 🛡️ MiniEval — AI Trust Layer for Slack

![Python](https://img.shields.io/badge/Python-3.11-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Slack](https://img.shields.io/badge/Slack-Bolt-611f69) ![MCP](https://img.shields.io/badge/MCP-Server-orange)

**Know if your Slack AI is lying to you - before you forward it to your boss.**

MiniEval doesn't add another AI to your Slack. **It audits the AI already there.** Right-click any AI summary and it scores how faithfully the summary matches the original conversation - then posts a ⚠️ warning or ✅ verified card right in the thread.

---

## The Problem Nobody Is Talking About

Slack AI summarizes your threads and channels with one click. Your team trusts those summaries. But here's what's actually happening:

| Original Thread (Ground Truth) | Slack AI Summary | Faithful? |
|--------------------------------|------------------|:---------:|
| `The new policy is a draft, open for feedback until Friday. No decisions finalized.` | *"The new policy is approved and effective immediately."* | ❌ |
| `Acme wants SSO before they'll sign. They want it in 2 weeks or they walk — no promises yet.` | *"Acme has agreed to sign; SSO will be delivered in 2 weeks."* | ❌ |
| `Redis maxed out. Bumped max_connections 20→50, testing now. Might be a leak, not confirmed.` | *"Resolved by raising max_connections to 100, fully fixing the leak."* | ❌ |
| `Open enrollment closes Friday 5pm via the HR portal, no extensions this cycle.` | *"Open enrollment closes Friday at 5pm via the HR portal, no extensions."* | ✅ |

The AI sounds **completely confident** when it's completely wrong. In a fast-moving workspace, that's not a UX problem — it's a bad decision waiting to happen. A draft read as "approved." A contested deal read as "closed." A wrong number forwarded up the chain.

**There is no quality-control layer for AI summaries inside Slack. MiniEval is that layer.**

---

## The Solution

MiniEval is a lightweight NLI (Natural Language Inference) evaluation engine that sits between Slack's AI and the people who read its summaries. It scores every summary for faithfulness to the original conversation, then acts on that score — right inside Slack. No cloud, no GPT judge, no per-call billing. The engine runs locally and **no message content ever leaves your workspace.**

```
User right-clicks an AI summary        OR runs /minieval-check
            │
            ▼
    MiniEval Check modal opens
            │
            ▼
    Fetches the original thread / source content
            │
            ▼
    MiniEval Core (NLI)  ──►  Faithfulness Score   0.0 ────── 1.0
            │                       │                    │
            │                  score < 0.40         score ≥ 0.70
            ▼                  ⚠️ WARNING            ✅ VERIFIED
    Canvas Dashboard          posted in thread      posted in thread
    (live trust score)
            │
            ▼
    MCP Server  ──►  evaluate_summary() callable by any MCP client
```

---

## Architecture

```
┌────────────────────────── Slack Workspace ──────────────────────────┐
│                                                                     │
│  User → Message Shortcut / /minieval-check                          │
│                    │                                                 │
│                    ▼                                                 │
│           slack_event_handler.py   (Bolt app, Socket Mode)          │
│                    │                                                 │
│                    ▼                                                 │
│               pipeline.py          (orchestrates evaluate→store→post)│
│           ┌────────┼────────┐                                        │
│           ▼        ▼        ▼                                        │
│      evaluator_ storage.py  slack_notifier.py                       │
│      bridge.py  (SQLite)    (Block Kit cards)                       │
│      (NLI model)                                                     │
│                    │                                                 │
│                    ▼                                                 │
│           canvas_manager.py        (live Canvas dashboard)          │
└─────────────────────────────────────────────────────────────────────┘

┌────────────── MCP Server (standalone process) ──────────────┐
│  mcp_server.py  →  evaluate_summary                          │
│  (stdio)           get_channel_stats                         │
│                    get_recent_evaluations                    │
│  Callable by MCP Inspector, Claude Desktop, any MCP host    │
└─────────────────────────────────────────────────────────────┘
```
<img width="2771" height="2579" alt="architecture" src="https://github.com/user-attachments/assets/e165e55b-35e3-45a0-917d-aad25dfd9635" />

---

## Quick Start

```bash
# 1. Install
git clone https://github.com/DataAlchmesit/slack_minieval
cd slack_minieval
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET

# 3. Apply the Slack app manifest
# Paste app_manifest.yml into your app's App Manifest page at api.slack.com/apps
# Save, then reinstall the app when prompted

# 4. Run
python app.py
```

Then, in any channel where MiniEval is invited:
- **Right-click** any message → *Check this summary with MiniEval*, or
- Run **`/minieval-check`** and paste a summary to check, or
- Run **`/minieval-stats`** for the channel's trust report

Full walkthrough in [SETUP_AND_RUN.md](SETUP_AND_RUN.md).

---

## It Works (See For Yourself)

**Catches a hallucination — posted right in the thread:**

![Warning card]

**Verifies a faithful summary:**

![Verified card](demo/screenshots/verified-card.png <img width="749" height="423" alt="minieval-warning-card" src="https://github.com/user-attachments/assets/23d865d3-524f-4d3c-adad-0e5181ce5a60" />
)

**Live Canvas trust dashboard :**

🛡️ MiniEval — AI Trust Dashboard

Workspace AI Trust Score: 100% 🟢 Good

📊 This Week at a Glance
Metric              Value
Total evaluations   1
Hallucination rate  0.0%
Faithful summaries  100.0%
Period              Last 7 days

📋 Channel Breakdown
Channel              Evaluations   Hallucination Rate   Trust Score
<#C0BGF4E132T>       1             0.0%                 🟢 100%

🚀 How to Use MiniEval
Check any AI summary in 3 steps:
1. Right-click any message containing a summary
2. Select 'Check this summary with MiniEval' from the menu
3. Confirm the channel and hit Evaluate

Or use the slash command:
/minieval-check

Check your channel's trust score:
/minieval-stats

🧠 How It Works
MiniEval uses NLI (Natural Language Inference) to score how faithfully an AI summary represents its source content.

Score      Verdict         Action
70%+       ✅ Faithful     Verified card posted
40–70%     ⚪ Uncertain    No action (inconclusive)
Under 40%  ⚠️ Hallucinated Warning posted in thread

** Channel (MiniEval) stat :**

<img width="830" height="549" alt="minieval_stats" src="https://github.com/user-attachments/assets/791c86c6-5c63-4cf8-a9a1-02e5eb83c261" />

---

## Results

MiniEval's engine was validated offline against a labelled set before going live:

- **Correctly classified 9 of 10** hand-labelled faithful/hallucinated pairs
- **Sub-200ms** average inference latency, **on CPU** — no GPU required
- **~140M-parameter** model (`cross-encoder/nli-deberta-v3-small`), runs fully local
- Every number is reproducible — see [`scripts/generate_demo_stats.py`](scripts/generate_demo_stats.py), which runs the real engine against any labelled dataset and reports accuracy, not fabricated figures

> Note: the offline set is a balanced validation set for measuring the *evaluator's* accuracy, not a claim about how often real Slack summaries hallucinate. MiniEval reports only what it actually measures.

---

## MCP Integration

MiniEval's evaluation engine is exposed as a **real, spec-compliant Model Context Protocol server** — not internal plumbing, but an independently connectable server any MCP client can call.

```bash
npx @modelcontextprotocol/inspector python src/mcp_server.py
```

| Tool | Description |
|------|-------------|
| `evaluate_summary` | Score faithfulness of any (source, summary) pair |
| `get_channel_stats` | Hallucination rate + trust score for a channel |
| `get_recent_evaluations` | Recent evaluation history |

This means the same faithfulness check that runs in Slack is callable from Claude Desktop, Cursor, or any MCP host — so MiniEval's trust layer works for **any** AI output, not just Slack summaries.

<img width="957" height="259" alt="Screenshot 2026-07-13 182904" src="https://github.com/user-attachments/assets/d6c0b620-dd97-466b-87ff-b3178807ec03" />

---

## Repository Structure

```
slack_minieval/
├── app.py                  # Entry point
├── config.py               # Environment config + thresholds
├── evaluator_bridge.py     # NLI faithfulness scorer
├── mcp_server.py           # Spec-compliant MCP server (stdio)
├── pipeline.py             # Evaluation orchestrator
├── slack_event_handler.py  # Bolt app: shortcuts, commands, modal
├── slack_notifier.py       # Block Kit warning/verified cards
├── canvas_manager.py       # Live Canvas dashboard
├── modals.py               # Check modal builder
├── slack_utils.py          # Permalink parser, helpers
├── storage.py              # SQLite persistence
├── scripts/
│   ├── generate_demo_stats.py  # Reproducible batch evaluation
│   └── analyze_confusion.py    # Result diagnostics
├── demo/example_summaries.json # Labelled validation pairs
├── app_manifest.yml        # Slack app configuration
├── architecture.png
├── SETUP_AND_RUN.md
└── requirements.txt
```

---

## Data Model

MiniEval logs each evaluation to a local SQLite database:

| Field | Description |
|-------|-------------|
| `channel` / `thread_ts` | Where the summary was checked |
| `score` | Faithfulness score, 0.0–1.0 |
| `label` | FAITHFUL / UNCERTAIN / HALLUCINATED |
| `is_hallucination` | Boolean flag against the configured threshold |
| `timestamp` | When the check ran |

Only scores and metadata are stored — **raw message content is not retained** beyond the moment of evaluation.

---

## Key Dependencies

- **[slack-bolt](https://github.com/slackapi/bolt-python)** - event handling, Socket Mode, Block Kit, Canvas
- **[mcp](https://github.com/modelcontextprotocol/python-sdk)** - Model Context Protocol server
- **[transformers](https://github.com/huggingface/transformers)** + **[cross-encoder/nli-deberta-v3-small](https://huggingface.co/cross-encoder/nli-deberta-v3-small)** - NLI faithfulness scoring
- **SQLite** - local evaluation logging

---

## Links

- **Demo video:** _(https://youtu.be/X7yedpuhyqg)_
- **Git Hub Link:** _(https://github.com/DataAlchmesit/Slack_Minieval)_
- **Setup guide:** [SETUP_AND_RUN.md](SETUP_AND_RUN.md)

---

*MiniEval doesn't add another AI to your Slack workspace. It makes the AI already there trustworthy.*
