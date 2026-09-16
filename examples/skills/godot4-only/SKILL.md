---
name: godot4-only
description: >-
  Write GDScript that actually runs in Godot 4, checking the local offline docs
  before using an API. Use whenever writing, reviewing or fixing .gd files,
  Godot scenes, or anything for the Godot engine.
---

# Godot 4 only

Most GDScript on the internet is Godot 3. It looks correct and will not run.
Before writing Godot code, assume your memory is contaminated with Godot 3
syntax and check.

## Check the local docs first

The offline Godot 4 documentation is on this machine. `LIST_DIR` the docs folder
under the user's storage to find it, then `READ_FILE` the page for the class you
are about to use. Quote the signature you are relying on.

If the docs contradict your memory, the docs win. If you cannot find the page,
say so rather than guessing the API.

## Godot 3 constructs that must never appear

| Godot 3 (wrong) | Godot 4 (correct) |
| --- | --- |
| `var x setget _set_x` | `var x: int : set(value): ...` |
| `signal_emit("done", arg)` | `done.emit(arg)` |
| `connect("pressed", self, "_on_pressed")` | `pressed.connect(_on_pressed)` |
| `yield(get_tree(), "idle_frame")` | `await get_tree().process_frame` |
| `export var speed = 1.0` | `@export var speed: float = 1.0` |
| `onready var node = $Node` | `@onready var node: Node = $Node` |
| `KinematicBody2D` | `CharacterBody2D` |
| `move_and_slide(vel, UP)` | `velocity = vel; move_and_slide()` |
| `instance()` | `instantiate()` |
| `PoolVector2Array` | `PackedVector2Array` |
| `OS.get_ticks_msec()` | `Time.get_ticks_msec()` |
| `rand_range(a, b)` | `randf_range(a, b)` |
| `.empty()` | `.is_empty()` |

## Other things that break

- `Vector2` takes two arguments. `Vector2(x)` is not valid. Use `Vector2(x, y)`,
  or `Vector2(some_vector2i)` to convert.
- Simulation belongs in `_physics_process(delta)`, not `_process(delta)`.
- Do not mutate an array while iterating it. Collect changes and apply after.
- Use tabs for indentation, not spaces.
- `class_name` must be unique across the project.
- A script extending `RefCounted` cannot be attached to a node.

## Before you finish

Write the file, then `RUN` a syntax check if a Godot binary is available, or at
minimum `READ_FILE` it back and re-read it against this list. Report which
classes you verified against the docs and which you did not.
