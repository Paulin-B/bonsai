---
name: save-to-obsidian
description: Write something into the Obsidian vault as a proper note. Use when asked to save, note, capture or write down anything for later.
---

# Saving to Obsidian

Call the `save_note` tool. Do not describe what you would write — write it.

## Choosing the title

The title becomes the filename, so make it findable months later: a specific noun
phrase, not a date and not a category.

- Good: `Belt handshake protocol`, `Why flow fields beat A* here`
- Bad: `Notes`, `2026-09-13`, `Chat summary`

If the note belongs with something that already exists, reuse that exact title — the
tool appends rather than overwriting, so related material collects in one place.

## Writing the body

Write the content itself, in markdown. Not "here is a summary of what we discussed" —
the thing that was discussed.

- Lead with the decision or the fact, then the reason for it.
- Link related notes with `[[double brackets]]`; Obsidian resolves them, and a link to
  a note that does not exist yet is a useful marker rather than an error.
- Keep code in fenced blocks with a language tag.
- Leave out the conversational scaffolding. Nobody rereads "great question!".

## What not to save

Do not save something merely because it was said. A note earns its place if it would
save a future reader from re-deriving something: a decision and its reasoning, a
constraint discovered the hard way, a piece of knowledge that was expensive to find.
Transcripts and restatements are noise.

## After saving

Say which note you wrote to, by name. If the tool returned an error — no vault
configured, for instance — say that plainly rather than implying it was saved.
