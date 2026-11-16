"""
Ease Presets for Cinema 4D
==========================

A small preset library for animation curves.

* Click a preset tile to preview it, double-click (or press Apply) to apply it.
* Applies to every pair of consecutive *selected* keys (Timeline / F-Curve),
  or, if no keys are selected, to every key segment of the selected objects.
* Bezier presets set the tangents of the two keys (CSS cubic-bezier style).
* Bounce / Elastic presets insert keys between the two keys and shape them.
* Save your own curves from selected keys (2 keys = easing, 3+ keys = full
  curve shape) or save the edited parameters as a new preset.

User presets are stored as JSON next to this plugin's prefs
(ease_presets_user.json in the Cinema 4D prefs folder) so they can be shared.

Tested in Cinema 4D 2027 (Python 3.11).
"""

import c4d
import os
import json
import math
import copy
import time
from c4d import gui, plugins, storage, bitmaps

# Development ID. Get a free unique ID from https://developers.maxon.net
# before distributing this plugin to other machines.
PLUGIN_ID = 1066741
PLUGIN_NAME = "Ease Presets"
PLUGIN_VERSION = "1.2"
USER_FILE = "ease_presets_user.json"

EPS = 1e-6

# --------------------------------------------------------------------------
# Built-in presets
# --------------------------------------------------------------------------


