# Spike: asking the user during a tool call (`Resolve`)

Question: can the interview flow ask the user directly from inside a tool call (MCP form elicitation through the
SDK's `Resolve`), or does it have to hand questions back to the host model?

Code: [`spikes/resolve/`](../spikes/resolve/). It is a throwaway server with three tools that ask the same two
questions:

| Tool | Mechanism |
|---|---|
| `ask_with_resolve` | `Annotated[GameBasics, Resolve(fn)]`, where `fn` returns `Elicit(message, GameBasics)` |
| `ask_with_questions` | returns `{"status": "needs_input", "questions": [...]}`; the host model asks in chat and calls again with `answers` |
| `ask_adaptive` | the resolver takes `Context` and returns `Elicit(...)` only if the client declared `elicitation`, else `None` → `questions` |

## Automated results (in-memory client, `uv run pytest spikes/resolve`)

Run against `mcp` 2.1.0, 2.2.0 and 2.3.0, in both protocol modes: `auto`, which negotiates 2026-07-28 and batches
questions into `InputRequiredResult`, and `legacy`, which uses the initialize handshake and sends mid-call
`elicitation/create` requests.

| Case | Result |
|---|---|
| `Resolve` + client with an elicitation callback | ✅ answers injected, both modes |
| `Resolve` + client **without** elicitation | ❌ the call fails with a **protocol error** (`MISSING_REQUIRED_CLIENT_CAPABILITY`, "Client did not declare the form elicitation capability"), not a tool error result |
| `Resolve`, user declines | the call ends as a tool error |
| `ctx.elicit()` called directly in a tool body | ❌ fails on the 2026-07-28 protocol (`NoBackChannelError`: no server-initiated requests); use `Resolve` instead |
| `questions` round trip | ✅ works with every client, both modes |
| Adaptive resolver | ✅ elicits when supported, falls back to `questions` otherwise, both modes |

Lower bound: `Resolve`, `Elicit` and `MCPServer` work the same from `mcp` 2.1.0 on, so the dependency is
`mcp>=2.1,<3`.

## Decision for the product

1. **`questions` is the primary path.** `start_interview` always returns a structured `questions` payload (id,
   question, suggestion, type, constraints). Every client can use it, ChatGPT included.
2. **Elicitation is an optional shortcut.** It is used only through an adaptive resolver that checks
   `ctx.client_capabilities.elicitation` and otherwise returns `None`. A tool never *requires* elicitation, because a
   missing capability is a protocol error that some hosts show badly.
3. Answers from either path go through the same `set_field(source="user")` logic.
4. `ctx.elicit()` is not used.

## Manual check in real clients (pending)

To be done by hand. Add the spike server to the client, then ask the assistant to call each tool.

```json
{
  "mcpServers": {
    "resolve-spike": {
      "command": "uv",
      "args": ["run", "--directory", "<path to this repo>", "python", "spikes/resolve/server.py"]
    }
  }
}
```

For each client, note:

1. Does `ask_with_resolve` show a form? Or does it fail, and if so, how is the error shown?
2. Does `ask_adaptive` choose elicitation or questions?
3. Does the assistant ask the `ask_with_questions` questions in chat and call back with `answers`?

| Client | `ask_with_resolve` | `ask_adaptive` | `ask_with_questions` |
|---|---|---|---|
| Claude Desktop | _pending_ | _pending_ | _pending_ |
| Antigravity | _pending_ | _pending_ | _pending_ |
