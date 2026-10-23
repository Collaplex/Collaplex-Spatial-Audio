# Collaplex Spatial Audio

**Turns a Linux system output into a headphone spatial-audio chain.**
Spatial audio for headphones on Linux — a PipeWire chain driven by in-house DSP.

Package and command name: `collaplex-audio`.

![CI](https://github.com/Collaplex/Collaplex-Spatial-Audio/actions/workflows/ci.yml/badge.svg) ![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)


`Linux` · `PipeWire` · `96 kHz end to end` · `Local control panel` · `AGPL-3.0-or-later`

[中文](README.md) · **English**

![Control panel](doc/panel.png)

## Overview

Stereo on headphones has two built-in problems: everything sits inside your head, and different sources arrive at wildly different loudness. Collaplex Spatial Audio attaches a PipeWire effect chain to the system output and fixes both in order, then adds correction for your headphones and taste:

- **Binaural rendering** — measured HRTF (SADIE II / subject H4) plus a 3-second room reverb, so the stage moves outside your head.
- **Loudness normalization** — energy-windowed gain with rate limiting, so every source lands at the same level without squashing dynamics.
- **32-band EQ** — third-octave, 10 voicing presets, plus two venue combinations designed around real room acoustics.

Every parameter applies in real time and persists across restarts. The chain never edits your system audio configuration and never starts by itself: it takes over on `start`, and `stop` restores the previous output.

## Features

| Feature | Detail |
|---|---|
| Binaural HRTF | SADIE II subject H4, 96 kHz / 24 bit, four convolution paths (front-L/front-R) |
| Room reverb | 3-second stereo IR (generated from a room model) with its own wet-gain stage, live slider |
| Loudness normalization | Target −14 dBFS, energy window with 6 dB/s rate limit; gain shared across channels so the image stays put |
| 32-band EQ | 20 Hz – 25 kHz, third-octave, ±15 dB per band, 10 presets + 2 venue combinations |
| Master volume + soft limiter | ±12 dB; limiter at −0.63 dBFS, 0% clipped samples |
| Live panel | 62 Hz SSE push: four level meters, normalization gain, wet level, master volume, device state |
| Output device management | Pick the hardware output, persisted; a guard thread re-checks every 3 s and reattaches devices that appear late |
| Persistence | EQ / master / wet level stored in `~/.local/state/cinema-spatial/`, restored if shared memory is wiped |
| Non-invasive | No system PipeWire config changes, no autostart; after `stop` the default output returns to where it was |

## Signal chain

```
Global audio (application output)
  → collaplex_vsink        virtual sink (system default output)
  → loudness norm          pipe DSP ×2 (one process per channel, shared gain via flock)
  → collaplex_hrtf_in      binaural HRTF (SADIE II / H4, 4 convolution paths)
  → room reverb            convolver (3 s IR) + wet-gain stage (pipe DSP ×2)
  → collaplex_eq_in        32-band EQ + master volume + soft limiter (pipe DSP ×2)
  → digital output (S/PDIF or the selected hardware device)
```

Six nodes, six DSP processes, 96 kHz throughout. Each effect stage is an independent instance, so you can feed it with `pw-play` and verify it on its own instead of debugging the whole chain at once.

## Install

**Requirements**: Linux + PipeWire + WirePlumber + `python3-numpy` + `python3-scipy`.

**deb package (recommended)**

```sh
sudo dpkg -i collaplex-audio_1.0.2_all.deb
collaplex-audio start          # installing does not take over — this step does
```

**From source**

```sh
git clone https://github.com/Collaplex/Collaplex-Spatial-Audio.git
cd Collaplex-Spatial-Audio
collaplex-audio start          # same as bash scripts/install.sh
```

## Usage

```sh
collaplex-audio start    # deploy config → reload PipeWire → self-check
collaplex-audio stop     # restore the system default output, stop the panel
collaplex-audio web      # open the control panel (http://127.0.0.1:8789)
```

- `WET=0.4 bash scripts/install.sh` — initial wet level (default 0.3)
- `WEB_PORT=8899 collaplex-audio web` — different panel port

**Autostart**: the chain needs no service. Four config files sit in `~/.config/pipewire/pipewire.conf.d/` and load with PipeWire, so a single `start` is enough from then on. The panel ships as a systemd user unit, `collaplex-audio-panel.service`, installed and enabled by `start`. To undo:

```sh
systemctl --user disable --now collaplex-audio-panel.service
collaplex-audio stop
```

Audio devices and PipeWire are per-user, so "autostart" here means **at login**.

## Control panel

Open with `collaplex-audio web` → http://127.0.0.1:8789

| Area | Contents |
|---|---|
| Levels | Four meters across two channels: in-L / out-L / in-R / out-R. Bar length = live RMS (−60 to 0 dBFS), small text = block peak |
| Normalization | Target slider + input level bar + dynamic gain fader (0 dB at 60% of the scale, range −18 to +12 dB) |
| Reverb wet | 0 – 1, applied live |
| Master volume | ±12 dB, applied live |
| Output device | Hardware output dropdown + Apply, with attach state on the right |
| 32-band EQ | Third-octave, ±15 dB per band, 10 presets + venue linkage |

- Every slider: wheel to adjust, Shift+wheel to fine-tune, double-click to zero
- Panel refreshes at 62 Hz over SSE; meters rise fast and fall slow, peaks held 1.6 s
- Parameters travel through shared memory (`/dev/shm/collaplex-eq`) with a version counter, so nothing is read half-updated and changes need no reload

## Venue presets

A venue button switches EQ and reverb wet level together; both follow measured behaviour of real rooms:

| Venue | Design basis | Reverb time | Wet |
|---|---|---|---|
| Cinema | HF follows the X-curve (ISO 2969) at −1.5 dB/octave plus screen loss; 25–60 Hz +2.5–3 dB to restore LFE weight; slight dip at 200–400 Hz | 0.4 – 0.8 s | 0.35 |
| Opera house | 150–400 Hz +2 dB (wooden-hall warmth); 2–5 kHz +0.5–1 dB (unamplified presence); gentle HF roll-off | 1.4 – 1.8 s | 0.78 |

Measured difference: cinema is **+3.4 dB** heavier at 25–60 Hz than the opera house, which in turn is **+3.0 / +3.5 dB** up at 3.15–5 kHz and 8–16 kHz. Master volume never changes with the venue — that one is yours.

## Measurements

All numbers below come from the scripts in `tests/` run against the real chain, not design targets:

| Metric | Measured |
|---|---|
| Loudness normalization | 20.00 dB input difference → **0.00 dB** at the output |
| Binauralization | output L/R correlation **0.679** (passthrough = 1.000) |
| Reverb | wet 0 has no tail; wet 1 decays smoothly over 3 s (−11 → −129 dBFS) |
| Master linearity | +6 / +12 dB → RMS +5.99 / +11.20 dB |
| Soft limiter | at +12 dB, peaks **−1.11 dBFS**, 0% clipped samples |
| THD | pure tone at +0 / +6 dB: **−91.1 dB** relative to fundamental |
| Noise floor | −240 dBFS bypassed and through the chain; the chain adds none |
| Panel refresh | **62 Hz** (124 frames in 2 s) |
| Device guard | reattaches **2–3 s** after the link is pulled |

Low-frequency excess, before and after the fix (same material, same scripts):

| Metric | Before | After |
|---|---|---|
| HRTF + EQ LF (20–200 Hz vs 1 kHz) | +7.5 dB | **−0.1 dB** |
| Plus reverb | +13.6 dB | **+0.2 dB** |
| Whole-chain crest factor | 2.0 dB | **12.2 dB** |
| Samples hitting the limiter | 50.49% | **0.00%** |

Diagnosis and fix: [doc/DESIGN.md](doc/DESIGN.md) (Chinese).

## How it differs from commercial virtualizers

| | Dolby Atmos for Headphones and similar | This chain |
|---|---|---|
| Input | Multichannel / Atmos objects → binaural | **Stereo only** → binauralize + room reverb |
| HRTF | Proprietary average model, opaque, not swappable | SADIE II H4 measured HRTF, files replaceable |
| Adjustability | Fixed presets | 32-band EQ + wet + master + venue, all live |
| Measurability | Black box | Every stage can be fed and measured separately |

**The boundary, stated plainly**: this chain processes stereo only. It has no per-object rendering, so a 5.1 or Atmos source will localize noticeably better on a dedicated renderer. The space here comes from HRTF externalization plus room reverb, not from multichannel rendering.

Level-match before comparing: this chain runs +15.1 dB louder than passthrough, and the louder one almost always "wins" an unmatched comparison.

## Limitations

- **Linux + PipeWire only** — built on PipeWire's filter-chain and `pipe` plugin; no Windows or macOS.
- **Stereo input only** — no downmix from multichannel, no object rendering.
- **96 kHz throughout** — IRs are handled at 96 kHz; devices that cannot do it get resampled by PipeWire.
- **Headphone-oriented** — HRTF and room reverb are designed to externalize on headphones; for speakers, tune the EQ yourself.

## Development

```sh
uvx pyright --project pyrightconfig.json                        # type check (must be 0 errors)
bash build-deb.sh                                                  # build the deb into dist/
uv run --with websocket-client python tests/cdp_drag_check.py        # real-mouse CDP checks
```

Implementation notes, pitfalls and full diagnostic data: [doc/DESIGN.md](doc/DESIGN.md) (Chinese).

## License

**Dual-licensed**, see [LICENSING.md](LICENSING.md):

- Open source: **AGPL-3.0-or-later**, commercial use allowed; distributing derivatives or offering the software as a network service requires releasing the complete corresponding source under the same license.
- Commercial: closed-source integration, preinstallation / OEM distribution and custom development require a commercial license.
- Third-party material: the SADIE II data in `hrir/` is **Apache-2.0** (AudioLab, University of York) and may be used commercially.

Current version **1.0.2**. The first generation lives in `edgevoid/cinema-spatial`; this second generation is a ground-up rewrite with one independent instance per effect stage and in-house DSP, with parameters applied live.
