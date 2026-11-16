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

User presets are stored as JSON in the Cinema 4D prefs folder
(ease_presets_user.json) so they can be shared.

The window is built from Cinema 4D's own gadgets and colour scheme: it follows
the interface theme and reflows when resized or docked.

Tested in Cinema 4D 2027 (Python 3.11).
"""

import c4d
import os
import json
import math
import copy
import time
from c4d import gui, plugins, storage, bitmaps

# Unique ID registered with Maxon (Plugin Cafe, label "EasePresets").
PLUGIN_ID = 1074269
PLUGIN_NAME = "Ease Presets"
PLUGIN_VERSION = "2.0"
USER_FILE = "ease_presets_user.json"

EPS = 1e-6

# Limits for anything that comes from the preset file or the sliders.
MAX_COUNT = 20            # bounces / overshoots
MAX_CURVE_KEYS = 200      # keys in a saved multi-key curve
MAX_PRESETS = 500         # user presets read from the file
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_NAME = 64
VALUE_LIMIT = 100.0       # normalised values / tangents

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


def _clamp_count(count):
    return max(1, min(MAX_COUNT, int(round(count))))


def bounce_keys(amplitude, count, decay):
    """Ball-drop style bounce settling on the target value.
    amplitude = first bounce height (fraction of the move),
    count = number of bounces, decay = height of the last bounce relative
    to the first (spread evenly, so every bounce stays visible)."""
    count = _clamp_count(count)
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
        if half <= EPS:
            # no height left (amplitude 0): the remaining bounces are flat
            break
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
    count = _clamp_count(count)
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


# ---------------------------- validation ------------------------------------
# Preset files get copied between machines, so nothing read from disk is
# trusted: every number is checked and clamped before it reaches the scene.

def _num(value, lo, hi, default=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if not math.isfinite(value):
        return default
    return min(max(value, lo), hi)


def clean_name(name, default="Preset"):
    if not isinstance(name, str):
        return default
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:MAX_NAME] or default


def _clean_pair(value, lo_t, hi_t):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    t = _num(value[0], lo_t, hi_t)
    v = _num(value[1], -VALUE_LIMIT, VALUE_LIMIT)
    if t is None or v is None:
        return None
    return [t, v]


def sanitize_preset(raw):
    """Returns a clean copy of a preset dict, or None when it can't be used."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    params = raw.get("params")
    if not isinstance(params, dict):
        return None
    out = {"name": clean_name(raw.get("name")), "kind": kind,
           "mirror": bool(raw.get("mirror")), "builtin": False}

    if kind == "bezier":
        x1, x2 = _num(params.get("x1"), 0.0, 1.0), _num(params.get("x2"), 0.0, 1.0)
        y1 = _num(params.get("y1"), -VALUE_LIMIT, VALUE_LIMIT)
        y2 = _num(params.get("y2"), -VALUE_LIMIT, VALUE_LIMIT)
        if None in (x1, y1, x2, y2):
            return None
        out["params"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    elif kind in ("bounce", "elastic"):
        amplitude = _num(params.get("amplitude"), 0.0, 3.0)
        count = _num(params.get("count"), 1, MAX_COUNT)
        decay = _num(params.get("decay"), 0.01, 1.0)
        if None in (amplitude, count, decay):
            return None
        out["params"] = {"amplitude": amplitude, "count": int(round(count)), "decay": decay}
    elif kind == "curve":
        keys = params.get("keys")
        if not isinstance(keys, list) or not 2 <= len(keys) <= MAX_CURVE_KEYS:
            return None
        clean = []
        for k in keys:
            if not isinstance(k, dict):
                return None
            u = _num(k.get("u"), 0.0, 1.0)
            v = _num(k.get("v"), -VALUE_LIMIT, VALUE_LIMIT)
            lt = _clean_pair(k.get("lt"), -1.0, 0.0)
            rt = _clean_pair(k.get("rt"), 0.0, 1.0)
            if None in (u, v, lt, rt):
                return None
            if clean and u <= clean[-1]["u"] + EPS:
                return None            # keys must move forward in time
            clean.append({"u": u, "v": v, "lt": lt, "rt": rt})
        if clean[0]["u"] > EPS or abs(clean[-1]["u"] - 1.0) > EPS:
            return None
        out["params"] = {"keys": clean}
    else:
        return None
    return out


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

# Key selection of the four Timeline windows, Dope Sheet and F-Curve mode.
SELECT_BITS = [getattr(c4d, name) for name in (
    "NBIT_TL1_SELECT", "NBIT_TL2_SELECT", "NBIT_TL3_SELECT", "NBIT_TL4_SELECT",
    "NBIT_TL1_FCSELECT", "NBIT_TL2_FCSELECT", "NBIT_TL3_FCSELECT", "NBIT_TL4_FCSELECT")
    if hasattr(c4d, name)]


def key_selected(key):
    for bit in SELECT_BITS:
        if key.GetNBit(bit):
            return True
    return False


def _walk_objects(first):
    """Objects and their tags, depth first, without recursion."""
    stack = [first] if first else []
    while stack:
        op = stack.pop()
        while op:
            yield op
            for tag in op.GetTags():
                yield tag
            down = op.GetDown()
            if down:
                stack.append(down)
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
        if track.GetTrackCategory() == c4d.CTRACK_CATEGORY_VALUE and track.GetCurve() is not None:
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
    """Returns (shape error, keys removed in between), or None if skipped."""
    ka, kb = _find(curve, t_a), _find(curve, t_b)
    if ka is None or kb is None:
        return None
    t0, t1 = t_a.Get(), t_b.Get()
    dt = t1 - t0
    if dt <= EPS:
        return None
    v0, v1 = ka.GetValue(), kb.GetValue()
    dv = v1 - v0

    removed = 0
    if replace_between or len(keys) > 2:
        for i in reversed(range(curve.GetKeyCount())):
            t = curve.GetKey(i).GetTime().Get()
            if t0 + EPS < t < t1 - EPS:
                curve.DelKey(i)
                removed += 1

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
    if ka is None or kb is None:
        return None
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
        return 0.0, removed
    err = 0.0
    for i in range(1, 16):
        u = i / 16.0
        got = curve.GetValue(c4d.BaseTime(t0 + u * dt))
        err = max(err, abs((got - v0) / dv - eval_curve(keys, u)))
    return err, removed


def apply_preset(doc, preset, replace_between=True):
    """Returns (segments, tracks, mode, worst shape error, keys removed)."""
    keys = build_keys(preset)
    jobs, mode = collect_jobs(doc)
    if not jobs:
        return 0, 0, mode, 0.0, 0
    count = 0
    worst = 0.0
    removed = 0
    doc.StartUndo()
    try:
        for track, pairs in jobs:
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, track)
            curve = track.GetCurve()
            for t_a, t_b in pairs:
                res = apply_segment(doc, curve, t_a, t_b, keys, replace_between)
                if res is not None:
                    count += 1
                    worst = max(worst, res[0])
                    removed += res[1]
            curve.SetKeyDirty()
    finally:
        # always close the undo step, or one failed track breaks Ctrl+Z
        doc.EndUndo()
        c4d.EventAdd()
    return count, len(jobs), mode, worst, removed


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
            if i1 - i0 + 1 > MAX_CURVE_KEYS:
                return None, "That is %d keys - a preset holds up to %d." % (
                    i1 - i0 + 1, MAX_CURVE_KEYS)
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
                preset = {"name": "", "kind": "bezier", "mirror": False, "params": p}
            else:
                preset = {"name": "", "kind": "curve", "mirror": False,
                          "params": {"keys": keys}}
            preset = sanitize_preset(preset)
            if preset is None:
                return None, "These keys can't be stored as a preset (values out of range)."
            return preset, None
    return None, "Select at least 2 keys on one curve (Timeline / F-Curve)."


