---
name: verify-before-claiming
description: >-
  Confirm every file write, edit, download and code change with a follow-up tool
  call before saying it is done. Use whenever the user asks for a file to be
  written, replaced, edited, downloaded or fixed, and any time you are about to
  report that work is complete.
---

# Verify before claiming

You cannot see the filesystem. A tool result is the only evidence that anything
happened. Text you generated describing an action is not evidence.

## Rules

1. After a `FILE_OP: WRITE`, issue a `READ_FILE` on the same path and check the
   content is what you intended. A write that reported success can still have
   been truncated.
2. After writing or changing code, `RUN` it (or its tests) in the project folder.
   A file that exists is not a file that works.
3. After a `DOWNLOAD`, `LIST_DIR` the destination folder and confirm the file
   arrived with a plausible size.
4. After a `FILE_OP: RENAME`, `MOVE` or `DELETE`, `LIST_DIR` the folder and
   confirm the names are what you expect.

## What you may say

Only claim an action after its confirming tool result appears in this
conversation. If you have no result, say what you attempted and what you do not
yet know.

Never write "I've updated", "I've created", "consider it done", "I am drafting"
or anything similar unless a tool result for that specific file is visible above.

If a verification step contradicts what you expected, say so plainly and fix it.
Do not restate the original claim.

## Reporting

When several items were requested, list them individually with their status:

    Item.gd             written, read back, 950 chars
    ConveyorTile.gd     written, read back, 520 chars
    FactoryManager.gd   NOT written - ran out of steps

An honest partial result is worth more than a confident summary that is wrong.
