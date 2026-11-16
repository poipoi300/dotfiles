# Audio

PipeWire / WirePlumber with EasyEffects 8.3.2. The SMSL DAC is one stereo sink;
headphones versus the speaker amplifier is selected physically with the IR remote.
Headphones and Speakers are separate presets. The Revelator microphone always
uses Mic, independently of output selection.

## Installation and controls

Apply this module through the normal arcus workflow. It declares packages and
enables `easyeffects.service` for `graphical-session.target`. The package did not
ship a user unit when inspected. The unit starts EasyEffects hidden with
`--gapplication-service`, runs `audio-eq-prepare` first, then `audio-eq restore`.
Do not also enable EasyEffects desktop autostart.

Add this to the main niri configuration yourself:

```kdl
include "audio-binds.kdl"
```

Mod+Alt+H toggles Headphones/Off; Mod+Alt+S toggles Speakers/Off. These keys do
not conflict with the inspected DMS binds. The former Mod+Alt+M mic toggle is
removed. The module does not modify the main niri configuration or DMS binds.

```sh
audio-eq headphones
audio-eq speakers
audio-eq off
audio-eq toggle-headphones
audio-eq toggle-speakers
audio-eq status
audio-eq mic  # Debug reload of Mic; cannot turn it off.
```

State lives at `$XDG_STATE_HOME/audio-eq/state` (default
`~/.local/state/audio-eq/state`), is written atomically, and commands use flock.
Status prints JSON such as `{"output":"headphones","mic":"on"}`. Old saved
mic-Off states migrate to the always-on policy. Startup restores only the output
choice; input is always loaded with Mic. There is no supported mic-Off command.
Every output change ensures Mic is loaded and global bypass is disabled.

Off loads the empty **Output Off** preset, bypassing the output chain only.
EasyEffects global bypass affects input too, so output-Off never enables it.
Use this controller instead of changing the chains or global bypass in the GUI;
status describes controller state rather than arbitrary external GUI edits.

Enable **Audio EQ Rocker** in DMS Settings → Plugins and add it to the bar. The
plugin lives under `~/.config/DankMaterialShell/plugins/AudioEq/`. It shows only
H and S inside the same native `BasePill` used by the stock clock/media/weather
widgets. An active option has the theme's primary text colour; neither is
highlighted when output is Off. Clicking the active option turns output effects
off; clicking the other switches presets. There is no middle or mic segment.
`StyledText`, `StateLayer`, bar text sizing, Theme colours, spacing and corner
radius provide native hover/pressed/ripple behaviour without separate segment
background boxes. Horizontal and vertical bars are supported.

FileView watches the state file; the plugin reads `audio-eq status` at startup
and after changes, with no polling timer. Physical routing still requires the
IR remote. Reload DMS after updating the plugin; the implementation does not
restart the live DMS service. The startup/autoload changes take effect on the
next EasyEffects service start; `audio-eq mic` can explicitly reload Mic.

## Paths, routing, and autoload

EasyEffects **8.3.2** uses `$XDG_DATA_HOME/easyeffects/output/` and `input/`
(normally `~/.local/share/easyeffects/`), not the older config paths. This was
verified against its matching source tag and a running instance. Use `-l` to
load a preset; `-p` lists presets in this version.

`audio-eq-prepare` pins output to
`alsa_output.usb-SMSL_SMSL_USB_AUDIO-00.analog-stereo` and discovers exactly one
Revelator input by its model prefix. Its actual serial-bearing node name is
written only into the local `~/.config/easyeffects/db/easyeffectsrc` and locally
generated `$XDG_DATA_HOME/easyeffects/autoload/input/` JSON files. Public module
files contain no serials, usernames, endpoint GUIDs or absolute home paths.
Connect the Revelator before service startup; discovery fails clearly otherwise.
The helper never changes WirePlumber's default sink/source.

Mic autoload entries map the Revelator's route **descriptions** (as expected by
EasyEffects, not PulseAudio port names) to Mic, including the empty route.
An input fallback to Mic also handles a newly exposed route. Input automatic
processing is explicitly enabled, global bypass is disabled, and ExecStartPost
loads Mic regardless of the saved rocker state. Output device autoload remains
unused: one DAC cannot identify the IR remote's physical selection, and output
autoload would override the user's Off choice.

## APO → EasyEffects conversion

Sources: Equalizer APO `config.txt`, `Headphones.txt`, `Speakers.txt`,
`Microphone.txt`, and `Limiter.txt`, inspected as untrusted text. The active
Windows config included headphones and microphone; speakers were commented out.
Both output curves targeted the same SMSL endpoint. All 14 bands are enabled in
both channels, with IIR processing, decramping Off, **APO (DR)** filter design,
and slope **x1** (one biquad). PK maps to Bell with frequency, gain, and Q intact.
Preamp maps to equalizer input gain. No LP/HP bands occur in these curves; their
APO-mode equivalents would be Lo-pass/Hi-pass, Q=1/√2 unless explicitly supplied.

