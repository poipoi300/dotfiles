---
name: bro
description: Use only when the user's whole message is "bro" or "/bro", optionally with punctuation such as "bro?". Restates your previous message in plain human language, with no jargon.
metadata:
  source: https://github.com/cursor/plugins/tree/d42aff08aa58965d2735c4ead61a9af82b89d0d0/pstack/skills/bro
---

# Bro

Restate your last message. Stop using jargon and speak coherently. Say it more
simply and concisely, like one human talking to another.

- Say what changed for the user, not how you did it: "the tests pass", not "CI
  is green".
- Leave out hashes, file paths, commands, flags, and counts unless the user
  needs one to act.
- Call each PR, branch, or agent by a name the user would know, not a label you
  made up.
- If a fact needs context to make sense, give the context or leave the fact out.
- Keep every question or decision that is waiting on the user, in plain words.
- Add nothing new: no new findings, no tool calls, no further work.
