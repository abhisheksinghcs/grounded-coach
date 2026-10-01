# Grounded Coach — Documentation

Design rationale and a code walkthrough so you can understand *why* the app is
built the way it is, not just *what* it does.

| Doc | What it covers |
|-----|----------------|
| [usage.md](./usage.md) | **Start here if you just want to run and test the app** — install, configure, run, and use it, with troubleshooting. Written for non-developers/testers. |
| [architecture.md](./architecture.md) | The big picture: the two-channel design, why the backend stays off the audio path, the GA protocol decisions, and the trade-offs behind each major choice. |
| [code-walkthrough.md](./code-walkthrough.md) | A file-by-file tour of the code, following one grounded coaching turn end to end. |
| [grounding-and-safety.md](./grounding-and-safety.md) | How retrieval, citations, empty-result handling, prompt-injection defense, and privacy are enforced. |
| [testing.md](./testing.md) | The mocked-vs-live testing strategy and what each test proves. |

To **use** the app, read [usage.md](./usage.md). To **understand** it, start with
[architecture.md](./architecture.md), then read
[code-walkthrough.md](./code-walkthrough.md) alongside the source.
