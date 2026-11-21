pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

// Loads config.json (live-reloaded on change) and resolves items to labels, icons and
// desktop entries. Pure data: nothing in here polls.
Singleton {
    id: root

    readonly property string home: Quickshell.env("HOME")
    // DOCK_DATA_DIR overrides this for testing a checkout without deploying it
    readonly property string dataDir: (Quickshell.env("DOCK_DATA_DIR") || (home + "/.local/share/dock")) + "/"

    readonly property var defaults: ({
        // Colours, font and radius come from the DankMaterialShell theme (Theme.qml). Any of
        // font, radius, background, border, text, labelBackground, panelBackground, accent set
        // here in config.json overrides it.
        style: {},
        icon: { size: 30, peak: 128, spacing: 8, waveSlots: 3 },
        behavior: {
            output: "", hideOnFullscreen: true, hideOnMaximized: true,
            revealDelayMs: 150, folderHoverDelayMs: 350, folderLeaveDelayMs: 450, thumbnails: true
        }
    })

    property var style: defaults.style
    property var iconCfg: defaults.icon
    property var behavior: defaults.behavior
    property var items: []

    // Bumped whenever the desktop entry database or the missing-command set changes, so
    // bindings that call info() re-evaluate.
    property int rev: 0
    property var missingCmds: ({})

    function merge(base, over) {
        const out = {};
        for (const k in base) out[k] = base[k];
        if (over) for (const k in over) out[k] = over[k];
        return out;
    }

    function parse(text) {
        let cfg;
        try {
            cfg = JSON.parse(text);
        } catch (e) {
            console.warn("dock: config.json is not valid JSON, keeping previous config:", e);
            return;
        }
        style = merge(defaults.style, cfg.style);
        iconCfg = merge(defaults.icon, cfg.icon);
        behavior = merge(defaults.behavior, cfg.behavior);
        items = cfg.items || [];
        checkCommands(items);
        checkDirs();
    }

    FileView {
        path: Quickshell.shellPath("config.json")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: root.parse(text())
        onLoadFailed: err => console.warn("dock: cannot read config.json:", err)
    }

    // ---- one-shot check which command items exist (runs on config load only) ----
    function collectCommands(list, out) {
        for (const n of list) {
            if (n.type === "command" && n.command && n.command.length) out.push(n.command[0]);
            else if (n.type === "group" && n.items) collectCommands(n.items, out);
        }
        return out;
    }
    function checkCommands(list) {
        const cmds = collectCommands(list, []);
        if (!cmds.length) { missingCmds = {}; rev++; return; }
        // a config reload while a check runs: run again with the new list once it exits
        if (cmdCheck.running) { cmdCheck.again = true; return; }
        cmdCheck.command = ["sh", "-c", 'for c in "$@"; do command -v "$c" >/dev/null 2>&1 || echo "$c"; done', "sh"].concat(cmds);
        cmdCheck.found = {};
        cmdCheck.running = true;
    }
    Process {
        id: cmdCheck
        property var found: ({})
        property bool again: false
        stdout: SplitParser { onRead: line => cmdCheck.found[line] = true }
        onExited: {
            root.missingCmds = cmdCheck.found;
            root.rev++;
            if (again) { again = false; root.checkCommands(root.items); }
        }
    }

    // ---- which directory folders exist (on config load and each time the dock is revealed,
    // so a late mount shows up without polling) ----
    property var dirs: ({})
    function collectDirs(list, out) {
        for (const n of list) {
            if (n.type === "dir" && n.path) out.push(expand(n.path));
            else if (n.type === "group" && n.items) collectDirs(n.items, out);
        }
        return out;
    }
    function checkDirs() {
        if (dirCheck.running) { dirCheck.again = true; return; }
        const paths = collectDirs(items, []);
        if (!paths.length) { dirs = {}; return; }
        dirCheck.command = ["sh", "-c", 'for p in "$@"; do [ -d "$p" ] && echo "$p"; done', "sh"].concat(paths);
        dirCheck.found = {};
        dirCheck.running = true;
    }
    function dirExists(node) { return !!dirs[expand(node.path)]; }
    Process {
        id: dirCheck
        property var found: ({})
        property bool again: false
        stdout: SplitParser { onRead: line => dirCheck.found[line] = true }
        onExited: {
            root.dirs = dirCheck.found;
            if (again) { again = false; root.checkDirs(); }
        }
    }

    // DesktopEntries fills in asynchronously after startup
    Connections {
        target: DesktopEntries.applications
        function onValuesChanged() { root.rev++; }
    }

    // ---- helpers ----
    function expand(path) {
        if (!path) return "";
        if (path === "~") return home;
        if (path.startsWith("~/")) return home + path.slice(1);
        return path;
    }

    // A theme name, absolute path, ~/path or a path relative to ~/.local/share/dock/
    function iconUrl(name) {
        if (!name) return "";
        if (name.startsWith("file:") || name.startsWith("image:")) return name;
        if (name.startsWith("/") || name.startsWith("~/")) return "file://" + expand(name);
        if (name.indexOf("/") >= 0) return "file://" + dataDir + name;
        return Quickshell.iconPath(name, true);
    }

    function toList(v) {
        if (v === undefined || v === null || v === "") return [];
        return typeof v === "string" ? [v] : Array.from(v);
    }

    function lookupEntry(node) {
        if (node.type !== "app") return null;
        const ids = toList(node.desktop);
        for (const id of ids) {
            const e = DesktopEntries.byId(id);
            if (e) return e;
        }
        for (const id of ids) {
            const e = DesktopEntries.heuristicLookup(id);
            if (e) return e;
        }
        return null;
    }

    // Everything the UI needs to draw and match one node.
    function info(node) {
        rev; // dependency
        const entry = lookupEntry(node);
        const explicit = iconUrl(node.icon);
        let icon = explicit;
        if (!icon && entry) icon = iconUrl(entry.icon);
        if (!icon) icon = iconUrl(node.fallbackIcon);
        if (!icon && node.type !== "group" && node.type !== "dir") icon = iconUrl("application-x-executable");

        let present = true;
        if (node.type === "app") present = entry !== null;
        else if (node.type === "command") {
            present = !(node.command && missingCmds[node.command[0]]);
            if (node.requiresIcon && !explicit) present = false;
        } else if (node.type === "dir") present = dirExists(node);

        const keys = [];
        if (entry) {
            keys.push(entry.id.toLowerCase());
            if (entry.startupClass) keys.push(entry.startupClass.toLowerCase());
        }
        for (const id of toList(node.desktop)) keys.push(id.toLowerCase());
        for (const m of toList(node.match)) keys.push(m.toLowerCase());

        return {
            label: node.name || (entry ? entry.name : ""),
            icon: icon,
            present: present,
            keys: keys,
            entry: entry
        };
    }

    function launch(node) {
        if (node.type === "app") {
            const e = lookupEntry(node);
            if (e) e.execute();
        } else if (node.type === "command" && node.command) {
            Quickshell.execDetached(node.command);
        }
    }

    function openFile(path) {
        Quickshell.execDetached(["xdg-open", path]);
    }

    // ---- file icons for directory folders (extension based, no mime database) ----
    readonly property var extIcons: ({
        png: "image-x-generic", jpg: "image-x-generic", jpeg: "image-x-generic", webp: "image-x-generic",
        gif: "image-x-generic", bmp: "image-x-generic", svg: "image-x-generic", avif: "image-x-generic",
        mp4: "video-x-generic", mkv: "video-x-generic", webm: "video-x-generic", avi: "video-x-generic",
        mov: "video-x-generic", m4v: "video-x-generic",
        mp3: "audio-x-generic", flac: "audio-x-generic", ogg: "audio-x-generic", opus: "audio-x-generic",
        wav: "audio-x-generic", m4a: "audio-x-generic",
        pdf: "application-pdf", txt: "text-x-generic", md: "text-x-generic", log: "text-x-generic",
        json: "text-x-generic", srt: "text-x-generic", ass: "text-x-generic",
        zip: "package-x-generic", rar: "package-x-generic", "7z": "package-x-generic",
        tar: "package-x-generic", gz: "package-x-generic", xz: "package-x-generic", iso: "media-optical",
        sh: "text-x-script", py: "text-x-script", exe: "application-x-executable",
        appimage: "application-x-executable", desktop: "application-x-executable"
    })
    readonly property var thumbExts: ({ png: 1, jpg: 1, jpeg: 1, webp: 1, gif: 1, bmp: 1, avif: 0 })

    function ext(name) {
        const i = name.lastIndexOf(".");
        return i > 0 ? name.slice(i + 1).toLowerCase() : "";
    }
    function fileIcon(name, isDir) {
        if (isDir) return Quickshell.iconPath("folder", true);
        return Quickshell.iconPath(extIcons[ext(name)] || "text-x-generic", true)
            || Quickshell.iconPath("text-x-generic", true);
    }
    function hasThumb(name) { return thumbExts[ext(name)] === 1; }

    // Preview icons (up to 4) for a group node
    function groupPreview(node) {
        const out = [];
        for (const c of (node.items || [])) {
            if (out.length >= 4) break;
            if (c.type === "group" || c.type === "dir") out.push({ icon: iconUrl(c.icon) || Quickshell.iconPath("folder", true), thumb: false, dim: false });
            else {
                const i = info(c);
                out.push({ icon: i.icon, thumb: false, dim: !i.present });
            }
        }
        return out;
    }
}
