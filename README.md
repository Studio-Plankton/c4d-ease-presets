# Ease Presets for Cinema 4D

A preset library for animation curves: easing, back, bounce and elastic presets that
you apply to selected keys, plus your own saved curves.

Version 2.0, written for and tested in Cinema 4D 2027 (Python 3.11).

| Wide: settings beside the presets | Narrow or docked: settings below |
| --- | --- |
| <img src="docs/ease-presets.png" width="560" alt="Ease Presets window: preset tiles on the left, chart and settings for Back Out on the right"> | <img src="docs/stacked.png" width="230" alt="Stacked layout with the chart and settings under the tiles"> |

## Install

1. Download `ease_presets_2.0.zip` from the
   [latest release](https://github.com/Studio-Plankton/c4d-ease-presets/releases/latest)
   and unpack it. It holds one folder, `ease_presets`.
2. In Cinema 4D, open **Edit > Preferences** and click **Open Preferences Folder**.
   Then close Cinema 4D.
3. Copy the `ease_presets` folder into the `plugins` folder of that preferences folder.
4. Start Cinema 4D, then **Extensions > Ease Presets**.

To update, replace the `ease_presets` folder with the new one. Your own presets are
stored outside it and stay.

Dock the window anywhere and save the layout: it reflows to the space it gets.

## Use

- Select two or more keys in the Timeline (Dope Sheet or F-Curve), or select an
  animated object to affect all of its key segments.
- Click a tile to preview it, then press **Apply**. Double-click applies in one go.
  Right-click a tile for Apply, Save As, Rename and Delete.
- Every pair of neighbouring selected keys gets the preset. Ctrl+Z undoes it.
- Bezier presets only change tangents. Bounce and Elastic insert keys between the two
  keys. To re-apply, select the first and last key again: **Replace Keys Between**
  clears the keys that were inserted before.
- Tweak before applying:
  - Bezier: Out Time, Out Value, In Time, In Value (the two tangent handles).
  - Bounce and Elastic: Amplitude, Count, Decay (size of the last bounce compared to
    the first; 1 = no decay).
  - **Reverse** turns an ease-out into an ease-in and back.
- Click the chart to replay the motion.

## Window

- Tabs filter the library: All, Ease, Back, Dynamics, My Presets.
- The tiles add and remove columns as the window changes width.
- A wide window puts the settings beside the tiles; a narrow one puts them below.
- Drag the divider between the tiles and the settings to resize them (sideways when
  side by side, up and down when stacked). The chart fills its section, so a bigger
  section means a bigger chart. It is Cinema 4D's own divider, and the split is
  remembered for each of the two layouts.
- All colours come from the Cinema 4D interface scheme, so the window follows the
  theme and any colours changed in Preferences.

## Your own presets

- **Save from Keys**: select 2 keys (saves their easing) or a run of 3 or more keys
  (saves the whole curve shape, up to 200 keys).
- **Save As**: saves the current slider settings as a new preset.
- User presets live in `ease_presets_user.json` in the Cinema 4D preferences folder
  (Presets > Show Preset File). Copy that file to share presets with the team, then
  use Presets > Reload Preset File.
- The file is checked when it is read: values are clamped to safe ranges and entries
  that are not valid are skipped, with a count in the status line.

## Notes

- The status line warns when Cinema 4D shaped a curve differently from the preset.
- The plugin ID (1074269) is registered with Maxon under the label "EasePresets", so
  the plugin can be shared outside the studio.
- The plugin reads and writes only its preset file and its own window sizes in the
  Cinema 4D preferences. It makes no network connections.

## Development

Link the repo folder into the preferences `plugins` folder instead of copying it, so
edits are picked up at the next start of Cinema 4D (PowerShell):

```powershell
New-Item -ItemType Junction -Path "$env:APPDATA\Maxon\<your C4D prefs folder>\plugins\ease_presets" -Target "<repo>\ease_presets"
```

## Licence

MIT, see [LICENSE](LICENSE).
