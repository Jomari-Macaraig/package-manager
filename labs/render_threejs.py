"""three.js renderer: a self-contained interactive HTML page.

Orbit/zoom with the mouse, a step slider to replay the loading order, hover
labels via CSS2DRenderer. three.js loads from the jsDelivr CDN, so viewing
needs internet. Boxes sit side by side in one scene.

    page = render_threejs(plan)                   # HTML string
    IPython.display.HTML(threejs_iframe(page))    # notebook
    page.encode()                                 # API body, text/html
"""

import json
from html import escape

from render_common import (
    box_cuboids, box_size, box_title, footer_lines, item_colors,
)
from schemas import Response

THREE_VERSION = "0.170.0"
GAP = 4.0  # scene units between boxes

_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Packing plan</title>
<style>
  html, body { margin: 0; height: 100%; font-family: Helvetica, Arial,
    sans-serif; background: #fff; overflow: hidden; }
  #ui { position: absolute; top: 10px; left: 12px; z-index: 2; }
  #ui h1 { font-size: 16px; margin: 0 0 6px; }
  #legend { position: absolute; top: 10px; right: 12px; z-index: 2;
    font-size: 13px; }
  #legend div { display: flex; align-items: center; gap: 6px; }
  #legend span { width: 12px; height: 12px; display: inline-block; }
  #footer { position: absolute; bottom: 8px; left: 12px; right: 12px;
    font-size: 12px; z-index: 2; }
  .seq { font-size: 11px; background: rgba(255,255,255,.8);
    padding: 0 3px; border-radius: 3px; }
</style>
<script type="importmap">
{"imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@__V__/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@__V__/examples/jsm/"
}}
</script></head>
<body>
<div id="ui"><h1>__TITLE__</h1>
  <label>Step <input id="step" type="range" min="0" max="__MAX__"
    value="__MAX__"> <output id="stepOut">__MAX__</output> / __MAX__</label>
</div>
<div id="legend">__LEGEND__</div>
<div id="footer">__FOOTER__</div>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject }
  from "three/addons/renderers/CSS2DRenderer.js";

const DATA = __DATA__;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);
scene.add(new THREE.AmbientLight(0xffffff, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.position.set(2, 5, 3);
scene.add(sun);

const renderer = new THREE.WebGLRenderer({ antialias: true,
  preserveDrawingBuffer: true });
renderer.setPixelRatio(window.devicePixelRatio);
document.body.appendChild(renderer.domElement);
const labels = new CSS2DRenderer();
labels.domElement.style.position = "absolute";
labels.domElement.style.top = "0";
labels.domElement.style.pointerEvents = "none";
document.body.appendChild(labels.domElement);

// Plan view coords (u span, v depth, w height) -> three (x=u, y=w, z=v):
// right-handed with y up and +z toward the viewer, so nothing is mirrored.
function cuboid(lo, hi, color, inset) {
  const size = [hi[0] - lo[0] - 2 * inset, hi[2] - lo[2] - 2 * inset,
                hi[1] - lo[1] - 2 * inset];
  const geom = new THREE.BoxGeometry(...size);
  const group = new THREE.Group();
  if (color) group.add(new THREE.Mesh(geom,
    new THREE.MeshLambertMaterial({ color })));
  group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geom),
    new THREE.LineBasicMaterial({ color: 0x111827 })));
  group.position.set((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2,
                     (lo[1] + hi[1]) / 2);
  return group;
}

const units = [];
const bounds = new THREE.Box3();
for (const box of DATA.boxes) {
  const root = new THREE.Group();
  root.position.x = box.offset;
  scene.add(root);
  root.add(cuboid([0, 0, 0], box.size, null, 0));
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(box.size[0], box.size[1]),
    new THREE.MeshLambertMaterial({ color: 0xe5e7eb }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(box.size[0] / 2, -0.01, box.size[1] / 2);
  root.add(floor);
  const name = document.createElement("div");
  name.className = "seq";
  name.textContent = box.title;
  const nameTag = new CSS2DObject(name);
  nameTag.position.set(box.size[0] / 2, box.size[2] + 1, box.size[1] / 2);
  root.add(nameTag);
  for (const u of box.units) {
    const unit = cuboid(u.lo, u.hi, u.color, 0.02);
    const tag = document.createElement("div");
    tag.className = "seq";
    tag.textContent = u.seq;
    tag.title = u.name;
    const label = new CSS2DObject(tag);
    label.position.set(0, (u.hi[2] - u.lo[2]) / 2 + 0.2, 0);
    unit.add(label);
    unit.userData.seq = u.seq;
    root.add(unit);
    units.push(unit);
  }
  bounds.expandByObject(root);
}

const center = bounds.getCenter(new THREE.Vector3());
const radius = bounds.getSize(new THREE.Vector3()).length();
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, radius * 20);
// front-right-top: the box origin (back-left-bottom) is the far corner
camera.position.set(center.x + radius * 0.6, center.y + radius * 0.75,
                    center.z + radius * 1.4);
const controls = new OrbitControls(camera, labels.domElement);
labels.domElement.style.pointerEvents = "auto";
controls.target.copy(center);
controls.update();

const step = document.getElementById("step");
const stepOut = document.getElementById("stepOut");
step.addEventListener("input", () => {
  stepOut.textContent = step.value;
  for (const u of units) {
    u.visible = u.userData.seq <= Number(step.value);
    u.traverse(o => { if (o.isCSS2DObject) o.visible = u.visible; });
  }
});

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h);
  labels.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();
renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
});
</script></body></html>
"""


def render_threejs(plan: Response, upto: int | None = None) -> str:
    """Interactive HTML page; upto sets the slider's starting step."""
    colors = item_colors(plan)
    boxes, offset, max_seq = [], 0.0, 0
    for box in plan.boxes:
        size = box_size(box)
        units = [
            {"seq": c.seq, "name": c.name, "color": c.color,
             "lo": c.lo, "hi": c.hi}
            for c in box_cuboids(box, colors)
        ]
        max_seq = max([max_seq] + [u["seq"] for u in units])
        boxes.append({"title": box_title(box), "size": size,
                      "offset": offset, "units": units})
        offset += size[0] + GAP

    legend = "".join(
        f'<div><span style="background:{color}"></span>{escape(name)}</div>'
        for name, color in colors.items()
    )
    footer = "<br>".join(escape(line) for line in footer_lines(plan, 140))
    # "</" inside a <script> would end it early
    data = json.dumps({"boxes": boxes}).replace("</", "<\\/")
    start = max_seq if upto is None else min(upto, max_seq)

    page = (
        _TEMPLATE.replace("__V__", THREE_VERSION)
        .replace("__TITLE__", escape(" · ".join(b["title"] for b in boxes)))
        .replace("__LEGEND__", legend)
        .replace("__FOOTER__", footer)
        .replace("__DATA__", data)
        .replace('value="__MAX__"> <output id="stepOut">__MAX__</output>',
                 f'value="{start}"> <output id="stepOut">{start}</output>')
        .replace("__MAX__", str(max_seq))
    )
    if start != max_seq:  # apply the starting step on load
        page = page.replace(
            "window.addEventListener(\"resize\", resize);",
            "window.addEventListener(\"resize\", resize);\n"
            "step.dispatchEvent(new Event(\"input\"));",
        )
    return page


def threejs_iframe(page: str, height: int = 600) -> str:
    """Wrap the page in an iframe so notebooks run its scripts in isolation."""
    return (f'<iframe srcdoc="{escape(page, quote=True)}" width="100%" '
            f'height="{height}" style="border:0"></iframe>')