def _bz(name, x1, y1, x2, y2):
    return {"name": name, "kind": "bezier", "builtin": True, "mirror": False,
            "params": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


def _dyn(name, kind, amplitude, count, decay, mirror=False):
    return {"name": name, "kind": kind, "builtin": True, "mirror": mirror,
            "params": {"amplitude": amplitude, "count": count, "decay": decay}}


BUILTIN_PRESETS = [
    _bz("Linear", 1 / 3.0, 1 / 3.0, 2 / 3.0, 2 / 3.0),
    _bz("Ease", 0.25, 0.1, 0.25, 1.0),
    _bz("Smooth", 0.5, 0.0, 0.5, 1.0),
    _bz("Sine In", 0.12, 0.0, 0.39, 0.0),
    _bz("Sine Out", 0.61, 1.0, 0.88, 1.0),
    _bz("Sine In-Out", 0.37, 0.0, 0.63, 1.0),
    _bz("Cubic In", 0.32, 0.0, 0.67, 0.0),
    _bz("Cubic Out", 0.33, 1.0, 0.68, 1.0),
    _bz("Cubic In-Out", 0.65, 0.0, 0.35, 1.0),
    _bz("Quint In", 0.64, 0.0, 0.78, 0.0),
    _bz("Quint Out", 0.22, 1.0, 0.36, 1.0),
    _bz("Quint In-Out", 0.83, 0.0, 0.17, 1.0),
    _bz("Expo In", 0.7, 0.0, 0.84, 0.0),
    _bz("Expo Out", 0.16, 1.0, 0.3, 1.0),
    _bz("Expo In-Out", 0.87, 0.0, 0.13, 1.0),
    _bz("Back In", 0.36, 0.0, 0.66, -0.56),
    _bz("Back Out", 0.34, 1.56, 0.64, 1.0),
    _bz("Back In-Out", 0.68, -0.6, 0.32, 1.6),
    _bz("Anticipate", 0.5, -0.35, 0.3, 1.0),
    _bz("Snap", 0.9, 0.0, 0.1, 1.0),
    _dyn("Bounce Out", "bounce", 0.35, 3, 0.16),
    _dyn("Bounce In", "bounce", 0.35, 3, 0.16, mirror=True),
    _dyn("Elastic Out", "elastic", 0.4, 4, 0.1),
    _dyn("Elastic In", "elastic", 0.4, 4, 0.1, mirror=True),
]

# --------------------------------------------------------------------------
# Normalised curve model
#   A curve is a list of keys {"u", "v", "lt": [dt, dv], "rt": [dt, dv]}
#   with u (time) and v (value) normalised so the first key is (0,0) and the
#   last key is (1,1). Tangents are in the same normalised space.
# --------------------------------------------------------------------------


def _key(u, v, lt=(0.0, 0.0), rt=(0.0, 0.0)):
    return {"u": float(u), "v": float(v), "lt": [float(lt[0]), float(lt[1])],
            "rt": [float(rt[0]), float(rt[1])]}


def bezier_keys(x1, y1, x2, y2):
    return [_key(0, 0, rt=(x1, y1)), _key(1, 1, lt=(x2 - 1.0, y2 - 1.0))]


def _step_ratio(decay, count):
    """Per-step ratio so that step (count-1) ends at `decay` x the first."""
    decay = min(max(float(decay), 0.01), 1.0)
    if count <= 1:
        return decay
    return decay ** (1.0 / (count - 1))


def bounce_keys(amplitude, count, decay):
    """Ball-drop style bounce settling on the target value.
    amplitude = first bounce height (fraction of the move),
    count = number of bounces, decay = height of the last bounce relative
    to the first (spread evenly, so every bounce stays visible)."""
    count = max(1, int(round(count)))
    amplitude = max(0.0, float(amplitude))
    ratio = _step_ratio(decay, count)
    heights = [amplitude * ratio ** i for i in range(count)]
    # Physics: time of a half arc is proportional to sqrt(height).
    durations = [1.0] + [2.0 * math.sqrt(h) for h in heights]
    total = sum(durations)

    fall = durations[0] / total
    keys = [_key(0.0, 0.0, rt=(fall / 3.0, 0.0))]
    t = fall
    prev_half, prev_h = fall, 1.0
    for i, h in enumerate(heights):
        half = durations[i + 1] / total / 2.0
        # impact (sharp corner): parabola in from previous peak, out to next
        keys.append(_key(t, 1.0,
                         lt=(-prev_half / 3.0, -(2.0 / 3.0) * prev_h),
                         rt=(half / 3.0, -(2.0 / 3.0) * h)))
        # apex of the bounce (flat)
        keys.append(_key(t + half, 1.0 - h,
                         lt=(-half / 3.0, 0.0), rt=(half / 3.0, 0.0)))
        t += 2.0 * half
        prev_half, prev_h = half, h
    keys.append(_key(1.0, 1.0, lt=(-prev_half / 3.0, -(2.0 / 3.0) * prev_h)))
    return keys


def elastic_keys(amplitude, count, decay):
    """Spring-like oscillation around the target value.
    amplitude = first overshoot (fraction of the move),
    count = number of overshoots, decay = size of the last overshoot
    relative to the first."""
    count = max(1, int(round(count)))
    amplitude = float(amplitude)
    ratio = _step_ratio(decay, count)
    seg = 1.0 / (count + 1)
    h = seg / 3.0
    first_peak = 1.0 + amplitude
    keys = [_key(0.0, 0.0, rt=(h, first_peak * 0.5))]
    for i in range(count):
        v = 1.0 + amplitude * ((-ratio) ** i)
        keys.append(_key(seg * (i + 1), v, lt=(-h, 0.0), rt=(h, 0.0)))
    keys.append(_key(1.0, 1.0, lt=(-h, 0.0)))
    return keys


def mirror_keys(keys):
    """Time-reverse a curve: f_in(x) = 1 - f_out(1 - x)."""
    out = []
    for k in reversed(keys):
        out.append(_key(1.0 - k["u"], 1.0 - k["v"],
                        lt=(-k["rt"][0], -k["rt"][1]),
                        rt=(-k["lt"][0], -k["lt"][1])))
    return out


def build_keys(preset):
    kind = preset.get("kind")
    p = preset.get("params", {})
    if kind == "bezier":
        keys = bezier_keys(p["x1"], p["y1"], p["x2"], p["y2"])
    elif kind == "bounce":
        keys = bounce_keys(p["amplitude"], p["count"], p["decay"])
    elif kind == "elastic":
        keys = elastic_keys(p["amplitude"], p["count"], p["decay"])
    elif kind == "curve":
        keys = copy.deepcopy(p["keys"])
    else:
        keys = bezier_keys(1 / 3.0, 1 / 3.0, 2 / 3.0, 2 / 3.0)
    if preset.get("mirror"):
        keys = mirror_keys(keys)
    return keys


# ---------------------------- evaluation (for drawing) ----------------------

def _segments(keys):
    for a, b in zip(keys, keys[1:]):
        p0 = (a["u"], a["v"])
        p1 = (a["u"] + a["rt"][0], a["v"] + a["rt"][1])
        p3 = (b["u"], b["v"])
        p2 = (b["u"] + b["lt"][0], b["v"] + b["lt"][1])
        yield p0, p1, p2, p3


def _bez(p0, p1, p2, p3, s):
    m = 1.0 - s
    a, b, c, d = m * m * m, 3 * m * m * s, 3 * m * s * s, s * s * s
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def sample_curve(keys, steps=24):
    pts = []
    for seg in _segments(keys):
        for i in range(steps + 1):
            if pts and i == 0:
                continue
            pts.append(_bez(*seg, s=i / float(steps)))
    return pts


def eval_curve(keys, u):
    u = min(max(u, 0.0), 1.0)
    for seg in _segments(keys):
        if seg[0][0] - EPS <= u <= seg[3][0] + EPS:
            lo, hi = 0.0, 1.0
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                if _bez(*seg, s=mid)[0] < u:
                    lo = mid
                else:
                    hi = mid
            return _bez(*seg, s=0.5 * (lo + hi))[1]
    return keys[-1]["v"]


# --------------------------------------------------------------------------
# Scene helpers
# --------------------------------------------------------------------------

SELECT_BITS = [c4d.NBIT_TL1_SELECT, c4d.NBIT_TL2_SELECT,
               c4d.NBIT_TL3_SELECT, c4d.NBIT_TL4_SELECT]


def key_selected(key):
    for bit in SELECT_BITS:
        if key.GetNBit(bit):
            return True
    return False


def _walk_objects(op):
    while op:
        yield op
        for tag in op.GetTags():
            yield tag
        for child in _walk_objects(op.GetDown()):
            yield child
        op = op.GetNext()


def iter_animated_nodes(doc):
    for node in _walk_objects(doc.GetFirstObject()):
        yield node
    mat = doc.GetFirstMaterial()
    while mat:
        yield mat
        mat = mat.GetNext()


def value_tracks(node):
    for track in node.GetCTracks():
        if track.GetTrackCategory() == c4d.CTRACK_CATEGORY_VALUE:
            yield track


def collect_jobs(doc):
    """Returns ([(track, [(tA, tB), ...]), ...], mode)."""
    jobs = []
    for node in iter_animated_nodes(doc):
        for track in value_tracks(node):
            curve = track.GetCurve()
            times = [curve.GetKey(i).GetTime() for i in range(curve.GetKeyCount())
                     if key_selected(curve.GetKey(i))]
            if len(times) >= 2:
                times.sort(key=lambda t: t.Get())
                jobs.append((track, list(zip(times, times[1:]))))
    if jobs:
        return jobs, "selected keys"

    for op in doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE):
        nodes = [op] + list(op.GetTags())
        for node in nodes:
            for track in value_tracks(node):
                curve = track.GetCurve()
                times = [curve.GetKey(i).GetTime() for i in range(curve.GetKeyCount())]
                if len(times) >= 2:
                    times.sort(key=lambda t: t.Get())
                    jobs.append((track, list(zip(times, times[1:]))))
    return jobs, "selected objects"


def _free_key(key, curve):
    """Make a key's tangents user-editable and independent."""
    key.ChangeNBit(c4d.NBIT_CKEY_AUTO, c4d.NBITCONTROL_CLEAR)
    key.ChangeNBit(c4d.NBIT_CKEY_BREAK, c4d.NBITCONTROL_SET)
    key.ChangeNBit(c4d.NBIT_CKEY_LOCK_O, c4d.NBITCONTROL_CLEAR)
    key.ChangeNBit(c4d.NBIT_CKEY_LOCK_L, c4d.NBITCONTROL_CLEAR)
    key.ChangeNBit(c4d.NBIT_CKEY_KEEPVISUALANGLE, c4d.NBITCONTROL_CLEAR)
    # Clamp / Remove Overshoot flatten handles that point against the motion
    # (Anticipate, Back In-Out) - presets must keep their exact tangents.
    key.ChangeNBit(c4d.NBIT_CKEY_CLAMP, c4d.NBITCONTROL_CLEAR)
    key.ChangeNBit(c4d.NBIT_CKEY_REMOVEOVERSHOOT, c4d.NBITCONTROL_CLEAR)


