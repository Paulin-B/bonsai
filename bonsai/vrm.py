"""Turning what Bonsai is doing into what a VRM avatar should look like.

A VRM is a glTF file with a VRMC_vrm extension, and the useful part of that extension
is a set of named expressions every conforming model must provide. The sample model
from pixiv carries eighteen: aa, ih, ou, ee, oh for the mouth, happy, angry, sad,
relaxed, neutral for the face, blink for the eyes, and lookUp and friends for the
gaze. That is a better vocabulary than the drawn face has - five mouth shapes instead
of one openness - and it maps onto signals that already exist here.

Kept apart from any renderer on purpose. Deciding what the face should do is
arithmetic over the mood and the sound, testable with no 3D at all; drawing it is
somebody else's problem, and which somebody depends on what is installed.
"""
import re

from .store import load_mood

# What a VRM 1.0 model promises. A model may provide more, and custom ones are common,
# but nothing here depends on anything outside this list.
VRM_EXPRESSIONS = (
    "aa", "ih", "ou", "ee", "oh",
    "happy", "angry", "sad", "relaxed", "neutral", "surprised",
    "blink", "blinkLeft", "blinkRight",
    "lookUp", "lookDown", "lookLeft", "lookRight",
)

# The mood bands, in the model's own words. Nothing invented: every name here is one
# a conforming model has to provide.
MOOD_FACES = (
    (0.55, "happy"),
    (0.20, "relaxed"),
    (-0.20, "neutral"),
    (-0.55, "sad"),
    (-1.01, "angry"),
)

# Loudness to mouth shape. Speech is not one shape opening and closing - running
# through a few vowels at different volumes is most of what reads as talking, and it
# costs nothing because the envelope is already there.
VISEMES = ("ih", "ee", "aa", "oh", "ou")


# An emotion written into the reply, the way Open-LLM-VTuber does it: "[happy] that
# worked" shows on the face and is not read out. The mood underneath is slow and
# earned; this is the line-by-line layer on top of it, and it is the difference
# between a face that reflects the afternoon and one that reacts to the sentence.
EMOTION_TAG_RE = re.compile(r"\[(happy|sad|angry|relaxed|surprised|neutral|joy|sigh"
                            r"|smug|thinking)\]", re.I)

# The words it may write, mapped to what a model can actually do. Several point at
# the same expression on purpose: a model is asked for feelings, not for the
# vocabulary of a file format.
EMOTION_FACES = {
    "happy": "happy", "joy": "happy", "smug": "happy",
    "sad": "sad", "sigh": "sad",
    "angry": "angry", "relaxed": "relaxed", "surprised": "surprised",
    "neutral": "neutral", "thinking": "relaxed",
}


def take_emotions(text):
    """(text without the tags, the emotions it asked for, in order)."""
    found = [m.group(1).lower() for m in EMOTION_TAG_RE.finditer(text or "")]
    return EMOTION_TAG_RE.sub("", text or "").strip(), found


def emotion_face(text):
    """The expression a reply asked for, or None. The last one wins - a line that
    starts cross and ends amused should end amused."""
    _, found = take_emotions(text)
    return EMOTION_FACES.get(found[-1]) if found else None


def mood_face(value):
    for threshold, name in MOOD_FACES:
        if value >= threshold:
            return name
    # Below every band is further down, not back to the middle: falling off the end
    # returned "neutral" for a mood of -5, which is the opposite of what it means.
    return MOOD_FACES[-1][1]


def viseme_for(level, tick=0):
    """(name, weight) for the mouth at this loudness.

    The shape is picked by the tick rather than the level so that a steady note does
    not hold one vowel open, which looks like a scream rather than like speech."""
    if level < 0.06:
        return "aa", 0.0
    return VISEMES[tick % len(VISEMES)], min(1.0, 0.25 + level * 0.75)


def expression_weights(state, level, blink=0.0, tick=0, mood=None, feeling=None):
    """Every expression that should be non-zero right now, as {name: 0..1}.

    One dictionary describing the whole face, so a renderer can set what changed and
    zero the rest without working anything out for itself."""
    value = load_mood()[0] if mood is None else mood
    weights = {}

    # What the line asked for beats what the day has been like: the mood is slow and
    # the sentence is now.
    face = feeling or mood_face(value)
    if face != "neutral":
        # Never full strength: a model holding an expression at 1.0 looks like a mask,
        # and the mouth has to be legible through it.
        weights[face] = 0.8 if feeling else min(0.85, 0.35 + abs(value) * 0.5)

    if state == "talk":
        name, weight = viseme_for(level, tick)
        if weight > 0:
            weights[name] = weight
    elif state == "think":
        weights["lookUp"] = 0.6

    if blink > 0:
        weights["blink"] = min(1.0, blink)
    return weights


def unknown_expressions(weights, available):
    """Anything asked for that this model does not have.

    Custom models leave presets out, and a renderer silently ignoring a name is how
    you end up wondering why the mouth never moves."""
    return sorted(set(weights) - set(available))