| Band | APO / EasyEffects | Hz | Headphones gain dB | Speakers gain dB | Q (H / S) |
|---|---|---:|---:|---:|---|
| Preamp | input-gain | — | -9.3 | -6.3 | — |
| 1 | LSC → Lo-shelf | 100 | +7.5 | +3 | 0.667641145 / 0.670294400 |
| 2 | PK → Bell | 750 | -2 | -1.5 | 2.3 |
| 3 | PK → Bell | 1600 | +3.5 | +3.5 | 1.41 |
| 4 | PK → Bell | 3000 | +3.5 | +3.5 | 1.41 |
| 5 | PK → Bell | 5600 | -4 | -4 | 4 |
| 6 | PK → Bell | 35 | +2 | +2 | 1.41 |
| 7 | PK → Bell | 528 | +1.5 | +1.5 | 2.3 |
| 8 | PK → Bell | 890 | -1.5 | -1.5 | 3.5 |
| 9 | PK → Bell | 1224 | -1.5 | -1.5 | 2 |
| 10 | PK → Bell | 2200 | +2.5 | +2.5 | 3 |
| 11 | PK → Bell | 4200 | +3 | +3 | 3 |
| 12 | PK → Bell | 8130 | -2.5 | -2.5 | 4 |
| 13 | HS → Hi-shelf | 6000 | +4 | +4 | 0.669929082 |
| 14 | PK → Bell | 7400 | +2 | +2 | 3 |

Shelf conversion uses the RBJ identity:

```
A = 10^(gain_dB / 40)
S = slope_dB / 12
Q = 1 / sqrt((A + 1/A) * (1/S - 1) + 2)
```

For `LSC 10.7992 dB`, S=0.8999333333333333; LSC uses the supplied center
frequency without corner-frequency correction. Separate gains require separate
Q values. EasyEffects' slope x1 means filter order, not APO's shelf S.
The plain `HS Fc 6000 Hz Gain 4 dB` supplies neither Q nor explicit slope:
APO's factory defaults to S=0.9 and leaves its corner-frequency flag false in
this branch. Thus its center stays 6000 Hz. This detail was checked in the APO
factory source, rather than relying on EasyEffects' generic APO text importer,
which defaults plain shelves to Q=2/3 and does not parse this explicit slope.
LSP APO (DR) uses alpha=sin(omega)/(2Q), matching the converted shelf exactly
apart from floating-point implementation precision.

References: [APO factory](https://github.com/mirror/equalizerapo/blob/master/filters/BiQuadFilterFactory.cpp),
[APO frequency handling](https://github.com/mirror/equalizerapo/blob/master/filters/BiQuadFilter.cpp),
[LSP DSP](https://github.com/lsp-plugins/lsp-dsp-units/blob/master/src/main/filters/Filter.cpp),
[EasyEffects 8.3.2](https://github.com/wwmm/easyeffects/tree/v8.3.2).

### Microphone: functional approximation requiring listening calibration

The Windows chain is ReaGate → Lisp → ReaJS expander → Limiter6. Lisp is a
**de-esser**, not a conventional broadband compressor. Its normalized VST
parameters, ReaGate timing values, and Limiter6 controls are not portable
physical-unit settings; this is not a bit-identical dynamics port.

| Windows stage | Mic preset | Fidelity / choices |
|---|---|---|
| ReaGate threshold 0.0158489 | Gate threshold -36.0000175 dB | Assumes threshold is linear amplitude; validate with voice |
| ReaGate timing / hold | Gate attack 10 ms, release 150 ms, lookahead 4 ms, zone -3 dB, reduction -60 dB | New starting settings; no equivalent hold control; not decoded VST timing |
| Lisp de-essing + Limiter6 RMS compression | Downward compressor, -18 dB threshold, 3:1, 10 ms attack, 100 ms release, 6 dB knee, no makeup | New broadband voice-compression starting point; Lisp's frequency-selective de-essing is omitted |
| ReaJS expander chunk | Covered partially by gate noise attenuation | Text decoded to `sstillwell/expander`, -42, 2, 0, 0, 0, 8, 100; separate expander not reproduced |
| Limiter6 | LSP limiter, -1 dB threshold, gain boost Off, 5 ms lookahead/attack, 20 ms release, True Peak/24 bit | Meaningful peak protection; normalized Ceiling=0.97 is not assumed to mean linear 0.97 |

The separate Voicemeeter limiter/delay config is not applied: there is no migrated
Voicemeeter bus, and its ReaJS chunk describes a 1140 ms delay that would be
inappropriate to add to the mic path. Tune the Mic preset with real speech after
installation; these settings have not been perceptually validated.

## Verification

Follow-up tests used a separate EasyEffects instance, isolated XDG directories,
a temporary null sink and a remapped virtual source. Test configuration disabled
automatic movement of desktop streams and replaced the SMSL destination with the
virtual sink before starting the test instance. The production routing helper
was exercised against that virtual Revelator fixture; no real audio device was
used or reconfigured.

- Exact device/empty-route autoload selected Mic with the fallback disabled.
- Restore migrated an old saved mic-Off state and linked gate → compressor →
  limiter on the virtual source.
- Actual global bypass removed the gate from the virtual signal path. Output
  Off instead left all three mic effects linked and global bypass disabled.
- H → Off and S → Off CLI transitions preserved Mic and its input graph.
  `audio-eq mic` reloaded Mic; `audio-eq mic off` was rejected.
- Headless sway and QtTest exercised the actual DMS plugin: Off → H → Off → S
  → H, with file-watch updates, no visible Off segment, and no mic control.
  A separate clean screenshot placed the plugin beside the stock DMS Clock.
- Shell syntax, niri snippet, JSON, privacy checks, and diff whitespace checked.
- Test processes stopped and virtual modules unloaded. Original default devices
  and the original running EasyEffects process were verified unchanged. No live
  DMS restart, package installation, arcus run, or commit occurred in this follow-up.

The Mic settings remain a functional approximation requiring speech calibration;
the conversion limitations above still apply.
