# Using Grounded Coach — A Tester's Guide

This guide walks a first-time user through installing, configuring, running, and
actually using the app. No prior knowledge of the codebase required.

> **What the app does, in one line:** while you talk, it listens to your
> microphone and shows short, written coaching suggestions backed by your Azure
> AI Search knowledge base — without recording or storing your audio.

---

## Before you start — what works today

Please read this so expectations are set:

| Capability | Status |
|------------|--------|
| App installs and runs locally | ✅ Works |
| Connects to Azure OpenAI Realtime (mic → model) | ✅ Works (needs Azure access, below) |
| Microphone capture | ✅ Works in a supported browser |
| Optional tab/system audio | ⚠️ Works where the browser allows it (see [limits](#optional-tabsystem-audio)) |
| **Grounded suggestions with citations** | ⚠️ **Needs a Search index to be created first** — see [Grounding setup](#enabling-grounded-suggestions) |
| Spoken responses | ⚙️ Off by default; opt-in |

If no Search index is configured yet, the app still runs and connects, but the
coach will say it has **no grounded information** rather than giving advice.
That's expected until the index step is done.

---

## 1. Prerequisites

* **Python 3.12+**
* **[uv](https://docs.astral.sh/uv/)** (recommended) or `pip`
* A **Chromium-based browser** (Chrome or Edge) — best WebRTC + audio support
* **Access to the Azure resources** (ask the project owner for either an API key
  or permission to sign in with `az login`):
  * Azure OpenAI `shhchat` with the `gpt-realtime-2.1` deployment
  * Azure AI Search `secondchat`

Check Python and uv:

```bash
python --version      # should be 3.12 or higher
uv --version          # or: pip --version
```

---

## 2. Get the code

```bash
git clone git@github.com:abhisheksinghcs/grounded-coach.git
cd grounded-coach
```

---

## 3. Install

```bash
uv sync --extra dev
```

<details>
<summary>Prefer pip?</summary>

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```
</details>

---

## 4. Configure credentials

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

Open `.env` and set **one** of the two auth options:

**Option A — API key (simplest for a tester):**
```bash
AZURE_OPENAI_API_KEY=<the key the owner gave you>
```

**Option B — Entra sign-in (no key to handle):** leave `AZURE_OPENAI_API_KEY`
blank and run:
```bash
az login
```

That's enough to **connect and talk to the model**. Grounded suggestions need
one more step (next section), which you can skip for a first smoke test.

> 🔒 Your `.env` is gitignored — never commit it or share the key in chat.

---

## 5. Run it

```bash
uv run coach
```

You should see it start on **http://127.0.0.1:8000**. Open that in Chrome/Edge.

---

## 6. Use the app

1. Make sure **Microphone** is checked.
2. Click **Start**.
3. Your browser will ask for **microphone permission** — click **Allow**.
   (If you deny it, the app refuses to connect — it never fakes capture.)
4. The status should move through `negotiating session… → connecting to Azure…
   → connected`, and the log panel shows "Microphone captured" and "Data channel
   open".
5. **Start talking** as if you're in a conversation. When you pause, the coach
   processes your turn.
6. Suggestions appear as cards on the right. If grounding is configured, each
   card is backed by a **Grounding sources** list whose `[id]`s match the
   citations in the suggestion.
7. Click **Stop** when done — this releases the microphone immediately.

### What you'll see without a Search index

The coach will respond that it **doesn't have grounded information**. That is the
correct, by-design behavior (it won't make things up). Connecting and this
"no grounding" message still confirm the realtime pipeline works end to end.

---

## Enabling grounded suggestions

Grounded advice requires an **Azure AI Search index** with your reference
content. This is a one-time setup by whoever owns the data.

1. Create an index on the `secondchat` Search service and load documents into it.
2. In `.env`, set the index name and map your index's real field names:
   ```bash
   AZURE_SEARCH_API_KEY=<search key>     # or use az login
   AZURE_SEARCH_INDEX=<your-index-name>
   AZURE_SEARCH_ID_FIELD=<id field>
   AZURE_SEARCH_CONTENT_FIELD=<main text field>
   AZURE_SEARCH_TITLE_FIELD=<title field>
   AZURE_SEARCH_URL_FIELD=<url field>
   ```
3. Verify retrieval **without any audio** using the built-in CLI:
   ```bash
   uv run python -m coach.retrieval "a question your docs should answer"
   ```
   You should see matching passages with their ids. If you do, the coach will
   use them.

Keyword search works immediately. (Hybrid/vector search is an advanced opt-in
that also needs a vectorizer on the index — leave it off unless the owner has
set that up.)

---

## Optional tab/system audio

You can coach on audio coming from *another tab or app* (e.g. a video call in a
browser tab), not just your mic.

1. Check **Computer / tab audio** before clicking Start.
2. In the browser's share dialog, pick the source **and enable "Share tab
   audio" / "Share system audio."**

**Important limits (not a bug):**
* **Screen video is never uploaded** — only the audio is used.
* Support depends on your OS/browser:
  * **macOS (Chrome/Edge):** you can capture audio from a **shared browser tab**;
    full desktop/system audio generally isn't available.
  * **Windows (Chrome/Edge):** "Share system audio" is additionally available.
  * Safari/Firefox: limited.
* If no audio is actually shared, the app refuses that source and releases it.

---

## Privacy notes to share with testers

* Your **audio goes directly from your browser to Azure** — it does not pass
  through or get stored by the local server.
* **Nothing is recorded or persisted** by default (no audio, no transcripts).
* The app **never uploads screen video**.
* Stopping or closing the tab **releases the microphone** right away.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| "No audio source selected/available" | The Microphone box was unchecked, or you denied the mic permission. Re-check it and Allow. |
| Status stuck at "negotiating session…" / 401 error in log | Auth problem. Check `AZURE_OPENAI_API_KEY` in `.env`, or run `az login`. |
| Connects but coach always says "no grounded information" | No Search index configured yet — see [Enabling grounded suggestions](#enabling-grounded-suggestions). |
| `python -m coach.retrieval` prints an index error | `AZURE_SEARCH_INDEX` is empty or wrong, or the Search key/login is missing. |
| Tab audio option does nothing | Your OS/browser doesn't support it, or you didn't enable "Share audio" in the dialog. See [limits](#optional-tabsystem-audio). |
| Microphone LED stays on after Stop | Shouldn't happen — Stop releases tracks. Refresh the tab if needed and report it. |
| Nothing happens when I speak | Speak a full sentence then pause; the coach acts on a completed turn. Check the log panel for events. |

---

## Giving feedback

When reporting an issue, please include:
* Your **OS and browser** (e.g. macOS 15 / Chrome 142).
* Whether you used an **API key** or **`az login`**.
* Whether a **Search index** was configured.
* The relevant lines from the **log panel** in the UI.
* What you expected vs what happened.

For how the app works internally, see [architecture.md](./architecture.md) and
[code-walkthrough.md](./code-walkthrough.md).