def _find(curve, t):
    res = curve.FindKey(t, c4d.FINDANIM_EXACT)
    return res["key"] if res else None


def apply_segment(doc, curve, t_a, t_b, keys, replace_between=True):
    ka, kb = _find(curve, t_a), _find(curve, t_b)
    if ka is None or kb is None:
        return None
    t0, t1 = t_a.Get(), t_b.Get()
    dt = t1 - t0
    if dt <= EPS:
        return None
    v0, v1 = ka.GetValue(), kb.GetValue()
    dv = v1 - v0

    if replace_between or len(keys) > 2:
        for i in reversed(range(curve.GetKeyCount())):
            t = curve.GetKey(i).GetTime().Get()
            if t0 + EPS < t < t1 - EPS:
                curve.DelKey(i)

    # inner keys (bounce, elastic, saved multi-key curves)
    for k in keys[1:-1]:
        res = curve.AddKey(c4d.BaseTime(t0 + k["u"] * dt))
        if not res:
            continue
        nk = res["key"]
        curve.SetKeyDefault(doc, res["nidx"])
        nk.SetValue(curve, v0 + k["v"] * dv)
        nk.SetInterpolation(curve, c4d.CINTERPOLATION_SPLINE)
        _free_key(nk, curve)
        nk.SetTimeLeft(curve, c4d.BaseTime(k["lt"][0] * dt))
        nk.SetValueLeft(curve, k["lt"][1] * dv)
        nk.SetTimeRight(curve, c4d.BaseTime(k["rt"][0] * dt))
        nk.SetValueRight(curve, k["rt"][1] * dv)

    # re-fetch: inserting keys can move the key array in memory
    ka, kb = _find(curve, t_a), _find(curve, t_b)
    first, last = keys[0], keys[-1]
    ka.SetInterpolation(curve, c4d.CINTERPOLATION_SPLINE)
    _free_key(ka, curve)
    ka.SetTimeRight(curve, c4d.BaseTime(first["rt"][0] * dt))
    ka.SetValueRight(curve, first["rt"][1] * dv)
    _free_key(kb, curve)
    kb.SetTimeLeft(curve, c4d.BaseTime(last["lt"][0] * dt))
    kb.SetValueLeft(curve, last["lt"][1] * dv)

    # verify: compare C4D's curve with the preset shape
    if abs(dv) < EPS:
        return 0.0
    err = 0.0
    for i in range(1, 16):
        u = i / 16.0
        got = curve.GetValue(c4d.BaseTime(t0 + u * dt))
        err = max(err, abs((got - v0) / dv - eval_curve(keys, u)))
    return err


def apply_preset(doc, preset, replace_between=True):
    keys = build_keys(preset)
    jobs, mode = collect_jobs(doc)
    if not jobs:
        return 0, 0, mode, 0.0
    count = 0
    worst = 0.0
    doc.StartUndo()
    for track, pairs in jobs:
        doc.AddUndo(c4d.UNDOTYPE_CHANGE, track)
        curve = track.GetCurve()
        for t_a, t_b in pairs:
            err = apply_segment(doc, curve, t_a, t_b, keys, replace_between)
            if err is not None:
                count += 1
                worst = max(worst, err)
        curve.SetKeyDirty()
    doc.EndUndo()
    c4d.EventAdd()
    return count, len(jobs), mode, worst


def capture_from_selection(doc):
    """Reads the first curve that has 2+ selected keys and returns a preset
    (bezier when exactly 2 keys span the range, else a multi-key curve)."""
    for node in iter_animated_nodes(doc):
        for track in value_tracks(node):
            curve = track.GetCurve()
            sel = [i for i in range(curve.GetKeyCount()) if key_selected(curve.GetKey(i))]
            if len(sel) < 2:
                continue
            i0, i1 = min(sel), max(sel)
            k0, k1 = curve.GetKey(i0), curve.GetKey(i1)
            t0, t1 = k0.GetTime().Get(), k1.GetTime().Get()
            v0, v1 = k0.GetValue(), k1.GetValue()
            dt, dv = t1 - t0, v1 - v0
            if dt <= EPS:
                continue
            if abs(dv) < EPS:
                return None, "The first and last selected keys have the same value - can't normalise that curve."
            raw = [curve.GetKey(i) for i in range(i0, i1 + 1)]
            keys = []
            for k in raw:
                keys.append(_key((k.GetTime().Get() - t0) / dt, (k.GetValue() - v0) / dv,
                                 lt=(k.GetTimeLeft().Get() / dt, k.GetValueLeft() / dv),
                                 rt=(k.GetTimeRight().Get() / dt, k.GetValueRight() / dv)))
            # straight segments for linear keys
            for a, b, ck in zip(keys, keys[1:], raw):
                if ck.GetInterpolation() != c4d.CINTERPOLATION_SPLINE:
                    du, dvv = b["u"] - a["u"], b["v"] - a["v"]
                    a["rt"] = [du / 3.0, dvv / 3.0]
                    b["lt"] = [-du / 3.0, -dvv / 3.0]
            keys[0]["lt"] = [0.0, 0.0]
            keys[-1]["rt"] = [0.0, 0.0]
            if len(keys) == 2:
                p = {"x1": keys[0]["rt"][0], "y1": keys[0]["rt"][1],
                     "x2": 1.0 + keys[1]["lt"][0], "y2": 1.0 + keys[1]["lt"][1]}
                return {"name": "", "kind": "bezier", "mirror": False,
                        "builtin": False, "params": p}, None
            return {"name": "", "kind": "curve", "mirror": False,
                    "builtin": False, "params": {"keys": keys}}, None
    return None, "Select at least 2 keys on one curve (Timeline / F-Curve)."


# --------------------------------------------------------------------------
# Preset storage
# --------------------------------------------------------------------------

