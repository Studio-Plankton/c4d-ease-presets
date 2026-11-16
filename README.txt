EASE PRESETS for Cinema 4D (v1.2, written for C4D 2027)
=========================================================

INSTALL
1. Save your scene and close Cinema 4D.
2. Copy the whole "ease_presets" folder (with ease_presets.pyp and res/) into:
   C:\Users\<you>\AppData\Roaming\Maxon\Maxon Cinema 4D 2027_C7F976AA\plugins
3. Start Cinema 4D -> Extensions menu -> Ease Presets.
   Tip: dock the window in your Animate layout and save the layout.

USE
- Select 2+ keys in the Timeline / F-Curve editor (or just select an animated
  object to affect all of its key segments).
- Use the tabs (All / Ease / Back / Dynamics / My Presets) to filter.
- Click a tile to preview it, then press "Apply to Selected Keys".
  Double-click a tile to apply in one go.
- Every pair of neighbouring selected keys gets the preset (Ctrl+Z undoes).
- Bezier presets only change the tangents. Bounce / Elastic insert keys between
  the two selected keys. Re-apply by selecting the first and last key again:
  "Replace keys in between" clears the old inserted keys.
- Edit the sliders to tweak the selected preset before applying.
  Bounce / Elastic: Count = number of bounces, Decay = size of the last
  bounce compared to the first (1.0 = no decay).
  "Reverse" flips an ease-out into an ease-in (and vice versa).

YOUR OWN PRESETS
- "Save from Keys": select 2 keys (saves their easing) or a run of 3+ keys
  (saves the whole curve shape), then give it a name.
- "Save as...": saves the current slider settings as a new preset.
- User presets show as purple tiles and live in ease_presets_user.json in your
  C4D prefs folder ("Show Preset File"). Copy that file to share with the team.

NOTES
- The status line warns if C4D shaped a curve differently from the preset.
- The plugin ID (1066741) is for local use. Before giving this to other
  machines, get a free unique ID at developers.maxon.net and change PLUGIN_ID.