# --------------------------------------------------------------------------
# Preset storage
# --------------------------------------------------------------------------

class PresetStore(object):
    def __init__(self):
        self.path = os.path.join(storage.GeGetC4DPath(c4d.C4D_PATH_PREFS), USER_FILE)
        self.user = []
        self.skipped = 0
        self.load()

    def load(self):
        """Reads the user file. Returns None, or a message when it failed."""
        self.user = []
        self.skipped = 0
        if not os.path.isfile(self.path):
            return None
        try:
            if os.path.getsize(self.path) > MAX_FILE_BYTES:
                raise ValueError("file is larger than %d KB" % (MAX_FILE_BYTES // 1024))
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("presets", []) if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("no preset list in the file")
            for raw in items[:MAX_PRESETS]:
                preset = sanitize_preset(raw)
                if preset is None:
                    self.skipped += 1
                else:
                    self.user.append(preset)
            self.skipped += max(0, len(items) - MAX_PRESETS)
        except Exception as e:
            print("[Ease Presets] Could not read %s: %s" % (self.path, e))
            return "Could not read the preset file: %s" % e
        return None

    def save(self):
        """Writes to a temp file first, so a crash can't leave half a file."""
        data = {"version": 1, "presets": [
            {k: v for k, v in p.items() if k != "builtin"} for p in self.user]}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            gui.MessageDialog("Could not save presets:\n%s" % e)
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def all(self):
        return BUILTIN_PRESETS + self.user

    def add(self, preset):
        preset = sanitize_preset(preset)
        if preset is None or len(self.user) >= MAX_PRESETS:
            return False
        self.user.append(preset)
        return self.save()

    def remove(self, preset):
        # identity, not equality: two presets may hold the same values
        for i, p in enumerate(self.user):
            if p is preset:
                del self.user[i]
                return self.save()
        return False

    def rename(self, preset, name):
        preset["name"] = clean_name(name)
        return self.save()


# --------------------------------------------------------------------------
# UI - Cinema 4D gadgets and interface colours only
# --------------------------------------------------------------------------

def _color(name, fallback=c4d.COLOR_BG):
    return getattr(c4d, name, fallback)


# Interface colour ids: they are resolved by C4D at draw time, so the window
# follows the active scheme (and any colour the user changed in Preferences).
COL_BG = c4d.COLOR_BG
COL_TILE = _color("COLOR_BG_DARK1")
COL_TILE_SEL = _color("COLOR_QUICKTAB_BG_ACTIVE", c4d.COLOR_BGFOCUS)
COL_TEXT = c4d.COLOR_TEXT
COL_TEXT_STRONG = _color("COLOR_TITLE_TEXT", c4d.COLOR_TEXT)
COL_TEXT_SEL = _color("COLOR_QUICKTAB_TEXT_ACTIVE", c4d.COLOR_TEXT)
COL_GRID = _color("COLOR_CTIMELINE_GRIDMAIN", c4d.COLOR_EDGEDK)
COL_CURVE = _color("COLOR_CTIMELINE_GENERAL", c4d.COLOR_TEXT)
COL_CURVE_SEL = _color("COLOR_CTIMELINE_FCHIGHLIGHT", c4d.COLOR_TEXT)
COL_TANGENT = _color("COLOR_CTIMELINE_FCKEY", c4d.COLOR_TEXT)
COL_ACCENT = _color("COLOR_CTIMELINE_SELECTION", c4d.COLOR_TEXT_SELECTED)

_VCENTER = getattr(c4d, "DRAWTEXT_VALIGN_CENTER", 0)
_LINE = getattr(c4d, "LINESTYLE_NORMAL", 0)
_DASH = getattr(c4d, "LINESTYLE_DASHED", 0)

TABS = ["All", "Ease", "Back", "Dynamics", "My Presets"]

TILE_MIN_W = 92      # tiles never get narrower; columns are added / removed
TILE_H = 72
TILE_GAP = 4
TILE_LABEL_H = 20
SIDE_BY_SIDE_W = 600  # dialog width where the settings move beside the grid
PREVIEW_MIN = (140, 90)   # smallest chart
# Share of the window for (tiles, settings) until the divider is dragged.
SPLIT_SIDE = (62.0, 38.0)      # side by side: widths
SPLIT_STACKED = (40.0, 60.0)   # stacked: heights
PREF_SPLIT_SIDE, PREF_SPLIT_STACKED = 10, 11   # ids in the plugin's preferences
TAB_ROWS = ((330, 3), (210, 2), (130, 1))   # below this width: tabs per row


def preset_category(p):
    if not p.get("builtin"):
        return "My Presets"
    if p.get("kind") in ("bounce", "elastic"):
        return "Dynamics"
    if p.get("name", "").startswith("Back") or p.get("name") in ("Anticipate", "Snap"):
        return "Back"
    return "Ease"


def _describe(p):
    kind = p.get("kind")
    q = p.get("params", {})
    rev = " (rev)" if p.get("mirror") else ""
    if kind == "bezier":
        return "%.2f, %.2f, %.2f, %.2f%s" % (q.get("x1", 0), q.get("y1", 0),
                                             q.get("x2", 0), q.get("y2", 0), rev)
    if kind in ("bounce", "elastic"):
        return "%s x%d%s" % (kind.capitalize(), int(round(q.get("count", 1))), rev)
    return "Curve, %d keys%s" % (len(q.get("keys", [])), rev)


def _curve_mapper(keys, x1, y1, x2, y2, handles=False):
    """Returns to_px(u, v) that fits the curve (incl. overshoot, and the
    tangent handles when they are drawn too) in a rect."""
    pts = sample_curve(keys, 16)
    if handles:
        for p0, p1, p2, p3 in _segments(keys):
            pts.extend((p1, p2))
    vmin = min(0.0, min(p[1] for p in pts))
    vmax = max(1.0, max(p[1] for p in pts))
    span = max(vmax - vmin, EPS)

    def to_px(u, v):
        return (x1 + u * (x2 - x1), y2 - (v - vmin) / span * (y2 - y1))
    return to_px


def _draw_curve(area, keys, to_px, width):
    """The preset as real bezier segments: anti-aliased, sharp on HiDPI."""
    for p0, p1, p2, p3 in _segments(keys):
        a, b, c, d = to_px(*p0), to_px(*p1), to_px(*p2), to_px(*p3)
        area.DrawBezierLine([a[0], a[1]], [b[0], b[1], c[0], c[1], d[0], d[1]],
                            False, width, _LINE)


def _draw_hline(area, x1, x2, y, style=_LINE):
    area.DrawBezierLine([float(x1), float(y)],
                        [float(x1), float(y), float(x2), float(y), float(x2), float(y)],
                        False, 1.0, style)


def _draw_dot(area, x, y, r):
    area.DrawRoundedRectangle(int(round(x - r)), int(round(y - r)),
                              int(round(x + r)), int(round(y + r)), r, r)


def _fit_text(area, text, width):
    if area.DrawGetTextWidth(text) <= width:
        return text
    while len(text) > 1 and area.DrawGetTextWidth(text + "...") > width:
        text = text[:-1]
    return text + "..."


_reported = set()


def _report(where, error):
    """Draw code runs many times a second - print each problem once."""
    key = (where, repr(error))
    if key not in _reported:
        _reported.add(key)
        print("[Ease Presets] %s: %r" % (where, error))


class GridArea(gui.GeUserArea):
    """Preset tiles. The column count follows the width."""

    def __init__(self, dlg):
        super(GridArea, self).__init__()
        self.dlg = dlg
        self.cols = 4

    def rows(self):
        return max(1, (len(self.dlg.visible) + self.cols - 1) // self.cols)

    def GetMinSize(self):
        return TILE_MIN_W, self.rows() * TILE_H + TILE_GAP

    def Sized(self, w, h):
        # Hysteresis: the scroll bar showing up narrows the area a little,
        # which must not flip the column count back and forth.
        cols = self.cols
        if w < cols * TILE_MIN_W:
            cols = max(1, w // TILE_MIN_W)
        elif w >= (cols + 1) * TILE_MIN_W + 20:
            cols = max(1, w // TILE_MIN_W)
        if cols != self.cols:
            self.cols = cols
            self.dlg.defer("scroll")
        self.dlg.defer("mode")

    def _tile_rect(self, slot):
        cw = self.GetWidth() / float(self.cols)
        c, row = slot % self.cols, slot // self.cols
        x1 = int(c * cw) + TILE_GAP
        x2 = int((c + 1) * cw)
        y1 = row * TILE_H + TILE_GAP
        return x1, y1, x2, y1 + TILE_H - TILE_GAP

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        self.DrawSetPen(COL_BG)
        self.DrawRectangle(x1, y1, x2, y2)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        for slot, idx in enumerate(self.dlg.visible):
            tx1, ty1, tx2, ty2 = self._tile_rect(slot)
            if ty1 > y2 or ty2 < y1 or tx2 - tx1 < 24:
                continue
            p = self.dlg.presets[idx]
            sel = idx == self.dlg.sel
            self.DrawSetPen(COL_TILE_SEL if sel else COL_TILE)
            self.DrawRoundedRectangle(tx1, ty1, tx2, ty2, 4, 4)
            try:
                keys = build_keys(p)
                to_px = _curve_mapper(keys, tx1 + 9, ty1 + 8, tx2 - 9, ty2 - TILE_LABEL_H - 3)
                if not sel:
                    self.DrawSetPen(COL_GRID)
                    for ref in (0.0, 1.0):
                        _draw_hline(self, tx1 + 5, tx2 - 5, to_px(0, ref)[1], _DASH)
                self.DrawSetPen(COL_CURVE_SEL if sel else COL_CURVE)
                _draw_curve(self, keys, to_px, 1.5)
            except Exception as e:
                _report("tile", e)
            self.DrawSetTextCol(COL_TEXT_SEL if sel else COL_TEXT, c4d.COLOR_TRANS)
            self.DrawText(_fit_text(self, p.get("name", "?"), tx2 - tx1 - 8),
                          (tx1 + tx2) // 2, ty2 - TILE_LABEL_H // 2 - 1,
                          c4d.DRAWTEXT_HALIGN_CENTER | _VCENTER)

    def _slot_at(self, mx, my):
        for slot in range(len(self.dlg.visible)):
            tx1, ty1, tx2, ty2 = self._tile_rect(slot)
            if tx1 <= mx <= tx2 and ty1 <= my <= ty2:
                return slot
        return None

    def InputEvent(self, msg):
        if msg[c4d.BFM_INPUT_DEVICE] != c4d.BFM_INPUT_MOUSE:
            return False
        channel = msg[c4d.BFM_INPUT_CHANNEL]
        if channel not in (c4d.BFM_INPUT_MOUSELEFT, c4d.BFM_INPUT_MOUSERIGHT):
            return False
        pos = self.Global2Local()
        slot = self._slot_at(msg[c4d.BFM_INPUT_X] + pos["x"], msg[c4d.BFM_INPUT_Y] + pos["y"])
        if slot is None:
            return True
        self.dlg.select_preset(self.dlg.visible[slot])
        if channel == c4d.BFM_INPUT_MOUSERIGHT:
            self.dlg.context_menu()
        elif msg[c4d.BFM_INPUT_DOUBLECLICK]:
            self.dlg.do_apply()
        return True


class PreviewArea(gui.GeUserArea):
    """F-Curve style preview with a dot that plays the motion."""
    CYCLE = 1.8   # seconds per loop
    MOVE = 1.2    # seconds of motion inside the loop
    LOOPS = 3     # then it rests until the preset changes or it is clicked

    def __init__(self, dlg):
        super(PreviewArea, self).__init__()
        self.dlg = dlg
        self.t0 = time.time()

    def restart(self):
        self.t0 = time.time()
        self.dlg.defer(None)       # makes sure the timer runs

    def playing(self):
        return time.time() - self.t0 < self.CYCLE * self.LOOPS

    def GetMinSize(self):
        return PREVIEW_MIN

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        self.SetClippingRegion(x1, y1, x2, y2)
        w, h = self.GetWidth(), self.GetHeight()
        self.DrawSetPen(COL_BG)
        self.DrawRectangle(0, 0, w, h)
        self.DrawSetPen(COL_TILE)
        self.DrawRoundedRectangle(0, 0, w, h, 4, 4)
        work = self.dlg.work

        self.DrawSetFont(c4d.FONT_BOLD)
        self.DrawSetTextCol(COL_TEXT_STRONG, c4d.COLOR_TRANS)
        name = _fit_text(self, work.get("name", ""), w - 20)
        self.DrawText(name, 10, 14, c4d.DRAWTEXT_HALIGN_LEFT | _VCENTER)
        used = self.DrawGetTextWidth(name)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        info = _describe(work)
        if used + self.DrawGetTextWidth(info) + 34 < w:
            self.DrawSetTextCol(COL_TEXT, c4d.COLOR_TRANS)
            self.DrawText(info, w - 10, 14, c4d.DRAWTEXT_HALIGN_RIGHT | _VCENTER)

        try:
            keys = build_keys(work)
            gx1, gy1, gx2, gy2 = 12, 34, w - 12, h - 30
            if gy2 - gy1 < 20 or gx2 - gx1 < 40:
                return
            bezier = work.get("kind") == "bezier"
            to_px = _curve_mapper(keys, gx1, gy1, gx2, gy2, handles=bezier)

            self.DrawSetPen(COL_GRID)
            for i in range(0, 5):
                x = int(gx1 + i * (gx2 - gx1) / 4.0)
                self.DrawLine(x, gy1 - 6, x, gy2 + 6)
            for ref in (0.0, 1.0):
                _draw_hline(self, gx1 - 4, gx2 + 4, to_px(0, ref)[1])

            if bezier:
                # tangent handles, like the F-Curve editor shows them
                self.DrawSetPen(COL_TANGENT)
                for p0, p1, p2, p3 in _segments(keys):
                    for key, handle in ((p0, p1), (p3, p2)):
                        a, b = to_px(*key), to_px(*handle)
                        self.DrawBezierLine([a[0], a[1]], [a[0], a[1], b[0], b[1], b[0], b[1]],
                                            False, 1.0, _LINE)
                        _draw_dot(self, b[0], b[1], 2)

            self.DrawSetPen(COL_CURVE_SEL)
            _draw_curve(self, keys, to_px, 2.0)

            # value trail: where the object is at equal time steps
            ty = h - 14
            steps = 14
            vals = [eval_curve(keys, i / float(steps)) for i in range(steps + 1)]
            lo, hi = min(0.0, min(vals)), max(1.0, max(vals))
            self.DrawSetTextCol(COL_TEXT, c4d.COLOR_TRANS)
            self.DrawText("Motion", 10, ty, c4d.DRAWTEXT_HALIGN_LEFT | _VCENTER)
            tx1, tx2 = 10 + self.DrawGetTextWidth("Motion") + 12, w - 16
            if tx2 - tx1 < 40:
                tx1 = 16           # no room for the label's share: full width

            def tpos(v):
                return tx1 + (v - lo) / (hi - lo) * (tx2 - tx1)
            self.DrawSetPen(COL_GRID)
            self.DrawLine(tx1, ty, tx2, ty)
            self.DrawSetPen(COL_CURVE)
            for v in vals:
                _draw_dot(self, tpos(v), ty, 2)

            elapsed = time.time() - self.t0
            if elapsed >= self.CYCLE * self.LOOPS:
                u = 1.0
            else:
                u = min((elapsed % self.CYCLE) / self.MOVE, 1.0)
            v = eval_curve(keys, u)
            px, py = to_px(u, v)
            self.DrawSetPen(COL_ACCENT)
            _draw_dot(self, px, py, 4)
            _draw_dot(self, tpos(v), ty, 5)
        except Exception as e:
            _report("preview", e)

    def InputEvent(self, msg):
        if msg[c4d.BFM_INPUT_DEVICE] == c4d.BFM_INPUT_MOUSE and \
                msg[c4d.BFM_INPUT_CHANNEL] == c4d.BFM_INPUT_MOUSELEFT:
            self.restart()
            self.Redraw()
            return True
        return False


# dialog ids
ID_ROOT = 1000
ID_TABS = 1001
ID_MAIN = 1002
ID_SCROLL = 1003
ID_GRID = 1004
ID_PANEL = 1005
ID_PREVIEW = 1006
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
ID_RENAME = 1045
ID_RELOAD = 1046
ID_STATUS = 1050
ID_SPLIT = 1051
ID_TAB0 = 1100          # one id per tab

BEZIER_IDS = {ID_X1: "x1", ID_Y1: "y1", ID_X2: "x2", ID_Y2: "y2"}
DYN_IDS = {ID_AMP: "amplitude", ID_COUNT: "count", ID_DECAY: "decay"}

FIT = c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT


class EaseDialog(gui.GeDialog):
    def __init__(self):
        super(EaseDialog, self).__init__()
        self.store = PresetStore()
        self.presets = self.store.all()
        self.tab = 0
        self.visible = list(range(len(self.presets)))
        self.sel = 0
        self.work = copy.deepcopy(self.presets[0])
        self.replace = True
        self.side = True          # settings beside the grid (else below it)
        self.weights = {}         # divider positions, per layout
        self.load_prefs()
        self.grid = GridArea(self)
        self.preview = PreviewArea(self)
        self.tab_cols = len(TABS)
        self.tab_guis = []
        self._deferred = set()

    # ---------------------------------------------------------------- layout
    def CreateLayout(self):
        self.SetTitle(PLUGIN_NAME)

        self.MenuFlushAll()
        self.MenuSubBegin("Presets")
        self.MenuAddString(ID_APPLY, "Apply to Selected Keys")
        self.MenuAddSeparator()
        self.MenuAddString(ID_SAVE_SEL, "Save from Selected Keys...")
        self.MenuAddString(ID_SAVE_CUR, "Save Current Settings As...")
        self.MenuAddString(ID_RENAME, "Rename...")
        self.MenuAddString(ID_DELETE, "Delete")
        self.MenuAddSeparator()
        self.MenuAddString(ID_RELOAD, "Reload Preset File")
        self.MenuAddString(ID_FOLDER, "Show Preset File")
        self.MenuSubEnd()
        self.MenuFinished()

        self.GroupBegin(ID_ROOT, FIT, 1, 0)
        self.GroupBorderSpace(4, 4, 4, 2)
        self.GroupSpace(0, 4)

        self.GroupBegin(ID_TABS, c4d.BFH_SCALEFIT, 1, 0)
        self._build_tabs()
        self.GroupEnd()

        self.GroupBegin(ID_MAIN, FIT, 1, 0)
        self._build_main()
        self.GroupEnd()

        self.AddStaticText(ID_STATUS, c4d.BFH_SCALEFIT, 0, 0, "", c4d.BORDER_NONE)
        self.GroupEnd()
        return True

    def _build_tabs(self):
        """Attribute Manager style tabs: one pill per tab, as wide as its
        text, with gaps, wrapping to more rows when the window is narrow."""
        self.tab_guis = []
        self.GroupBegin(0, c4d.BFH_LEFT, 1, 0)
        self.GroupSpace(0, 4)
        for i, name in enumerate(TABS):
            if i % self.tab_cols == 0:
                # each row is its own group, so the pills stay packed
                if i:
                    self.GroupEnd()
                self.GroupBegin(0, c4d.BFH_LEFT, self.tab_cols, 0)
                self.GroupSpace(4, 0)
            bc = c4d.BaseContainer()
            bc.SetBool(c4d.QUICKTAB_BAR, False)
            bc.SetBool(c4d.QUICKTAB_SHOWSINGLE, True)
            bc.SetBool(c4d.QUICKTAB_NOMULTISELECT, True)
            tab = self.AddCustomGui(ID_TAB0 + i, c4d.CUSTOMGUI_QUICKTAB, "",
                                    c4d.BFH_LEFT, 0, 0, bc)
            if tab is not None:
                tab.AppendString(0, name, i == self.tab)
            self.tab_guis.append(tab)
        self.GroupEnd()
        self.GroupEnd()

    def _show_tab(self):
        for i, tab in enumerate(self.tab_guis):
            if tab is not None:
                tab.Select(0, i == self.tab)

    def _build_main(self):
        """Grid + settings. Rebuilt when the window crosses the width where
        the settings fit beside the grid."""
        side = self.side
        # A weighted group: C4D draws its own draggable divider between the
        # two sections, the same one its managers use.
        self.GroupBegin(ID_SPLIT, FIT, 2 if side else 1, 1 if side else 2, "",
                        c4d.BFV_GRIDGROUP_ALLOW_WEIGHTS)

        self.ScrollGroupBegin(ID_SCROLL, FIT,
                              c4d.SCROLLGROUP_VERT | c4d.SCROLLGROUP_AUTOVERT, 80, 60)
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0)
        self.AddUserArea(ID_GRID, c4d.BFH_SCALEFIT | c4d.BFV_TOP)
        self.AttachUserArea(self.grid, ID_GRID)
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(ID_PANEL, FIT, 1, 0)
        self.GroupBorderSpace(4 if side else 0, 0 if side else 4, 0, 0)
        self.GroupSpace(0, 4)
        # the chart takes all the room its section gets
        self.AddUserArea(ID_PREVIEW, FIT)
        self.AttachUserArea(self.preview, ID_PREVIEW)

        self.GroupBegin(ID_GRP_BEZ, c4d.BFH_SCALEFIT, 2, 0)
        for gid, label in ((ID_X1, "Out Time"), (ID_Y1, "Out Value"),
                           (ID_X2, "In Time"), (ID_Y2, "In Value")):
            self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, label, 0)
            self.AddEditSlider(gid, c4d.BFH_SCALEFIT, 70)
        self.GroupEnd()

        self.GroupBegin(ID_GRP_DYN, c4d.BFH_SCALEFIT, 2, 0)
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Amplitude", 0)
        self.AddEditSlider(ID_AMP, c4d.BFH_SCALEFIT, 70)
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Count", 0)
        self.AddEditNumberArrows(ID_COUNT, c4d.BFH_LEFT, 70)
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Decay", 0)
        self.AddEditSlider(ID_DECAY, c4d.BFH_SCALEFIT, 70)
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0)
        self.AddCheckbox(ID_MIRROR, c4d.BFH_LEFT, 0, 0, "Reverse")
        self.AddCheckbox(ID_REPLACE, c4d.BFH_LEFT, 0, 0, "Replace Keys Between")
        self.GroupEnd()

        self.AddButton(ID_APPLY, c4d.BFH_SCALEFIT, 0, 0, "Apply")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0)
        self.AddButton(ID_SAVE_SEL, c4d.BFH_SCALEFIT, 0, 0, "Save from Keys...")
        self.AddButton(ID_SAVE_CUR, c4d.BFH_SCALEFIT, 0, 0, "Save As...")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupEnd()
        self._load_weights()

    # ------------------------------------------------------- divider + prefs
    def _default_weights(self):
        bc = c4d.BaseContainer()
        if self.side:
            bc.SetInt32(c4d.GROUPWEIGHTS_PERCENT_W_CNT, 2)
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_W_VAL + 0, SPLIT_SIDE[0])
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_W_VAL + 1, SPLIT_SIDE[1])
            bc.SetInt32(c4d.GROUPWEIGHTS_PERCENT_H_CNT, 1)
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_H_VAL + 0, 100.0)
        else:
            bc.SetInt32(c4d.GROUPWEIGHTS_PERCENT_W_CNT, 1)
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_W_VAL + 0, 100.0)
            bc.SetInt32(c4d.GROUPWEIGHTS_PERCENT_H_CNT, 2)
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_H_VAL + 0, SPLIT_STACKED[0])
            bc.SetFloat(c4d.GROUPWEIGHTS_PERCENT_H_VAL + 1, SPLIT_STACKED[1])
        return bc

    def _load_weights(self):
        try:
            bc = self.weights.get(self.side) or self._default_weights()
            self.GroupWeightsLoad(ID_SPLIT, bc)
        except Exception as e:
            _report("weights", e)

    def Message(self, msg, result):
        # the user let go of the divider: remember where it is
        if msg.GetId() == c4d.BFM_WEIGHTS_CHANGED and \
                msg.GetInt32(c4d.BFM_WEIGHTS_CHANGED) == ID_SPLIT:
            try:
                bc = self.GroupWeightsSave(ID_SPLIT)
                if bc is not None:
                    self.weights[self.side] = bc
                    self.save_prefs()
            except Exception as e:
                _report("weights", e)
        return gui.GeDialog.Message(self, msg, result)

    def load_prefs(self):
        try:
            bc = plugins.GetWorldPluginData(PLUGIN_ID)
            if bc is not None:
                for side, pid in ((True, PREF_SPLIT_SIDE), (False, PREF_SPLIT_STACKED)):
                    sub = bc.GetContainer(pid)
                    if sub is not None and len(sub) > 0:
                        self.weights[side] = sub
        except Exception as e:
            _report("prefs", e)

    def save_prefs(self):
        try:
            bc = c4d.BaseContainer()
            for side, pid in ((True, PREF_SPLIT_SIDE), (False, PREF_SPLIT_STACKED)):
                if self.weights.get(side) is not None:
                    bc.SetContainer(pid, self.weights[side])
            plugins.SetWorldPluginData(PLUGIN_ID, bc, False)
        except Exception as e:
            _report("prefs", e)

    def _wanted_layout(self):
        """(settings beside the grid?, tabs per row) for the current width."""
        dim = self.GetItemDim(ID_ROOT)
        w = dim.get("w", 0) if dim else 0
        if w <= 0:
            return self.side, self.tab_cols
        # a band of 40 px keeps it from flipping while the edge is dragged
        side = w >= (SIDE_BY_SIDE_W - 40 if self.side else SIDE_BY_SIDE_W)
        cols = len(TABS)
        for limit, n in TAB_ROWS:
            if w < limit:
                cols = n
        return side, cols

    def _relayout(self, side, cols):
        if cols != self.tab_cols:
            self.tab_cols = cols
            self.LayoutFlushGroup(ID_TABS)
            self._build_tabs()
            self.LayoutChanged(ID_TABS)
        if side != self.side:
            self.side = side
            self.replace = self.GetBool(ID_REPLACE)
            self.LayoutFlushGroup(ID_MAIN)
            self._build_main()
            self.LayoutChanged(ID_MAIN)
            self.SetBool(ID_REPLACE, self.replace)
            self._sync_fields()

    def InitValues(self):
        self.SetBool(ID_REPLACE, self.replace)
        self._filter()
        self.select_preset(self.sel)
        if self.store.skipped:
            self._status("%d preset(s) in the preset file were not valid and were skipped."
                         % self.store.skipped)
        else:
            self._status("Select keys in the Timeline, then click a preset.")
        self.defer("mode")
        return True

    # ------------------------------------------------- deferred work + timer
    def defer(self, what):
        """Layout changes asked for while C4D is sizing or drawing the window
        are done a moment later, from the timer."""
        if what:
            self._deferred.add(what)
        self.SetTimer(40)

    def Timer(self, msg):
        todo, self._deferred = self._deferred, set()
        if "mode" in todo:
            side, cols = self._wanted_layout()
            if side != self.side or cols != self.tab_cols:
                self._relayout(side, cols)
                todo.discard("scroll")
        if "scroll" in todo:
            self.LayoutChanged(ID_SCROLL)
        if self.preview.playing():
            self.preview.Redraw()
        elif not self._deferred:
            self.preview.Redraw()      # the dot comes to rest
            self.SetTimer(0)           # nothing to animate: no idle redraws

    # --------------------------------------------------------------- helpers
    def _status(self, text):
        self.SetString(ID_STATUS, text)

    def _filter(self):
        name = TABS[self.tab]
        self.visible = [i for i, p in enumerate(self.presets)
                        if name == "All" or preset_category(p) == name]

    def _refresh_grid(self):
        self.grid.LayoutChanged()
        self.LayoutChanged(ID_SCROLL)
        self.grid.Redraw()

    def set_tab(self, i):
        self.tab = max(0, min(i, len(TABS) - 1))
        self._show_tab()
        self._filter()
        self._refresh_grid()
        if not self.visible and TABS[self.tab] == "My Presets":
            self._status("No saved presets yet - select keys and press 'Save from Keys'.")

    def _is_user(self):
        return 0 <= self.sel < len(self.presets) and not self.presets[self.sel].get("builtin")

    def _sync_fields(self):
        p = self.work
        params = p.get("params", {})
        kind = p.get("kind")
        dyn = kind in ("bounce", "elastic")
        for gid, key in BEZIER_IDS.items():
            val = params.get(key, 0.0) if kind == "bezier" else 0.0
            if key in ("x1", "x2"):
                # a handle can't leave the segment in time
                self.SetFloat(gid, val, 0.0, 1.0, 0.01, c4d.FORMAT_FLOAT, 0.0, 1.0)
            else:
                self.SetFloat(gid, val, -1.0, 2.0, 0.01, c4d.FORMAT_FLOAT, -5.0, 5.0)
        self.SetFloat(ID_AMP, params.get("amplitude", 0.35) if dyn else 0.0, 0.0, 1.0, 0.01,
                      c4d.FORMAT_FLOAT, 0.0, 3.0)
        self.SetInt32(ID_COUNT, int(round(params.get("count", 3))) if dyn else 1, 1, MAX_COUNT)
        self.SetFloat(ID_DECAY, params.get("decay", 0.16) if dyn else 0.01, 0.01, 1.0, 0.01)
        self.HideElement(ID_GRP_BEZ, kind != "bezier")
        self.HideElement(ID_GRP_DYN, not dyn)
        self.LayoutChanged(ID_PANEL)
        self.SetBool(ID_MIRROR, bool(p.get("mirror")))
        user = self._is_user()
        self.MenuInitString(ID_DELETE, user, False)
        self.MenuInitString(ID_RENAME, user, False)

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
        self._refresh_grid()
        self.select_preset(self.sel if select is None else select)

    def _ask_name(self, default):
        name = gui.InputDialog("Preset Name", default)
        if not name or not name.strip():
            return None
        return clean_name(name)

    def context_menu(self):
        """Right-click on a tile."""
        menu = c4d.BaseContainer()
        menu.SetString(ID_APPLY, "Apply")
        menu.SetString(ID_SAVE_CUR, "Save As...")
        if self._is_user():
            menu.SetString(0, "")
            menu.SetString(ID_RENAME, "Rename...")
            menu.SetString(ID_DELETE, "Delete")
        cid = gui.ShowPopupDialog(cd=None, bc=menu, x=c4d.MOUSEPOS, y=c4d.MOUSEPOS)
        if cid:
            self.Command(cid, c4d.BaseContainer())

    def do_apply(self):
        doc = c4d.documents.GetActiveDocument()
        if doc is None:
            return
        work = sanitize_preset(self.work)
        if work is None:
            self._status("These settings are out of range - nothing applied.")
            return
        try:
            n, tracks, mode, worst, removed = apply_preset(
                doc, work, self.GetBool(ID_REPLACE))
        except Exception as e:
            _report("apply", e)
            self._status("Apply failed: %s (Ctrl+Z restores the keys)" % e)
            return
        if n == 0:
            self._status("Nothing applied - select 2+ keys, or an animated object.")
            return
        text = "%s: %d segment(s) on %d track(s), %s" % (
            self.work.get("name", ""), n, tracks, mode)
        if removed:
            # never silent: these keys sat between the selected ones
            text += " - removed %d key%s in between" % (removed, "" if removed == 1 else "s")
        if worst > 0.02:
            text += " - C4D shaped the curve %.0f%% off the preset" % (worst * 100)
            print("[Ease Presets] %s" % text)
        self._status(text)

    # --------------------------------------------------------------- events
    def Command(self, cid, msg):
        if ID_TAB0 <= cid < ID_TAB0 + len(TABS):
            self.set_tab(cid - ID_TAB0)
        elif cid in BEZIER_IDS:
            self.work.setdefault("params", {})[BEZIER_IDS[cid]] = self.GetFloat(cid)
            self.preview.restart()
            self.preview.Redraw()
        elif cid in DYN_IDS:
            if cid == ID_COUNT:
                val = _clamp_count(self.GetInt32(cid))
            else:
                val = self.GetFloat(cid)
            self.work.setdefault("params", {})[DYN_IDS[cid]] = val
            self.preview.restart()
            self.preview.Redraw()
        elif cid == ID_MIRROR:
            self.work["mirror"] = self.GetBool(ID_MIRROR)
            self.preview.restart()
            self.preview.Redraw()
        elif cid == ID_REPLACE:
            self.replace = self.GetBool(ID_REPLACE)
        elif cid == ID_APPLY:
            self.do_apply()
        elif cid == ID_SAVE_SEL:
            doc = c4d.documents.GetActiveDocument()
            preset, err = capture_from_selection(doc)
            if err:
                gui.MessageDialog(err)
                return True
            name = self._ask_name("My Curve %d" % (len(self.store.user) + 1))
            if name:
                preset["name"] = name
                self._add(preset, "Saved '%s' from selected keys." % name)
        elif cid == ID_SAVE_CUR:
            name = self._ask_name(self.work.get("name", "Preset") + " copy")
            if name:
                preset = copy.deepcopy(self.work)
                preset["name"] = name
                self._add(preset, "Saved '%s'." % name)
        elif cid == ID_RENAME:
            if self._is_user():
                p = self.presets[self.sel]
                name = self._ask_name(p.get("name", ""))
                if name and self.store.rename(p, name):
                    self._refresh_list()
        elif cid == ID_DELETE:
            if self._is_user():
                p = self.presets[self.sel]
                if gui.QuestionDialog("Delete preset '%s'?" % p.get("name")):
                    self.store.remove(p)
                    self._refresh_list(max(0, self.sel - 1))
        elif cid == ID_RELOAD:
            err = self.store.load()
            self._refresh_list(0)
            if err:
                self._status(err)
            elif self.store.skipped:
                self._status("Reloaded: %d preset(s), %d not valid and skipped."
                             % (len(self.store.user), self.store.skipped))
            else:
                self._status("Reloaded: %d preset(s)." % len(self.store.user))
        elif cid == ID_FOLDER:
            if not os.path.isfile(self.store.path):
                self.store.save()
            storage.ShowInFinder(self.store.path, False)
        return True

    def _add(self, preset, done_text):
        if self.store.add(preset):
            if TABS[self.tab] not in ("All", "My Presets"):
                self.tab = TABS.index("My Presets")
                self._show_tab()
            self._refresh_list(len(self.store.all()) - 1)
            self._status(done_text)
        else:
            self._status("The preset was not saved.")


class EaseCommand(plugins.CommandData):
    dialog = None

    def _dlg(self):
        if self.dialog is None:
            self.dialog = EaseDialog()
        return self.dialog

    def Execute(self, doc):
        return self._dlg().Open(c4d.DLG_TYPE_ASYNC, pluginid=PLUGIN_ID,
                                defaultw=720, defaulth=480)

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