class PresetStore(object):
    def __init__(self):
        self.path = os.path.join(storage.GeGetC4DPath(c4d.C4D_PATH_PREFS), USER_FILE)
        self.user = []
        self.load()

    def load(self):
        self.user = []
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for p in data.get("presets", []):
                    p["builtin"] = False
                    self.user.append(p)
            except Exception as e:
                print("[Ease Presets] Could not read %s: %s" % (self.path, e))

    def save(self):
        data = {"version": 1, "presets": [
            {k: v for k, v in p.items() if k != "builtin"} for p in self.user]}
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            gui.MessageDialog("Could not save presets:\n%s" % e)

    def all(self):
        return BUILTIN_PRESETS + self.user

    def add(self, preset):
        preset = copy.deepcopy(preset)
        preset["builtin"] = False
        self.user.append(preset)
        self.save()

    def remove(self, preset):
        if preset in self.user:
            self.user.remove(preset)
            self.save()


# --------------------------------------------------------------------------
# UI  (design: "C - Native flat")
# --------------------------------------------------------------------------

def _hex(s):
    s = s.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def _lighten(c, amt):
    return tuple(min(1.0, max(0.0, x + amt)) for x in c)


def _vec(c):
    return c4d.Vector(c[0], c[1], c[2])


GREEN = _hex("#324332")        # highlight (selected tile, active elements, Apply)
GREEN_EDGE = _hex("#4f7352")   # border of the selected tile
GREEN_TINT = _hex("#a8cfa9")   # curves / underline on dark ground
WHITE = (1.0, 1.0, 1.0)
TEXT = _hex("#ececec")
TEXT_SUB = _hex("#a6a6a6")

THEME = {}


def _init_theme():
    """Custom-drawn areas follow the C4D interface background."""
    bg = _hex("#2b2b2b")
    try:
        v = gui.GetGuiWorldColor(c4d.COLOR_BG)
        if v is not None:
            bg = (v.x, v.y, v.z)
    except Exception:
        pass
    THEME.update({
        "bg": bg,
        "card": _lighten(bg, 0.035),
        "border": _lighten(bg, 0.085),
        "guide": _lighten(bg, 0.11),
        "track": _lighten(bg, 0.07),
    })


_VCENTER = getattr(c4d, "DRAWTEXT_VALIGN_CENTER", 0)


