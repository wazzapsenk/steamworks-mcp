---
name: steamworks-community
description: Talk to players on Steam - events and announcements (update notes, release, roadmap, livestreams), the Community Hub and discussions, Steam keys for press and creators under Valve's key rules, and replying to reviews. Drafts the texts in the game's voice. Use when the user posts an update, plans a roadmap post, manages the community or gives out keys.
---

# Community, announcements and keys

Tools: `status`, `launch_watch`, `study_reviews`

## Events and announcements

Steamworks > Community > Events & Announcements. Followers and owners see them in their library and the store page
shows them, so a regular rhythm helps visibility. Common kinds: a small update (patch notes), a major update, a
release, a livestream, an in-game event, a news post. Each needs a title, a short summary, the text (Steam BBCode)
and a cover image the editor asks for.

Draft them for the user in the game's voice (`status(path)` and the store text show it):

- **Patch notes**: one line of what matters most, then Fixed / Changed / Added lists in player words, then known
  issues. Thank reporters without naming them unless they agreed.
- **Roadmap**: what is next, by quarter or "soon / later", never a promise of dates you cannot keep.
- **Release / big update**: the hook in the first line, two or three highlights with images, a call to wishlist,
  buy or join the community.

The user posts and schedules them in Steamworks.

## Community Hub

Discussions (pin a bug-report thread and an FAQ; the `steamworks-bug-reports` skill has a template), guides,
screenshots and artwork, broadcasting. Moderators can be added in Steamworks.

## Reviews

Developers can reply publicly to reviews. Reply briefly and factually (a fix landed, a workaround), never argue.
`study_reviews(path, appids=[<own app id>])` shows the main themes; `launch_watch` the trend.

## Steam keys

Request keys in Steamworks for press, creators, testers and other stores. Valve's key rules allow selling keys
elsewhere, but Steam customers must not get a worse deal than buyers elsewhere; Valve may decline unusual requests.
Read the current rules on the Steam Keys page before a big request, and track who got which key.

Valve docs: https://partner.steamgames.com/doc/marketing/event_tools, https://partner.steamgames.com/doc/features/community,
https://partner.steamgames.com/doc/features/keys, https://partner.steamgames.com/doc/marketing/visibility
