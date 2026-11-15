pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Running windows (wlr-foreign-toplevel) plus urgency from niri's event stream.
// Event driven only: no timers, no polling.
Singleton {
    id: root

    property int rev: 0                 // bumped when the window set / app ids / urgency change
    property var urgentApps: ({})       // lowercase app_id -> true

    readonly property var active: ToplevelManager.activeToplevel
    readonly property bool activeFullscreen: active ? active.fullscreen : false
    readonly property bool activeMaximized: active ? active.maximized : false

    Instantiator {
        model: ToplevelManager.toplevels
        delegate: QtObject {
            required property var modelData
            property string appId: modelData.appId
            onAppIdChanged: root.rev++
            Component.onCompleted: root.rev++
            Component.onDestruction: root.rev++
        }
    }

    function matching(keys) {
        rev; // dependency
        const out = [];
        if (!keys || !keys.length) return out;
        for (const t of ToplevelManager.toplevels.values) {
            const id = (t.appId || "").toLowerCase();
            if (id && keys.indexOf(id) >= 0) out.push(t);
        }
        return out;
    }

    function urgent(keys) {
        rev;
        for (const k of keys) if (urgentApps[k]) return true;
        return false;
    }

    // Focus a running window of this app (cycling when it has several). Returns false if none.
    function focus(keys) {
        const list = matching(keys);
        if (!list.length) return false;
        let target = list[0];
        if (list.length > 1) {
            const i = list.findIndex(t => t.activated);
            if (i >= 0) target = list[(i + 1) % list.length];
        }
        // prefer an urgent window if there is one
        target.activate();
        return true;
    }

    // ---- urgency: niri IPC event stream (one long-lived process) ----
    property var wins: ({})   // id -> { app: string, urgent: bool }

    function recompute() {
        const u = {};
        for (const id in wins) if (wins[id].urgent && wins[id].app) u[wins[id].app] = true;
        urgentApps = u;
        rev++;
    }
    function put(w) { wins[w.id] = { app: (w.app_id || "").toLowerCase(), urgent: !!w.is_urgent }; }

    function onEvent(line) {
        if (line.indexOf('"Window') < 0) return;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { return; }
        if (ev.WindowsChanged) {
            wins = {};
            for (const w of ev.WindowsChanged.windows) put(w);
            recompute();
        } else if (ev.WindowOpenedOrChanged) {
            const w = ev.WindowOpenedOrChanged.window;
            const before = wins[w.id];
            put(w);
            if (!before || before.urgent !== wins[w.id].urgent) recompute();
        } else if (ev.WindowClosed) {
            if (wins[ev.WindowClosed.id]) { delete wins[ev.WindowClosed.id]; recompute(); }
        } else if (ev.WindowUrgencyChanged) {
            const w = wins[ev.WindowUrgencyChanged.id];
            if (w) { w.urgent = ev.WindowUrgencyChanged.urgent; recompute(); }
        }
    }

    Process {
        id: stream
        command: ["niri", "msg", "--json", "event-stream"]
        running: true
        stdout: SplitParser { onRead: line => root.onEvent(line) }
        onExited: restart.start()
    }
    Timer { id: restart; interval: 3000; onTriggered: stream.running = true }
}