class Raster(object):
    """Tiny anti-aliased rasteriser (pure Python) -> BaseBitmap.
    Keeps the thumbnails smooth whatever C4D's line drawing does."""

    def __init__(self, w, h, bg, base=None):
        self.w, self.h = max(1, int(w)), max(1, int(h))
        self.px = list(base) if base is not None else [tuple(bg)] * (self.w * self.h)

    def blend(self, x, y, col, a):
        if a <= 0.0 or x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        if a > 1.0:
            a = 1.0
        i = y * self.w + x
        r, g, b = self.px[i]
        self.px[i] = (r + (col[0] - r) * a, g + (col[1] - g) * a, b + (col[2] - b) * a)

    def round_rect(self, x1, y1, x2, y2, rad, col, alpha=1.0):
        hx, hy = (x2 - x1) * 0.5, (y2 - y1) * 0.5
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        rad = min(rad, hx, hy)
        for y in range(max(0, int(y1)), min(self.h, int(math.ceil(y2)))):
            qy = abs(y + 0.5 - my) - hy + rad
            for x in range(max(0, int(x1)), min(self.w, int(math.ceil(x2)))):
                qx = abs(x + 0.5 - mx) - hx + rad
                d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - rad
                cov = 0.5 - d
                if cov > 0.0:
                    self.blend(x, y, col, (cov if cov < 1.0 else 1.0) * alpha)

    def card(self, x1, y1, x2, y2, rad, fill, border):
        self.round_rect(x1, y1, x2, y2, rad, border)
        self.round_rect(x1 + 1, y1 + 1, x2 - 1, y2 - 1, rad - 1, fill)

    def stroke(self, pts, width, col, alpha=1.0, step=0.3):
        """Anti-aliased polyline: max coverage of densely stamped discs."""
        r = width * 0.5
        cov = {}
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(bx - ax, by - ay) / step))
            for i in range(n + 1):
                px = ax + (bx - ax) * i / n
                py = ay + (by - ay) * i / n
                for y in range(int(py - r - 1), int(py + r + 2)):
                    dy = y + 0.5 - py
                    for x in range(int(px - r - 1), int(px + r + 2)):
                        c = r + 0.5 - math.hypot(x + 0.5 - px, dy)
                        if c > 0.0:
                            k = (x, y)
                            if c > cov.get(k, 0.0):
                                cov[k] = c if c < 1.0 else 1.0
        for (x, y), c in cov.items():
            self.blend(x, y, col, c * alpha)

    def dashed_h(self, y, x1, x2, col, alpha=1.0, dash=2, gap=3):
        y = int(round(y))
        x = int(x1)
        while x < x2:
            for i in range(dash):
                if x + i < x2:
                    self.blend(x + i, y, col, alpha)
            x += dash + gap

    def line_v(self, x, y1, y2, col, alpha=1.0):
        for y in range(int(y1), int(y2)):
            self.blend(int(x), y, col, alpha)

    def dot(self, cx, cy, r, col, alpha=1.0):
        for y in range(int(cy - r - 1), int(cy + r + 2)):
            for x in range(int(cx - r - 1), int(cx + r + 2)):
                c = r + 0.5 - math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                if c > 0.0:
                    self.blend(x, y, col, min(c, 1.0) * alpha)

    def bitmap(self):
        bmp = bitmaps.BaseBitmap()
        if bmp.Init(self.w, self.h, 24) != c4d.IMAGERESULT_OK:
            return None
        w = self.w
        for i, (r, g, b) in enumerate(self.px):
            bmp.SetPixel(i % w, i // w, int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))
        return bmp


def _curve_mapper(keys, x1, y1, x2, y2):
    pts = sample_curve(keys, 32)
    vmin = min(0.0, min(p[1] for p in pts))
    vmax = max(1.0, max(p[1] for p in pts))
    span = max(vmax - vmin, EPS)

    def to_px(u, v):
        return (x1 + u * (x2 - x1), y2 - (v - vmin) / span * (y2 - y1))
    return [to_px(u, v) for u, v in pts], to_px


def preset_category(p):
    if not p.get("builtin"):
        return "My Presets"
    if p.get("kind") in ("bounce", "elastic"):
        return "Dynamics"
    if p.get("name", "").startswith("Back") or p.get("name") in ("Anticipate", "Snap"):
        return "Back"
    return "Ease"


TABS = ["All", "Ease", "Back", "Dynamics", "My Presets"]
GRID_COLS = 5
TILE_H = 70
TILE_GAP = 8


def _preset_key(p):
    return json.dumps([p.get("kind"), p.get("params"), bool(p.get("mirror"))], sort_keys=True)


_BASES = {}


def _card_base(w, h, rad, fill, border):
    """Pixels of an empty rounded card, cached (the slow part of a redraw)."""
    key = (w, h, rad, fill, border, THEME["bg"])
    px = _BASES.get(key)
    if px is None:
        if len(_BASES) > 60:
            _BASES.clear()
        r = Raster(w, h, THEME["bg"])
        r.card(0, 0, w, h, rad, fill, border)
        px = _BASES[key] = r.px
    return px


class BitmapCache(object):
    def __init__(self):
        self.items = {}

    def get(self, key, build):
        bmp = self.items.get(key)
        if bmp is None:
            if len(self.items) > 300:
                self.items.clear()
            try:
                bmp = build()
            except Exception as e:
                print("[Ease Presets] draw error: %s" % e)
                bmp = None
            self.items[key] = bmp
        return bmp


def _draw_bmp(area, bmp, x, y):
    if bmp is None:
        return
    w, h = bmp.GetBw(), bmp.GetBh()
    area.DrawBitmap(bmp, int(x), int(y), w, h, 0, 0, w, h, c4d.BMP_NORMAL)


class TabBar(gui.GeUserArea):
    HEIGHT = 40

    def __init__(self, dlg):
        super(TabBar, self).__init__()
        self.dlg = dlg
        self.hits = []

    def GetMinSize(self):
        return 300, self.HEIGHT

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        w, h = self.GetWidth(), self.GetHeight()
        self.DrawSetPen(_vec(THEME["bg"]))
        self.DrawRectangle(0, 0, w, h)
        self.DrawSetPen(_vec(THEME["border"]))
        self.DrawRectangle(0, h - 1, w, h)
        self.DrawSetFont(c4d.FONT_BOLD)
        x = 16
        self.hits = []
        for i, name in enumerate(TABS):
            tw = self.DrawGetTextWidth(name)
            active = i == self.dlg.tab
            self.DrawSetTextCol(_vec(TEXT if active else TEXT_SUB), c4d.COLOR_TRANS)
            self.DrawText(name, x, h // 2, c4d.DRAWTEXT_HALIGN_LEFT | _VCENTER)
            if active:
                self.DrawSetPen(_vec(GREEN_TINT))
                self.DrawRectangle(x, h - 3, x + tw, h - 1)
            self.hits.append((x - 9, x + tw + 9, i))
            x += tw + 22
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(_vec(TEXT_SUB), c4d.COLOR_TRANS)
        self.DrawText("%s %s" % (PLUGIN_NAME, PLUGIN_VERSION), w - 16, h // 2,
                      c4d.DRAWTEXT_HALIGN_RIGHT | _VCENTER)

    def InputEvent(self, msg):
        if msg[c4d.BFM_INPUT_DEVICE] != c4d.BFM_INPUT_MOUSE or \
                msg[c4d.BFM_INPUT_CHANNEL] != c4d.BFM_INPUT_MOUSELEFT:
            return False
        pos = self.Global2Local()
        mx = msg[c4d.BFM_INPUT_X] + pos["x"]
        for a, b, i in self.hits:
            if a <= mx <= b:
                self.dlg.set_tab(i)
                break
        return True


class GridArea(gui.GeUserArea):
    def __init__(self, dlg):
        super(GridArea, self).__init__()
        self.dlg = dlg
        self.cache = BitmapCache()

    def GetMinSize(self):
        rows = (len(self.dlg.visible) + GRID_COLS - 1) // GRID_COLS
        return GRID_COLS * 84, max(1, rows) * TILE_H + TILE_GAP

    def _tile(self, p, w, h, sel):
        fill = GREEN if sel else THEME["card"]
        edge = GREEN_EDGE if sel else THEME["border"]
        r = Raster(w, h, THEME["bg"], _card_base(w, h, 7, fill, edge))
        pts, to_px = _curve_mapper(build_keys(p), 9, 7, w - 9, h - 22)
        guide = WHITE if sel else THEME["guide"]
        galpha = 0.22 if sel else 1.0
        for ref in (0.0, 1.0):
            r.dashed_h(to_px(0, ref)[1], 6, w - 6, guide, galpha)
        r.stroke(pts, 1.7, WHITE if sel else GREEN_TINT)
        return r.bitmap()

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        self.DrawSetPen(_vec(THEME["bg"]))
        self.DrawRectangle(x1, y1, x2, y2)
        cw = self.GetWidth() / float(GRID_COLS)
        tw, th = int(cw) - TILE_GAP, TILE_H - TILE_GAP
        if tw < 20:
            return
        self.DrawSetFont(c4d.FONT_DEFAULT)
        for slot, idx in enumerate(self.dlg.visible):
            c, row = slot % GRID_COLS, slot // GRID_COLS
            tx, ty = int(c * cw) + TILE_GAP // 2, row * TILE_H + TILE_GAP
            if ty > y2 or ty + th < y1:
                continue
            p = self.dlg.presets[idx]
            sel = idx == self.dlg.sel
            key = (_preset_key(p), tw, th, sel, THEME["bg"])
            _draw_bmp(self, self.cache.get(key, lambda: self._tile(p, tw, th, sel)), tx, ty)
            self.DrawSetTextCol(_vec(WHITE if sel else TEXT_SUB), c4d.COLOR_TRANS)
            self.DrawText(p.get("name", "?"), tx + tw // 2, ty + th - 11,
                          c4d.DRAWTEXT_HALIGN_CENTER | _VCENTER)

    def InputEvent(self, msg):
        if msg[c4d.BFM_INPUT_DEVICE] != c4d.BFM_INPUT_MOUSE or \
                msg[c4d.BFM_INPUT_CHANNEL] != c4d.BFM_INPUT_MOUSELEFT:
            return False
        pos = self.Global2Local()
        mx = msg[c4d.BFM_INPUT_X] + pos["x"]
        my = msg[c4d.BFM_INPUT_Y] + pos["y"] - TILE_GAP
        cw = max(1.0, self.GetWidth() / float(GRID_COLS))
        c, row = int(mx // cw), int(my // TILE_H)
        if 0 <= c < GRID_COLS and row >= 0 and my >= 0:
            slot = row * GRID_COLS + c
            if slot < len(self.dlg.visible):
                self.dlg.select_preset(self.dlg.visible[slot])
                if msg[c4d.BFM_INPUT_DOUBLECLICK]:
                    self.dlg.do_apply()
        return True


class PreviewArea(gui.GeUserArea):
    CYCLE = 1.8   # seconds per loop
    MOVE = 1.2    # seconds of motion inside the loop

    def __init__(self, dlg):
        super(PreviewArea, self).__init__()
        self.dlg = dlg
        self.t0 = time.time()
        self._key = None
        self._bmp = None
        self.layout = None

    def restart(self):
        self.t0 = time.time()

    def GetMinSize(self):
        return 290, 200

    def _build(self, keys, w, h):
        r = Raster(w, h, THEME["bg"], _card_base(w, h, 8, THEME["card"], THEME["border"]))
        gx1, gy1, gx2, gy2 = 14, 34, w - 14, h - 40
        for i in range(1, 8):
            r.line_v(gx1 + i * (gx2 - gx1) / 8.0, gy1 - 4, gy2 + 4, THEME["guide"], 0.45)
        pts, to_px = _curve_mapper(keys, gx1, gy1, gx2, gy2)
        for ref in (0.0, 1.0):
            r.dashed_h(to_px(0, ref)[1], 10, w - 10, THEME["guide"], 1.0, 3, 4)
        r.stroke(pts, 6.0, GREEN_TINT, 0.16, step=0.6)
        r.stroke(pts, 2.4, GREEN_TINT)
        # value trail: where the object is at 15 equal time steps
        ty = h - 18
        vals = [eval_curve(keys, i / 14.0) for i in range(15)]
        lo, hi = min(0.0, min(vals)), max(1.0, max(vals))
        tx1, tx2 = 16, w - 16
        r.stroke([(tx1, ty), (tx2, ty)], 2.0, THEME["guide"])
        tpos = lambda v: tx1 + (v - lo) / (hi - lo) * (tx2 - tx1)
        for i, v in enumerate(vals):
            r.dot(tpos(v), ty, 2.4, GREEN_TINT, 0.25 + 0.6 * i / 14.0)
        self.layout = (to_px, tpos, ty)
        return r.bitmap()

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        w, h = self.GetWidth(), self.GetHeight()
        self.DrawSetPen(_vec(THEME["bg"]))
        self.DrawRectangle(0, 0, w, h)
        try:
            keys = build_keys(self.dlg.work)
        except Exception:
            return
        key = (_preset_key(self.dlg.work), w, h, THEME["bg"])
        if key != self._key:
            self._key = key
            self.layout = None
            try:
                self._bmp = self._build(keys, w, h)
            except Exception as e:
                print("[Ease Presets] draw error: %s" % e)
                self._bmp = None
        _draw_bmp(self, self._bmp, 0, 0)

        self.DrawSetFont(c4d.FONT_BOLD)
        self.DrawSetTextCol(_vec(TEXT), c4d.COLOR_TRANS)
        self.DrawText(self.dlg.work.get("name", ""), 14, 17, c4d.DRAWTEXT_HALIGN_LEFT | _VCENTER)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(_vec(TEXT_SUB), c4d.COLOR_TRANS)
        self.DrawText(_describe(self.dlg.work), w - 14, 17, c4d.DRAWTEXT_HALIGN_RIGHT | _VCENTER)

        if not self.layout:
            return
        to_px, tpos, ty = self.layout
        u = min(((time.time() - self.t0) % self.CYCLE) / self.MOVE, 1.0)
        v = eval_curve(keys, u)
        px, py = to_px(u, v)
        self.DrawSetPen(_vec(WHITE))
        _ellipse(self, px, py, 4)
        self.DrawSetPen(_vec(GREEN_TINT))
        _ellipse(self, tpos(v), ty, 6)


def _ellipse(area, x, y, r):
    x, y, r = int(x), int(y), int(r)
    try:
        area.DrawEllipseFill(x - r, y - r, x + r, y + r)
    except Exception:
        area.DrawRectangle(x - r, y - r, x + r, y + r)


def _describe(p):
    kind = p.get("kind")
    q = p.get("params", {})
    rev = " (rev)" if p.get("mirror") else ""
    if kind == "bezier":
        return "%.2f, %.2f, %.2f, %.2f%s" % (q["x1"], q["y1"], q["x2"], q["y2"], rev)
    if kind in ("bounce", "elastic"):
        return "%s x%d%s" % (kind.capitalize(), int(round(q.get("count", 1))), rev)
    return "Curve, %d keys%s" % (len(q.get("keys", [])), rev)


class GreenButton(gui.GeUserArea):
    def __init__(self, label, callback):
        super(GreenButton, self).__init__()
        self.label = label
        self.callback = callback
        self.cache = BitmapCache()
        self.pressed = False

    def GetMinSize(self):
        return 120, 36

    def _build(self, w, h, pressed):
        r = Raster(w, h, THEME["bg"])
        r.round_rect(0, 0, w, h, 6, _lighten(GREEN, -0.04) if pressed else GREEN)
        return r.bitmap()

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        w, h = self.GetWidth(), self.GetHeight()
        p = self.pressed
        _draw_bmp(self, self.cache.get((w, h, p, THEME["bg"]), lambda: self._build(w, h, p)), 0, 0)
        self.DrawSetFont(c4d.FONT_BOLD)
        self.DrawSetTextCol(_vec(WHITE), c4d.COLOR_TRANS)
        self.DrawText(self.label, w // 2, h // 2, c4d.DRAWTEXT_HALIGN_CENTER | _VCENTER)

    def InputEvent(self, msg):
        if msg[c4d.BFM_INPUT_DEVICE] == c4d.BFM_INPUT_MOUSE and \
                msg[c4d.BFM_INPUT_CHANNEL] == c4d.BFM_INPUT_MOUSELEFT:
            self.pressed = True
            self.Redraw()
            try:
                self.callback()
            finally:
                self.pressed = False
                self.Redraw()
            return True
        return False


# dialog ids
ID_SCROLL = 1000
ID_GRID = 1001
ID_PREVIEW = 1002
ID_TABS = 1004
ID_RIGHT = 1005
ID_X1, ID_Y1, ID_X2, ID_Y2 = 1010, 1011, 1012, 1013
ID_AMP, ID_COUNT, ID_DECAY = 1020, 1021, 1022
ID_GRP_BEZ, ID_GRP_DYN = 1025, 1026
ID_MIRROR = 1030
ID_REPLACE = 1031
ID_APPLY = 1040
ID_SAVE_SEL = 1041
ID_SAVE_CUR = 1042
ID_DELETE = 1043
ID_FOLDER = 1044
ID_STATUS = 1050

BEZIER_IDS = {ID_X1: "x1", ID_Y1: "y1", ID_X2: "x2", ID_Y2: "y2"}
DYN_IDS = {ID_AMP: "amplitude", ID_COUNT: "count", ID_DECAY: "decay"}


class EaseDialog(gui.GeDialog):
    def __init__(self):
        super(EaseDialog, self).__init__()
        _init_theme()
        self.store = PresetStore()
        self.presets = self.store.all()
        self.tab = 0
        self.visible = list(range(len(self.presets)))
        self.sel = 0
        self.work = copy.deepcopy(self.presets[0])
        self.tabbar = TabBar(self)
        self.grid = GridArea(self)
        self.preview = PreviewArea(self)
        self.apply_btn = GreenButton("Apply to selected keys", self.do_apply)

    # ---------------------------------------------------------------- layout
    def _label(self, text):
        self.AddStaticText(0, c4d.BFH_LEFT, 74, 0, text, 0)

    def CreateLayout(self):
        self.SetTitle(PLUGIN_NAME)
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)

        self.AddUserArea(ID_TABS, c4d.BFH_SCALEFIT)
        self.AttachUserArea(self.tabbar, ID_TABS)

        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 2, 1)
        self.GroupBorderSpace(12, 10, 14, 10)
        self.GroupSpace(14, 0)

        # left: preset tiles
        self.ScrollGroupBegin(ID_SCROLL, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
                              c4d.SCROLLGROUP_VERT | c4d.SCROLLGROUP_AUTOVERT, 440, 420)
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0)
        self.AddUserArea(ID_GRID, c4d.BFH_SCALEFIT | c4d.BFV_TOP)
        self.AttachUserArea(self.grid, ID_GRID)
        self.GroupEnd()
        self.GroupEnd()

        # right: preview + settings
        self.GroupBegin(ID_RIGHT, c4d.BFH_RIGHT | c4d.BFV_SCALEFIT, 1, 0, "", 0, 290)
        self.GroupSpace(0, 8)
        self.AddUserArea(ID_PREVIEW, c4d.BFH_SCALEFIT | c4d.BFV_TOP)
        self.AttachUserArea(self.preview, ID_PREVIEW)

        self.GroupBegin(ID_GRP_BEZ, c4d.BFH_SCALEFIT, 2, 0)
        self.GroupSpace(6, 4)
        for gid, label in ((ID_X1, "Out time"), (ID_Y1, "Out value"),
                           (ID_X2, "In time"), (ID_Y2, "In value")):
            self._label(label)
            self.AddEditSlider(gid, c4d.BFH_SCALEFIT, 80)
        self.GroupEnd()

        self.GroupBegin(ID_GRP_DYN, c4d.BFH_SCALEFIT, 2, 0)
        self.GroupSpace(6, 4)
        self._label("Amplitude")
        self.AddEditSlider(ID_AMP, c4d.BFH_SCALEFIT, 80)
        self._label("Count")
        self.AddEditNumberArrows(ID_COUNT, c4d.BFH_LEFT, 70)
        self._label("Decay")
        self.AddEditSlider(ID_DECAY, c4d.BFH_SCALEFIT, 80)
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 1, 0)
        self.AddCheckbox(ID_MIRROR, c4d.BFH_LEFT, 0, 0, "Reverse")
        self.AddCheckbox(ID_REPLACE, c4d.BFH_LEFT, 0, 0, "Replace keys between")
        self.GroupEnd()

        self.AddUserArea(ID_APPLY, c4d.BFH_SCALEFIT)
        self.AttachUserArea(self.apply_btn, ID_APPLY)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 1)
        self.GroupSpace(6, 0)
        self.AddButton(ID_SAVE_SEL, c4d.BFH_SCALEFIT, 0, 0, "Save from keys")
        self.AddButton(ID_SAVE_CUR, c4d.BFH_SCALEFIT, 0, 0, "Save as...")
        self.AddButton(ID_DELETE, c4d.BFH_SCALEFIT, 0, 0, "Delete")
        self.GroupEnd()
        self.AddButton(ID_FOLDER, c4d.BFH_SCALEFIT, 0, 0, "Show preset file")
        self.GroupEnd()

        self.GroupEnd()

        self.AddSeparatorH(0, c4d.BFH_SCALEFIT)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderSpace(14, 2, 14, 4)
        self.AddStaticText(ID_STATUS, c4d.BFH_SCALEFIT, 0, 0, "", c4d.BORDER_NONE)
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(ID_REPLACE, True)
        self._filter()
        self.select_preset(self.sel)
        self._status("Select keys in the Timeline, then click a preset.")
        self.SetTimer(33)
        return True

    def Timer(self, msg):
        self.preview.Redraw()

    # --------------------------------------------------------------- helpers
    def _status(self, text):
        self.SetString(ID_STATUS, text)

    def _filter(self):
        name = TABS[self.tab]
        self.visible = [i for i, p in enumerate(self.presets)
                        if name == "All" or preset_category(p) == name]

    def set_tab(self, i):
        self.tab = i
        self._filter()
        self.tabbar.Redraw()
        self.grid.LayoutChanged()
        self.LayoutChanged(ID_SCROLL)
        self.grid.Redraw()
        if not self.visible and TABS[i] == "My Presets":
            self._status("No saved presets yet - select keys and press 'Save from keys'.")

    def _sync_fields(self):
        p = self.work
        params = p.get("params", {})
        kind = p.get("kind")
        dyn = kind in ("bounce", "elastic")
        for gid, key in BEZIER_IDS.items():
            val = params.get(key, 0.0) if kind == "bezier" else 0.0
            self.SetFloat(gid, val, -1.0, 2.0, 0.01, c4d.FORMAT_FLOAT, -5.0, 5.0)
        self.SetFloat(ID_AMP, params.get("amplitude", 0.35) if dyn else 0.0, 0.0, 1.0, 0.01,
                      c4d.FORMAT_FLOAT, 0.0, 3.0)
        self.SetInt(ID_COUNT, int(round(params.get("count", 3))) if dyn else 1, 1, 20)
        self.SetFloat(ID_DECAY, params.get("decay", 0.16) if dyn else 0.0, 0.01, 1.0, 0.01)
        self.HideElement(ID_GRP_BEZ, kind != "bezier")
        self.HideElement(ID_GRP_DYN, not dyn)
        self.LayoutChanged(ID_RIGHT)
        self.SetBool(ID_MIRROR, bool(p.get("mirror")))
        self.Enable(ID_DELETE, not self.presets[self.sel].get("builtin"))

    def select_preset(self, idx):
        self.presets = self.store.all()
        self.sel = max(0, min(idx, len(self.presets) - 1))
        self.work = copy.deepcopy(self.presets[self.sel])
        self._sync_fields()
        self.preview.restart()
        self.grid.Redraw()
        self.preview.Redraw()

    def _refresh_list(self, select=None):
        self.presets = self.store.all()
        self._filter()
        self.grid.LayoutChanged()
        self.LayoutChanged(ID_SCROLL)
        self.select_preset(self.sel if select is None else select)

    def _ask_name(self, default):
        name = gui.InputDialog("Preset name", default)
        if not name:
            return None
        return name.strip() or None

    def do_apply(self):
        doc = c4d.documents.GetActiveDocument()
        n, tracks, mode, worst = apply_preset(doc, self.work, self.GetBool(ID_REPLACE))
        if n == 0:
            self._status("Nothing applied - select 2+ keys, or an animated object.")
            return
        text = "%s -> %d segment(s) on %d track(s) [%s]" % (
            self.work.get("name", ""), n, tracks, mode)
        if worst > 0.02:
            text += "  WARNING: C4D shaped the curve %.0f%% off the preset" % (worst * 100)
            print("[Ease Presets] %s" % text)
        self._status(text)

    # --------------------------------------------------------------- events
    def Command(self, cid, msg):
        if cid in BEZIER_IDS:
            self.work.setdefault("params", {})[BEZIER_IDS[cid]] = self.GetFloat(cid)
            self.preview.Redraw()
        elif cid in DYN_IDS:
            if cid == ID_COUNT:
                val = max(1, int(round(self.GetFloat(cid))))
            else:
                val = self.GetFloat(cid)
            self.work.setdefault("params", {})[DYN_IDS[cid]] = val
            self.preview.Redraw()
        elif cid == ID_MIRROR:
            self.work["mirror"] = self.GetBool(ID_MIRROR)
            self.preview.restart()
            self.preview.Redraw()
        elif cid == ID_SAVE_SEL:
            doc = c4d.documents.GetActiveDocument()
            preset, err = capture_from_selection(doc)
            if err:
                gui.MessageDialog(err)
                return True
            name = self._ask_name("My Curve %d" % (len(self.store.user) + 1))
            if name:
                preset["name"] = name
                self.store.add(preset)
                self._refresh_list(len(self.store.all()) - 1)
                self._status("Saved '%s' from selected keys." % name)
        elif cid == ID_SAVE_CUR:
            name = self._ask_name(self.work.get("name", "Preset") + " copy")
            if name:
                preset = copy.deepcopy(self.work)
                preset["name"] = name
                self.store.add(preset)
                self._refresh_list(len(self.store.all()) - 1)
                self._status("Saved '%s'." % name)
        elif cid == ID_DELETE:
            p = self.presets[self.sel]
            if not p.get("builtin") and gui.QuestionDialog("Delete preset '%s'?" % p.get("name")):
                self.store.remove(p)
                self._refresh_list(max(0, self.sel - 1))
        elif cid == ID_FOLDER:
            if not os.path.isfile(self.store.path):
                self.store.save()
            storage.ShowInFinder(self.store.path, False)
        return True


class EaseCommand(plugins.CommandData):
    dialog = None

    def _dlg(self):
        if self.dialog is None:
            self.dialog = EaseDialog()
        return self.dialog

    def Execute(self, doc):
        return self._dlg().Open(c4d.DLG_TYPE_ASYNC, pluginid=PLUGIN_ID,
                                defaultw=780, defaulth=600)

    def RestoreLayout(self, sec_ref):
        return self._dlg().Restore(pluginid=PLUGIN_ID, secret=sec_ref)


def _load_icon():
    bmp = bitmaps.BaseBitmap()
    path = os.path.join(os.path.dirname(__file__), "res", "icon.png")
    if os.path.isfile(path) and bmp.InitWith(path)[0] == c4d.IMAGERESULT_OK:
        return bmp
    return None


if __name__ == "__main__":
    plugins.RegisterCommandPlugin(id=PLUGIN_ID, str=PLUGIN_NAME, info=0,
                                  icon=_load_icon(),
                                  help="Animation curve preset library: apply easing, bounce and elastic presets to keys",
                                  dat=EaseCommand())
