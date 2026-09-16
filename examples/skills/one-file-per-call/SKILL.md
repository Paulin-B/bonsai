---
name: one-file-per-call
description: >-
  Handle multi-file requests one tool call at a time without stopping early. Use
  whenever the user asks for several files to be written, replaced, updated or
  created in one message, or attaches more than one file to act on.
---

# One file per call

Each tool call handles exactly one file. A request for four files needs four
separate `FILE_OP: WRITE` calls, across as many turns as it takes.

## Procedure

1. Before writing anything, list the files requested. If the user attached
   files, name each attachment.
2. If there are more than two, record them with `TASK: ADD` so the list survives
   the step budget.
3. Write one file. Wait for its result.
4. Check what remains and write the next one. Repeat.
5. Only answer in plain text once every file has a successful tool result.

## The failure to avoid

The common mistake is writing the first file, seeing it succeed, then
summarising as though all of them were done. The tool results above you are the
complete record of this turn. If a file is not in that record, you have not
written it.

If you run out of steps partway, say exactly which files were written and which
were not, so the work can resume next turn.

## Attachments are not saved

A file attached to a message is shown to you as text. Attaching does not put it
anywhere on disk. Replacing a file with attached content still requires you to
issue the `FILE_OP: WRITE` yourself.

## One call, one file

Do not put several `WRITE` calls in a single reply. Emit one, stop, and wait for
the result. Multiple calls in one reply can be misparsed, and the second file's
content ends up inside the first.
