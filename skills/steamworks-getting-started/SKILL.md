---
name: steamworks-getting-started
description: First steps with the steamworks-mcp server for someone new to it or to Steam releases. Finds the user's games, starts tracking one, explains in plain words what happens next and what stays the user's decision. Use when the user is new, asks "where do I start", "what can you do for my Steam release", or the server was just installed.
---

# Getting started with steamworks-mcp

Tools: `status`, `init_project`, `gap_report`, `start_interview`, `set_field`, `server_info`

The user may not be a programmer. Use plain words, no field paths or tool names unless they ask. Answer in their
language.

## 1. Find the game

Call `status()` without a path. It lists the games in the workspace: tracked ones and Unity, Godot or Unreal
projects not tracked yet. Show its `display` table.

- No game found: ask where the game's folder is. If it is outside the workspace folder shown in `server_info`, tell
  them to run `steamworks-mcp setup` in a terminal and pick the folder that holds their games, then restart the app.
- One or more not tracked yet: ask which one to start with.

## 2. Start tracking it

Call `init_project(path)`. It creates `steamworks.yaml` (every Steam setting of the game, in one readable file) and
reads the project: name, platforms, controller support, saves, achievements and stats used in code. Show what it
found and explain: these are **drafts**; nothing counts until the user says it is right.

Ask the user whether the game already exists in Steamworks (it has an app id). If yes, set it with
`set_field(path, field="apps.main.appid", value=<id>)`.

## 3. Show the road

Call `status(path)` and show its table: the four release steps (before you start, store page, build review,
release), how far each one is, and the one next thing to do. Explain the steps in one sentence each:

- **Step 0 · Prerequisites**: the Steamworks account, bank and tax forms, the app fee. Only the user can do these.
- **Step 1 · Store page**: texts, images, tags, languages. Valve reviews the page before it can go public.
- **Step 2 · Build review**: the uploaded game, Steam features (achievements, cloud saves), system requirements.
- **Step 3 · Release**: the release date, price, and pressing Release in Steamworks.

## 4. Ask the first questions

Call `start_interview(path)` and ask its questions a few at a time; save answers with `set_field`.

## What to tell the user once

- Their files: `steamworks.yaml` holds the values, `.steam-mcp/` the progress. Both can go into their git repository.
- Every value has a status: missing, draft, approved, done in Steamworks. Only the user approves.
- No key or Steam login is needed for anything above. Keys and the browser mode only matter when they want the
  assistant to read or fill Steamworks itself (`server_info` shows what is turned on).
- Nothing is ever published by these tools: the user presses Publish in Steamworks.

Next skills to offer: `steamworks-release` (the whole road), `steamworks-store-page`, `steamworks-sdk-integration`.

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
