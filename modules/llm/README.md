# llm

Local AI apps, built from the user's own clones in `~/src`. The module installs `llm-setup`
(build and import helper), `start-tavern` (the dock launcher) and three user units that are
installed but **not enabled**: `comfyui`, `textgen`, `sillytavern`. All listen on 127.0.0.1 only.
Personal paths (the model drive) live in the private `llm-private` module, which supplies
`~/.config/llm/paths.env` (`LLM_MODELS_SRC`, `SD_MODELS_DIR`) and
`~/.config/comfyui/extra_model_paths.yaml`.

## What lives where

| Thing | Location | Filesystem |
|---|---|---|
| SD/Flux/etc. models (StabilityMatrix layout), ComfyUI + StabilityMatrix read from here | the model drive, `.../StabilityMatrix/Data/Models` | NTFS, **read-only** |
| GGUF/EXL3 LLMs, textgen reads from here | the model drive, `.../LLM-WebUI/models` | NTFS, **read-only** |
| Old Windows installs (source of the one-time data import) | the model drive | NTFS, read-only, untrusted |
| ComfyUI code and venv | `~/src/ComfyUI` (`Comfy-Org/ComfyUI`, tag `v0.45.0`) | ext4 |
| ComfyUI input, output, temp, user, custom_nodes, new models | `~/.local/share/comfyui` (`--base-directory`) | ext4 |
| text-generation-webui code, venv, `user_data` | `~/src/textgen` (the user's fork, branch `main`) | ext4 |
| textgen writable model dir (symlinks to the read-only store) | `~/.local/share/textgen/models` | ext4 |
| SillyTavern code | `~/src/SillyTavern-local` (branch `release`, 1.19.0) | ext4 |
| SillyTavern `config.yaml` and `data/` | `~/.local/share/SillyTavern` (`--global`) | ext4 |
| StabilityMatrix data dir | `~/.local/share/StabilityMatrix`, pointer in `~/.config/StabilityMatrix/library.json` | ext4 |
| Hugging Face cache | `~/.cache/huggingface` (`HF_HOME`) | ext4 |

Nothing is ever written to the model drive. Only data is copied from the old installs: never
executables, venvs, extension or plugin code, installers.

## Setup from scratch

```sh
arcus on llm llm-private            # packages, scripts, units, paths
llm-setup comfyui                   # clone at COMFYUI_TAG, venv (Python 3.13), PyTorch, requirements
llm-setup textgen                   # venv for ~/src/textgen, upstream Linux/CUDA requirements
llm-setup sillytavern               # npm ci (no scripts)
llm-setup stabilitymatrix           # data dir + shared models override
llm-setup check                     # torch import, CUDA, device capability per venv

# one-time import from an old Windows install (see "Imported data" below)
llm-setup import-sillytavern <old SillyTavern root>
llm-setup import-comfyui     <old ComfyUI root>
llm-setup import-textgen     <old text-generation-webui root>
```

Run them: `systemctl --user start comfyui` (http://127.0.0.1:8188), `systemctl --user start textgen`
(UI :7860, OpenAI-style API :5000), `systemctl --user start sillytavern` (:8000), or
`start-tavern` for textgen plus SillyTavern and the browser. Logs: `journalctl --user -u <unit>`.

### start-tavern

Replaces the Windows "Start With Tavern" batch file. `systemctl --user restart textgen
sillytavern` stops leftovers of the previous run (`KillMode=control-group`) and starts fresh
processes; the script then waits until SillyTavern answers on `127.0.0.1:8000` and runs `xdg-open`.
`start-tavern --no-open` skips the browser. Units whose installs are missing idle on a
`ConditionPathExists=`; the script says so.

## PyTorch

- ComfyUI: Python 3.13 (upstream: "very well supported"), `torch 2.15.0+cu132`, `torchvision 0.29.2+cu132`
  from PyPI, whose Linux wheels are the CUDA 13.2 builds (arch list includes `sm_120`). The
  pytorch.org `cu134` index has `torch` but no matching `torchvision`/`torchaudio`, and `cu132` has no
  `torchaudio`, so PyPI gives the only consistent latest set. ComfyUI does not need torchaudio.
- textgen: Python 3.13, `torch 2.9.0+cu128` (what the fork's `one_click.py` pins; the pinned
  `exllamav3` and `flash-attn` wheels are built for exactly torch 2.9.0 cp313). It has `sm_120`.
  The pinned wheels come from GitHub release URLs in `requirements/full/requirements.txt` (oobabooga,
  turboderp, mjun0812) and `download.pytorch.org` (xformers): that is upstream's supply chain.

## textgen model directory

textgen saves per-model settings into `<model-dir>/config-user.yaml`, which fails on a read-only
store. `llm-setup models-overlay` (run by the unit before start) builds
`~/.local/share/textgen/models`: one symlink per entry of the store, plus a private writable copy of
`config-user.yaml`. New downloads from the UI land in the overlay (ext4), not on the drive. Run it again
(or restart the unit) after adding models to the store.

## StabilityMatrix

The AUR package (`ai` module) installs to `/opt/stabilitymatrix`, whose `Data` directory is
world-writable. SM is not in portable mode (that needs a `.sm-portable` marker inside `Data`; do not
create it): it reads `~/.config/StabilityMatrix/library.json` for its data directory, and
`<data>/settings.json` key `ModelDirectoryOverride` for the shared models. `llm-setup stabilitymatrix`
writes both (only if absent). First start still shows the licence and analytics prompts: those are the
user's choice.

Read-only models folder, tested with a read-only tree under a headless sway: the override is honoured,
models are indexed, the UI starts. Only `SharedFolders.SetupSharedModelFolders` fails at startup (it
creates every category folder missing from the models root; the error is logged, nothing else breaks),
and writing `.cm-info.json` metadata or downloading models into the store cannot work. If a category
folder is ever missing and that error matters, create it from Windows or a writable mount. Downloads and
metadata for new models are better done with the store writable once, or into a separate writable
folder via **Settings > Models folder** later.

Do not install packages (ComfyUI etc.) through SM here: ComfyUI is the standalone install above. If
wanted anyway: Packages > Add Package, choose a package; it installs into `<data>/Packages` with its own
venv (several GB), and "Model Sharing: Configuration" writes its `extra_model_paths.yaml`.

## Imported data

SillyTavern (`llm-setup import-sillytavern`, from `data/default-user`): characters, chats, group chats,
groups, worlds, personas and user avatars, backgrounds, themes, presets (Kobold/NovelAI/OpenAI/TextGen,
instruct, context, sysprompt, reasoning), Quick Replies, vectors, movingUI, `user/` files, backups,
`settings.json` (which holds extension settings) and `stats.json`. LAN server addresses in
`settings.json` (192.168.x.x) are rewritten to 127.0.0.1.

Skipped on purpose: `secrets.json` (API keys; key names were `api_key_openrouter`, `api_key_generic`,
`api_key_makersuite`, `csrfSecret`, `_migrated`: re-enter the keys in the UI), `config.yaml` of the old
install (it contained a plaintext basic-auth password: do not reuse it), `mcp_settings.json` (Windows
`cmd /c npx` and `powershell` commands), `extensions/` (code), `thumbnails/` (regenerated), caches, logs,
numbered settings backups, `plugins/`, `public/scripts/extensions/third-party/` (code).

Third-party extensions that were installed (reinstall from the Extensions menu or `git clone` into
`~/.local/share/SillyTavern/data/default-user/extensions/<name>`; global ones go into
`public/scripts/extensions/third-party/`, which is inside the clone and git-ignored):

| Extension | Source |
|---|---|
| NemoOrchestrator | https://github.com/NemoVonNirgend/NemoOrchestrator |
| ProsePolisher | https://github.com/NemoVonNirgend/ProsePolisher |
| SillyTavern-ScenePulse | https://github.com/xenofei/SillyTavern-ScenePulse |
| SillyTavern-Dialogue-Colorizer | https://github.com/XanadusWorks/SillyTavern-Dialogue-Colorizer |
| SillyTavern-MCP-Client | https://github.com/bmen25124/SillyTavern-MCP-Client |
| SillyTavern-MoonlitEchoesTheme | https://github.com/RivelleDays/SillyTavern-MoonlitEchoesTheme |
| noass | https://gitgud.io/Monblant/noass |
| sorcery | https://github.com/p-e-w/sorcery |

Server plugin (needs `enableServerPlugins: true` in `config.yaml`, off by default): SillyTavern-MCP-Server,
https://github.com/bmen25124/SillyTavern-MCP-Server.
No remote on record, code exists only on the old drive: `lore-lens` (user extension),
`agent-bridge` (extension and server plugin), `agent-working` (extension).

ComfyUI (`llm-setup import-comfyui`): `user/default/workflows` from the old ComfyUI and from the
StabilityMatrix ComfyUI package (no overwrite of same-named files), and `comfy.settings.json`.
Custom nodes are not installed.

textgen (`llm-setup import-textgen`): chat/instruct/notebook logs and the `Default` user profile. The
old presets, characters, instruction templates, grammars and tools are identical to the fork's tracked
defaults. The per-model settings (`config-user.yaml`) stay in the model store and are copied into the overlay.
