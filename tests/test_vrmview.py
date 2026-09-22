"""The page that draws the VRM, checked without starting a browser.

Nothing here creates a QWebEngineView. Doing that from a test once took Hyprland
down with it - it aborts inside CHyprOpenGLImpl::begin when a second process starts
fighting it for GL state - and a test suite has no business touching the compositor.
What can be checked without one: the files the page needs, the page's own contents,
and every decision about whether to use a VRM at all.
"""
import sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

print("\n-- when a VRM is used at all --")
ga.save_settings(dict(ga.DEFAULTS))
check("no model set means the drawn face", ga.model_path(), None)
check("  ...and it says so", "No VRM model set" in ga.why_no_vrm(), True)
ga.save_settings({**ga.DEFAULTS, "vrm_model": "/nowhere/absent.vrm"})
check("a path that is not a file is not used", ga.model_path(), None)
check("  ...naming the path, since a typo is the likely cause",
      "/nowhere/absent.vrm" in ga.why_no_vrm(), True)
here = Path(__file__).resolve()
ga.save_settings({**ga.DEFAULTS, "vrm_model": str(here)})
check("a file that exists is taken at its word", ga.model_path(), here)
ga.save_settings(dict(ga.DEFAULTS))

print("\n-- the renderer's files --")
check("it knows what it needs", len(ga.vrmview.WEB_FILES) >= 4, True)
check("  ...including three and three-vrm",
      all(any(part in name for name in ga.vrmview.WEB_FILES)
          for part in ("three.module", "three-vrm")), True)
check("  ...and the loader's own dependency, which it imports by relative path",
      "utils/BufferGeometryUtils.js" in ga.vrmview.WEB_FILES, True)
check("missing files are named, not just counted",
      isinstance(ga.missing_web_files(), list), True)

print("\n-- the page itself --")
page = ga.vrmview.PAGE
check("it maps the bare 'three' import, which the loader uses",
      '"three": "./three.module.js"' in page, True)
check("the VRM plugin is registered with the loader",
      "VRMLoaderPlugin" in page and "loader.register" in page, True)
check("the background is transparent, so it can sit over anything",
      "setClearColor(0x000000, 0)" in page and "alpha: true" in page, True)

print("\n-- two bugs the pictures found --")
# A flat half-turn is right for VRM 0.x and shows you the back of the head for 1.0.
check("it does not blindly rotate by PI", "rotation.y = Math.PI" in page, False)
check("  ...it asks three-vrm which way round this one goes",
      "rotateVRM0" in page, True)
# requestAnimationFrame does not fire for a page that is not on screen, so a capture
# that relied on the loop photographed a face that had never been told what to do.
check("a frame's work is callable outside the animation loop",
      "const advance =" in page, True)
check("  ...and the snapshot does it before reading pixels",
      page.index("advance(0.016)") < page.index("toDataURL"), True)
check("the snapshot takes a size, since an unlaid-out page reports none",
      "window.snapshot = (w, h)" in page, True)

print("\n-- expressions are cleared, not just set --")
check("every expression is written every frame",
      "manager.setValue(name, wanted[name] || 0)" in page, True)

print("\n-- and the window falls back on its own --")
window = ga.AvatarWindow()
check("with no model there is no web view", window.model, None)
check("  ...so the drawn face is shown", window.avatar.isHidden(), False)
check("telling it what to do is harmless either way",
      window.show_face("talk", 0.8), False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
