"""Drawing the VRM, in a web view, because that is where three-vrm lives.

The deciding is in vrm.py and needs nothing installed. This is the other half: a page
holding three.js and @pixiv/three-vrm, told what the face should be doing several
times a second.

A browser engine to show an avatar is a lot, and it was still the right call. VRM
models are toon-shaded through MToon, and a renderer without it makes them look
plastic; three-vrm also brings spring bones, so hair and clothes move on their own.
Writing either by hand against a bare 3D engine would be worse in every way than
loading the library the format's own authors maintain.

The libraries are fetched once into a data directory rather than vendored, the same
as the speech models: a megraine of someone else's JavaScript does not belong in this
repository, and it is not needed until a VRM is actually pointed at.
"""
import json
from pathlib import Path

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QColor

from .store import settings

WEB_DIR = Path.home() / ".local/share/bonsai_web"


# Chromium is told not to touch the GPU, and this is not a preference.
#
# A hardware-accelerated web view inside a PyQt window, on Wayland, on NVIDIA,
# corrupts the display: the whole desktop came back as blocks of red and green noise,
# and before that the same combination made Hyprland abort inside its own GL renderer.
# Two compositors fighting over one driver is not something this feature can win.
#
# The cost is nothing here. The avatar is a 200x250 window with one character in it,
# and software rendering draws it in milliseconds - it was verified that way, because
# it was the only way that did not take the session down.
SAFE_FLAGS = ("--disable-gpu --disable-gpu-compositing --disable-software-rasterizer "
              "--use-gl=angle --use-angle=swiftshader --in-process-gpu")


def use_software_rendering():
    """Set before any web view exists, or Chromium has already chosen.

    Left alone if the user has set the variable themselves - someone who has gone out
    of their way to configure this is not to be argued with."""
    import os
    if "QTWEBENGINE_CHROMIUM_FLAGS" in os.environ:
        return os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    if not settings().get("vrm_use_gpu", False):
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = SAFE_FLAGS
    return os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")

# What the page needs, and where each comes from. Fetched on demand so that nobody
# downloads two megabytes of renderer to use a text box.
WEB_FILES = {
    "three.module.js": "https://unpkg.com/three@0.169.0/build/three.module.js",
    "loaders/GLTFLoader.js":
        "https://unpkg.com/three@0.169.0/examples/jsm/loaders/GLTFLoader.js",
    "utils/BufferGeometryUtils.js":
        "https://unpkg.com/three@0.169.0/examples/jsm/utils/BufferGeometryUtils.js",
    "three-vrm.module.js":
        "https://unpkg.com/@pixiv/three-vrm@3.1.6/lib/three-vrm.module.js",
}


def web_ready():
    return all((WEB_DIR / name).is_file() for name in WEB_FILES)


def missing_web_files():
    return [name for name in WEB_FILES if not (WEB_DIR / name).is_file()]


def fetch_web_files(log=print):
    """Download the renderer. Returns True when everything is in place."""
    import urllib.request
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in WEB_FILES.items():
        target = WEB_DIR / name
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        log(f"Fetching {name}...")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        except Exception as exc:
            log(f"Could not fetch {name}: {exc}")
            return False
    return True


