# Audio

PipeWire / WirePlumber with EasyEffects 8.3.2. The SMSL DAC is one stereo sink;
headphones versus the speaker amplifier is selected physically with the IR remote.
The two EQ presets remain independent. The Revelator is the microphone interface.

## Installation and controls

Apply this module through the normal arcus workflow. It declares the required
packages and enables `easyeffects.service` for `graphical-session.target`.
No package-provided user unit existed in the inspected EasyEffects package.
The unit uses `--gapplication-service` (supported but deprecated in 8.3.2), hides
the window, and restores the saved rocker and microphone state after startup.
Do not also enable EasyEffects' desktop autostart.

Add this to the main niri configuration yourself:

```kdl
include "audio-binds.kdl"
```

The snippet provides Mod+Alt+H (Headphones/Off), Mod+Alt+S (Speakers/Off), and
Mod+Alt+M (Mic on/off). These combinations were absent from the inspected DMS
binds. The module does not modify the main niri configuration or DMS binds.

```sh
audio-eq headphones
audio-eq speakers
audio-eq off
audio-eq toggle-headphones
audio-eq toggle-speakers
audio-eq mic on
audio-eq mic off
audio-eq mic toggle
audio-eq status
```

`status` prints JSON such as `{"output":"headphones","mic":"on"}`.
State is stored atomically at `$XDG_STATE_HOME/audio-eq/state`, defaulting to
`~/.local/state/audio-eq/state`; concurrent commands are serialized with flock.
First use starts with both chains Off. Pressing an already selected output
option returns to Off. Notifications show both resulting states.

Off loads an empty output pipeline (`Output Off`). Mic Off loads an empty input
pipeline (`Mic Off`). This bypasses all effects in that chain, keeping the other
chain independent. EasyEffects' CLI bypass is global, so the script disables
that global bypass after applying its chain-specific setting. These commands own
both chains: use them for changes, rather than editing the active chain or
switching global bypass in the GUI. Status describes the saved controller state,
not arbitrary changes made outside the controller.

In DMS Settings → Plugins, enable **Audio EQ Rocker**, then add its widget to the
bar's desired section. The plugin is discovered under
`~/.config/DankMaterialShell/plugins/AudioEq/`. It supports horizontal and
vertical bars, reads `audio-eq status` at startup and after state-file changes,
and uses FileView watching instead of polling. Installing the plugin does not
rewrite existing bar settings. H and S toggle their preset; off selects Off.
Physical routing still requires the IR remote.

## Paths, routing, and autoload

EasyEffects **8.3.2** stores presets under `$XDG_DATA_HOME/easyeffects/output/`
and `input/` (normally `~/.local/share/easyeffects/`), not the older
`~/.config/easyeffects/` paths. Verified against the matching source tag's
`presets_directory_manager.cpp`, preset serializers, and a running instance.
Use `easyeffects -l Headphones` to load: `-p` lists presets in this version.

`audio-eq-prepare` runs before the daemon, pins output to
`alsa_output.usb-SMSL_SMSL_USB_AUDIO-00.analog-stereo`, and discovers exactly one
Revelator input by its stable model prefix using pactl. It writes the actual
serial-bearing source name only into the local runtime configuration
`~/.config/easyeffects/db/easyeffectsrc`. No serials or endpoint GUIDs are shipped
in this public module. Startup fails clearly if there is not exactly one
Revelator source; connect it before starting the service. It never changes the
WirePlumber default sink/source.

Device → preset autoload is deliberately unused: the shared DAC cannot identify
which physical output the IR remote selected, and autoload on reconnect would
override the user's Off choice. Service startup instead restores both saved
selections. No legacy VST DLLs or Windows executable code are copied or run.

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

## Verification and restoration

- Presets loaded with EasyEffects 8.3.2; last-loaded input/output names confirmed
  using `-a` / `-s`. Persisted EQ mode, Q, frequency, gain, compressor and gate
  settings were inspected. A clamped limiter release setting was corrected to
  the supported 20 ms maximum.
- Final tests used a separate EasyEffects instance with independent XDG
  directories, a temporary `agent-test` null sink and its monitor source.
  Automatic processing/moving of existing desktop streams was disabled in that
  instance. All eight output/mic transitions passed, including output Off while
  Mic stayed On. Notifications were captured by a test stub.
- GUI tested using installed DMS components under **headless sway**, with QtTest
  clicks on actual plugin segments: H → Off → S → Off. FileView updates passed;
  no polling timer exists in the plugin. The isolated harness emitted unrelated
  stock theme-generation warnings; plugin loading and interactions passed.
- The service started and restarted successfully in the earlier authorized live
  audio test; later tests complied with the updated virtual-device-only rule.
  Earlier nested niri testing also preceded the headless-only correction.
  EasyEffects emitted an upstream hidden-window QML `height of null` warning;
  no preset-load or missing-plugin errors were observed.
- niri snippet and systemd unit validation passed. Headless sway and all test
  EasyEffects processes were stopped; null-sink module unloaded. Temporary live
  deployment files were removed and original SMSL/Revelator defaults verified.
  The live DMS service and main niri configuration were never modified.
- `lsp-plugins-lv2` was installed before the user's later no-package-install
  instruction. No other packages were installed. It remains installed.
- No arcus run and no commit. Activation and actual hardware listening tests are
  left to installation. In T3's shell, notify-send initially inherited an
  incompatible AppImage libnotify through LD_LIBRARY_PATH; it worked when that
  variable was removed for the test. Normal desktop invocation does not need
  that test-environment workaround.