def model_path():
    configured = (settings().get("vrm_model") or "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    return path if path.is_file() else None


def why_no_vrm():
    """Why the 3D face is not being used, in a sentence."""
    if model_path() is None:
        configured = (settings().get("vrm_model") or "").strip()
        if configured:
            return f"No VRM model at {configured}."
        return ("No VRM model set. Point 'VRM model' in Settings at a .vrm file and "
                "it will be used instead of the drawn face.")
    if not web_ready():
        return (f"The 3D renderer is not downloaded yet ({len(missing_web_files())} "
                "file(s) missing). It is fetched once, the first time it is needed.")
    return ""


PAGE = """<!doctype html>
<meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  canvas { display: block; }
  #problem { position: absolute; inset: 0; display: none; padding: 12px;
             font: 13px system-ui, sans-serif; color: #e8867a; background: #181a1c; }
</style>
<div id="problem"></div>
<script type="importmap">
{ "imports": { "three": "./three.module.js" } }
</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from './loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from './three-vrm.module.js';

const fail = (message) => {
  const box = document.getElementById('problem');
  box.style.display = 'block';
  box.textContent = message;
  console.log('VRM_ERROR ' + message);
};

const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 0);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
// Head height and a little back: a VRM stands at the origin facing +z, and the
// interesting part of it for this is the face.
const aspect = () => window.innerWidth / Math.max(1, window.innerHeight);
// Taken from the canvas, not left at 1 until a resize event happens to arrive. The
// page came up 2516x1308 and framed itself as though it were square.
const camera = new THREE.PerspectiveCamera(28, aspect(), 0.1, 20);
const light = new THREE.DirectionalLight(0xffffff, 2.2);
light.position.set(1, 1, 1);
scene.add(light, new THREE.AmbientLight(0xffffff, 1.1));

let vrm = null;
let facing = 0;
let wanted = {};
let tick = 0;

const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));

window.loadModel = (url) => {
  loader.load(url, (gltf) => {
    vrm = gltf.userData.vrm;
    if (!vrm) { fail('That file loaded, but it is not a VRM.'); return; }
    // Both are optimisations and both have come and gone between three-vrm
    // versions; combineSkeletons does not exist in 3.1.6 and threw, which lost
    // the model entirely over something that only saves draw calls.
    for (const name of ['removeUnnecessaryVertices', 'combineSkeletons',
                        'removeUnnecessaryJoints']) {
      if (typeof VRMUtils[name] === 'function') {
        try { VRMUtils[name](gltf.scene); } catch (e) { console.log('skip ' + name); }
      }
    }
    // NOT a flat rotation of PI. VRM 0.x faces -z and 1.0 faces +z, so a fixed
    // half-turn is right for one and shows you the back of the head for the other -
    // which is exactly what it did. rotateVRM0 turns 0.x round and leaves 1.0 alone.
    if (typeof VRMUtils.rotateVRM0 === 'function') VRMUtils.rotateVRM0(vrm);
    facing = vrm.scene.rotation.y;
    scene.add(vrm.scene);
    const head = vrm.humanoid?.getNormalizedBoneNode('head');
    const y = head ? head.getWorldPosition(new THREE.Vector3()).y : 1.4;
    camera.aspect = aspect();
    camera.updateProjectionMatrix();
    camera.position.set(0, y, 1.15);
    camera.lookAt(0, y - 0.03, 0);
    console.log('VRM_READY ' + (vrm.expressionManager
      ? Object.keys(vrm.expressionManager.expressionMap).join(',') : ''));
  }, undefined, (err) => fail('Could not load the model: ' + err));
};

window.setExpressions = (weights) => { wanted = weights || {}; };

// One frame's worth of work, kept out of the animation loop so that a snapshot can
// do it too. requestAnimationFrame does not fire for a page that is not on screen,
// so a capture that relied on the loop photographed a face that had never been told
// what to do - a neutral expression, whatever had been asked for.
const advance = (delta) => {
  if (!vrm) return;
  const manager = vrm.expressionManager;
  if (manager) {
    // Everything is set every frame, including to zero: leaving last frame's
    // expression behind is how a face gets stuck mid-word.
    for (const name of Object.keys(manager.expressionMap)) {
      manager.setValue(name, wanted[name] || 0);
    }
  }
  // A small sway, so it is not a photograph. Nothing dramatic - it reads as alive
  // at an amplitude you would not consciously notice.
  tick += delta;
  vrm.scene.position.y = Math.sin(tick * 1.1) * 0.004;
  vrm.scene.rotation.y = facing + Math.sin(tick * 0.43) * 0.05;
  vrm.update(delta);
};

const clock = new THREE.Clock();
const render = () => {
  requestAnimationFrame(render);
  advance(clock.getDelta());
  renderer.render(scene, camera);
};
render();

window.addEventListener('resize', () => {
  renderer.setSize(window.innerWidth, window.innerHeight);
  camera.aspect = aspect();
  camera.updateProjectionMatrix();
});
// Renders and hands back the pixels. The only way to see what a web view actually
// drew: Qt's widget grab() captures the page's own compositing and comes back blank
// for anything WebGL put on the screen.
window.diagnose = () => {
  if (!vrm) return 'no model loaded';
  const box = new THREE.Box3().setFromObject(vrm.scene);
  const size = box.getSize(new THREE.Vector3());
  return JSON.stringify({
    canvas: [renderer.domElement.width, renderer.domElement.height],
    window: [window.innerWidth, window.innerHeight],
    camera: camera.position.toArray().map(n => +n.toFixed(2)),
    modelMin: box.min.toArray().map(n => +n.toFixed(2)),
    modelMax: box.max.toArray().map(n => +n.toFixed(2)),
    modelSize: size.toArray().map(n => +n.toFixed(2)),
    visible: vrm.scene.visible,
  });
};
window.snapshot = (w, h) => {
  // An explicit size, because a page that has not been laid out reports a window of
  // zero and draws into a canvas of nothing - which comes back as a valid but empty
  // data URL, and looks exactly like a scene that rendered badly.
  if (w && h) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  advance(0.016);
  renderer.render(scene, camera);
  return renderer.domElement.toDataURL('image/png');
};
console.log('VRM_PAGE_READY');
</script>
"""


def write_page():
    """The page lives beside the libraries so relative imports resolve."""
    page = WEB_DIR / "avatar.html"
    page.write_text(PAGE, encoding="utf-8")
    return page


class VrmView:
    """Wraps a web view so the rest of the app never imports QtWebEngine.

    Made through `build()` rather than a constructor, so that a machine without the
    bindings gets None and the drawn face, instead of an ImportError at startup."""

    @staticmethod
    def build(parent=None):
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            return None
        if not web_ready() or model_path() is None:
            return None

        from PyQt6.QtWebEngineCore import QWebEngineSettings
        use_software_rendering()
        view = QWebEngineView(parent)
        view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        # The page is a file:// URL and the model is a file:// URL somewhere else,
        # which Chromium refuses by default - it comes back as "Failed to fetch" with
        # nothing about permissions in it. The page is one this app wrote, and the
        # only thing it reads is the model the user chose.
        attributes = view.settings()
        attributes.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        attributes.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        view.setUrl(QUrl.fromLocalFile(str(write_page())))
        model = model_path()

        def show_model(_ok=True):
            view.page().runJavaScript(
                f"window.loadModel({json.dumps(QUrl.fromLocalFile(str(model)).toString())})")

        view.loadFinished.connect(show_model)
        return view

    @staticmethod
    def apply(view, weights):
        if view is not None:
            view.page().runJavaScript(f"window.setExpressions({json.dumps(weights)})")
