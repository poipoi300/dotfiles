import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Wayland
import Quickshell.Io

PanelWindow {
    id: win

    // ---- geometry (window coordinates; window is anchored bottom-centre) ----
    // Wide enough for the magnified row, the input zone and the folder panel. Only changes
    // with the config, never per frame.
    readonly property real winW: Math.max(1400, Math.ceil(restW + 2 * zoneExt + 64))
    readonly property real winH: 680
    readonly property real barH: 48
    readonly property real bottomMargin: 8
    readonly property real barBottom: winH - bottomMargin
    readonly property real iconBottom: barBottom - 11
    readonly property real padX: 12

    readonly property var items: Items.items
    readonly property int n: items.length
    readonly property real base: Items.iconCfg.size
    readonly property real peak: Items.iconCfg.peak
    readonly property real gap: Items.iconCfg.spacing
    readonly property real pitch: base + gap
    readonly property real restW: Math.max(0, n * pitch - gap)
    readonly property real restLeft: (winW - restW) / 2
    readonly property real barZone: bottomMargin + barH + 8
    readonly property int waveSlots: Math.max(1, Items.iconCfg.waveSlots)
    // The wave fades out once the pointer is this far past the centre of an end icon
    // (just beyond the magnified icon's outer edge) and is gone fadeLen later.
    readonly property real fadeStart: peak / 2 + 1.5 * gap
    readonly property real fadeLen: 0.75 * peak
    // Largest growth on one side of the pointer (sampled once per config): how far the row can
    // extend past its resting ends.
    readonly property real maxSide: {
        let best = 0;
        for (let c = 0; c <= 1; c += 0.05) {
            let g = 0;
            for (let k = 0; k <= waveSlots + 1; k++) {
                const d = c + k;           // slot centres left of the pointer, nearest first
                g += k === 0 ? c * waveWeight(Math.abs(c - 0.5)) : waveWeight(d - 0.5);
            }
            best = Math.max(best, g);
        }
        return (peak - base) * best;
    }
    // input zone reaches this far past the resting ends
    readonly property real zoneExt: Math.max(maxSide + padX, fadeStart + fadeLen - base / 2) + 16

    // ---- state ----
    property bool active: false          // dock revealed
    property bool magnifying: false      // pointer is in the dock; icons magnified
    property bool sim: false             // test hook (ipc)
    property real px: winW / 2
    property var sizes: []
    property var lefts: []
    property int hoverIndex: -1
    property real magT: 0                // effective magnification (hoverT x end-of-row fade)
    property bool folderOpen: false
    property int folderIndex: -1
    property bool clickOpened: false

    readonly property bool suppressed:
        (Items.behavior.hideOnFullscreen && Windows.activeFullscreen)
        || (Items.behavior.hideOnMaximized && Windows.activeMaximized)
    onActiveChanged: if (active) Items.checkDirs()
    onSuppressedChanged: if (suppressed) { active = false; magnifying = false; closeFolder(); }

    readonly property bool pointerIn: zoneHover.hovered || sim
    readonly property bool wantMag: active && magnifying && pointerIn
    property real reveal: active ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    // Pointer events only store a target. One FrameAnimation advances px/hoverT toward their
    // targets and lays out once per rendered frame, so the work no longer scales with the input
    // event rate and never restarts an animation per event. It stops itself when settled.
    readonly property real hoverTarget: (folderOpen && folderPanel.hovered) ? 1 : (wantMag ? 1 : 0)
    property real hoverT: 0
    property real pxTarget: winW / 2
    onHoverTargetChanged: smoother.start()
    onWantMagChanged: smoother.start()      // hoverIndex depends on it
    FrameAnimation {
        id: smoother
        onTriggered: {
            const dt = Math.min(frameTime, 0.05);
            const kp = 1 - Math.exp(-dt * 38);      // pointer follow, ~26 ms time constant
            const kh = 1 - Math.exp(-dt * 16);      // magnify in/out, ~60 ms
            let busy = false;
            let dx = win.pxTarget - win.px;
            if (Math.abs(dx) > 0.15) { win.px += dx * kp; busy = true; } else win.px = win.pxTarget;
            let dh = win.hoverTarget - win.hoverT;
            if (Math.abs(dh) > 0.002) { win.hoverT += dh * kh; busy = true; } else win.hoverT = win.hoverTarget;
            win.relayout();
            if (!busy) stop();
        }
    }

    // ---- window ----
    implicitWidth: winW
    implicitHeight: winH
    anchors.bottom: true
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dock"
    WlrLayershell.keyboardFocus: folderOpen
        ? (clickOpened ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand)
        : WlrKeyboardFocus.None
    screen: {
        const want = Items.behavior.output;
        for (const s of Quickshell.screens) if (want && s.name === want) return s;
        return Quickshell.screens.length ? Quickshell.screens[0] : null;
    }

    // Only the dock zone (a thin strip while hidden) and the open folder panel take input.
    mask: Region {
        item: zone
        Region { item: panelMask }
    }

    // ---- layout of the magnification wave ----
    // Content coordinate: slot i covers [i, i+1) (icon plus half a gap each side), so the
    // resting icon centre is i + 0.5. The pointer's resting coordinate u is clamped to the
    // centres of the end icons; that point c is the wave's peak. Only real slots grow.
    //
    // Slot i is laid out at its resting position plus the growth of the slots before it, minus
    // the growth before c. So the point c sits exactly under the pointer while the pointer is
    // over the row (the icon being magnified stays under it), and the row grows outward from it
    // in both directions by the amount each side actually grew: no virtual slots, no empty
    // magnified space. Past an end the layout is held with the end icon (the largest) at its
    // resting centre, and the wave fades out smoothly once the pointer passes the end icon's
    // outer edge (fadeStart .. fadeStart + fadeLen). The resting row never moves; the
    // background just covers whatever the icons span.
    function waveWeight(d) {
        const R = waveSlots;
        return d >= R ? 0 : Math.pow(0.5 * (1 + Math.cos(Math.PI * d / R)), 1.25);
    }
    function relayout() {
        const cnt = n;
        if (!cnt) { sizes = []; lefts = []; hoverIndex = -1; magT = 0; return; }
        const u = (px - restLeft + gap / 2) / pitch;
        const c = Math.max(0.5, Math.min(cnt - 0.5, u));
        const out = Math.max(0, u - (cnt - 0.5), 0.5 - u) * pitch;
        const t = Math.max(0, Math.min(1, (out - fadeStart) / fadeLen));
        const m = hoverT * (1 - t * t * (3 - 2 * t));
        const amp = (peak - base) * m;
        const R = waveSlots;
        const k = Math.min(cnt - 1, Math.floor(c));
        const s = new Array(cnt);
        const l = new Array(cnt);
        let g = 0, gc = 0;
        for (let i = 0; i < cnt; i++) {
            const d = Math.abs(c - (i + 0.5));
            const w = amp > 0 && d < R ? amp * waveWeight(d) : 0;
            s[i] = base + w;
            l[i] = restLeft + i * pitch + g;
            if (i === k) gc = g + (c - k) * w;
            g += w;
        }
        for (let i = 0; i < cnt; i++) l[i] -= gc;
        sizes = s;
        lefts = l;
        magT = m;
        // the icon under the pointer; past an end that is the end icon while the pointer is
        // still over its magnified extent
        let h = -1;
        if (u >= 0 && u < cnt) h = Math.floor(u);
        else if (u < 0 && px >= l[0] - gap / 2) h = 0;
        else if (u >= cnt && px <= l[cnt - 1] + s[cnt - 1] + gap / 2) h = cnt - 1;
        hoverIndex = (wantMag && m > 0.2) ? h : -1;
    }
    onHoverIndexChanged: if (folderHoverTimer.running) folderHoverTimer.restart()
    onNChanged: relayout()
    onRestLeftChanged: { if (hoverT === 0) { px = winW / 2; pxTarget = px; } relayout(); }
    Component.onCompleted: relayout()

    // ---- input zone ----
    Item {
        id: zone
        readonly property real hiddenW: win.restW + 80
        readonly property real shownW: win.restW + 2 * win.zoneExt
        readonly property real tall: win.barZone + win.peak + 40
        width: win.suppressed ? 0 : (win.active ? shownW : hiddenW)
        height: win.suppressed ? 0
              : !win.active ? 4
              : (win.magnifying || win.sim || win.folderOpen) ? tall : win.barZone
        x: (win.winW - width) / 2
        y: win.winH - height

        HoverHandler {
            id: zoneHover
            onHoveredChanged: {
                if (hovered) {
                    leaveTimer.stop();
                    if (!win.suppressed) win.active = true;
                } else {
                    win.magnifying = false;
                    leaveTimer.restart();
                }
            }
            onPointChanged: {
                if (!hovered || win.suppressed) return;
                const p = point.position;
                if (!win.magnifying && win.active && p.y > zone.height - win.barZone) win.magnifying = true;
                win.pointerMoved(zone.x + p.x);
            }
        }
    }
    // input region of the folder panel (zero-size when closed)
    Item {
        id: panelMask
        x: folderPanel.x
        y: folderPanel.y
        width: win.folderOpen ? folderPanel.width : 0
        height: win.folderOpen ? folderPanel.height : 0
    }

    // keep the dock up while a folder is open or the pointer is on the panel
    Timer {
        id: leaveTimer
        interval: 350
        onTriggered: {
            if (win.sim || win.folderOpen || folderPanel.hovered) return;
            win.active = false;
        }
    }
    Timer {
        id: folderCloseTimer
        interval: Items.behavior.folderLeaveDelayMs
        running: win.folderOpen && !zoneHover.hovered && !folderPanel.hovered && !win.sim
        onTriggered: win.closeFolder()
    }
    // hover-open (and switching) of folders after a short delay on the hovered icon
    Timer {
        id: folderHoverTimer
        interval: Items.behavior.folderHoverDelayMs
        running: win.wantMag && win.hoverIndex >= 0 && win.hoverIndex !== win.folderIndex
                 && (win.isFolder(win.hoverIndex) || win.folderOpen)
        onTriggered: {
            if (win.isFolder(win.hoverIndex)) win.openFolder(win.hoverIndex, false);
            else win.closeFolder();
        }
    }

    function pointerMoved(x) {
        pxTarget = x;
        if (hoverT < 0.02) px = x;      // first contact: no slide-in from the old position
        smoother.start();
    }

    function isFolder(i) {
        const t = i >= 0 && i < n ? items[i].type : "";
        return t === "group" || t === "dir";
    }
    function openFolder(i, byClick) {
        if (!isFolder(i)) return;
        folderIndex = i;
        clickOpened = byClick || (folderOpen && clickOpened);
        folderPanel.anchorCx = lefts[i] + sizes[i] / 2;
        folderPanel.bottomY = iconBottom - peak - 16;
        folderPanel.openNode(items[i]);
        folderOpen = true;
    }
    function closeFolder() {
        if (!folderOpen) return;
        folderOpen = false;
        folderIndex = -1;
        clickOpened = false;
        // however it closed (Esc, outside click, launching from it, pointer left), hide the
        // dock again unless the pointer is on it
        if (!zoneHover.hovered) leaveTimer.restart();
    }

    // focus the running window, otherwise launch (and bounce the slot)
    function runNode(node, slot) {
        const keys = Items.info(node).keys;
        if (!Windows.focus(keys)) {
            Items.launch(node);
            if (slot >= 0) slotRepeater.itemAt(slot).bounce();
        }
    }

    // ---- visuals ----
    Item {
        id: scene
        anchors.fill: parent
        Item {
            id: dockContent
            anchors.fill: parent
            visible: win.reveal > 0.001
            transform: Translate { y: (1 - win.reveal) * (win.barH + win.bottomMargin + 14) }

            // Background: covers whatever the icons span (asymmetric while magnifying near an end).
            RectangularShadow {
                anchors.fill: bar
                offset.y: 3
                blur: 18
                spread: -2
                radius: bar.radius
                color: Theme.shadow
            }
            Rectangle {
                id: bar
                readonly property real x0: win.n && win.lefts.length ? win.lefts[0] : win.restLeft
                readonly property real x1: win.n && win.lefts.length
                    ? win.lefts[win.n - 1] + win.sizes[win.n - 1] : win.restLeft + win.restW
                x: x0 - win.padX
                width: x1 - x0 + 2 * win.padX
                y: win.barBottom - win.barH
                height: win.barH
                radius: Math.min(Theme.radius + 4, height / 2)
                color: Theme.bar
                border.width: 1
                border.color: Theme.border
            }

            Repeater {
                id: slotRepeater
                model: win.items
                delegate: DockItem {
                    slotX: win.lefts[slot] !== undefined ? win.lefts[slot] : 0
                    iconSize: win.sizes[slot] !== undefined ? win.sizes[slot] : win.base
                    iconBottom: win.iconBottom
                    shown: dockContent.visible
                    folderShown: win.folderOpen && win.folderIndex === slot
                    onTapped: {
                        if (win.isFolder(slot)) {
                            if (win.folderOpen && win.folderIndex === slot) win.closeFolder();
                            else win.openFolder(slot, true);
                        } else {
                            win.closeFolder();
                            win.runNode(node, slot);
                        }
                    }
                }
            }

            // label above the hovered icon
            RectangularShadow {
                anchors.fill: label
                visible: label.visible
                opacity: label.opacity
                offset.y: 2
                blur: 10
                radius: label.radius
                color: Theme.shadow
            }
            Rectangle {
                id: label
                readonly property int idx: win.hoverIndex
                property int shownIdx: -1          // keeps the text while fading out
                onIdxChanged: if (idx >= 0) shownIdx = idx
                readonly property bool show: idx >= 0 && win.magT > 0.5 && !win.folderOpen && win.wantMag
                opacity: show ? 1 : 0
                visible: opacity > 0.01
                Behavior on opacity { NumberAnimation { duration: 110 } }
                width: labelText.implicitWidth + 22
                height: labelText.implicitHeight + 10
                radius: Math.min(Theme.radius, height / 2)
                color: Theme.label
                border.width: 1
                border.color: Theme.border
                // follows the icon every frame (no Behavior: that would restart an animation per frame)
                readonly property int at: shownIdx >= 0 && shownIdx < win.n ? shownIdx : -1
                readonly property real cx: at >= 0 ? win.lefts[at] + win.sizes[at] / 2 : win.winW / 2
                x: Math.round(Math.max(4, Math.min(win.winW - width - 4, cx - width / 2)))
                y: Math.round(at >= 0 ? win.iconBottom - win.sizes[at] - height - 10 : win.iconBottom)
                Text {
                    id: labelText
                    anchors.centerIn: parent
                    text: label.at >= 0 ? Items.info(win.items[label.at]).label : ""
                    color: Theme.text
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    font.family: Theme.font
                }
            }
        }

        FolderPanel {
            id: folderPanel
            open: win.folderOpen
            onCloseRequested: win.closeFolder()
            onRunNode: node => { const slot = win.folderIndex; win.closeFolder(); win.runNode(node, slot); }
        }
    }

    // full-screen click catcher while a folder is open: click outside closes it
    LazyLoader {
        active: win.folderOpen
        PanelWindow {
            screen: win.screen
            anchors { top: true; bottom: true; left: true; right: true }
            exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Top
            WlrLayershell.namespace: "dock-backdrop"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            color: "transparent"
            TapHandler {
                acceptedButtons: Qt.AllButtons
                onTapped: win.closeFolder()
            }
        }
    }

    Timer {
        id: benchTimer
        property real t0: 0
        property real dur: 0
        repeat: true
        onTriggered: {
            const t = (Date.now() - t0) / dur;
            if (t >= 1) { running = false; win.sim = false; win.magnifying = false; return; }
            // sweep left to right and back, twice
            const ph = (t * 4) % 2;
            const f = ph < 1 ? ph : 2 - ph;
            win.pointerMoved(win.restLeft + f * win.restW);
        }
    }

    // test / scripting hooks:  qs -c dock ipc call dock <fn>
    IpcHandler {
        target: "dock"
        function simulate(x: real): void {
            win.sim = true; win.active = true; win.magnifying = true;
            win.pointerMoved(x);
        }
        function bench(hz: int, ms: int): void {   // synthetic pointer sweep for profiling
            benchTimer.interval = Math.max(1, Math.round(1000 / hz));
            benchTimer.t0 = Date.now(); benchTimer.dur = ms;
            win.sim = true; win.active = true; win.magnifying = true;
            benchTimer.running = true;
        }
        function layout(): string {   // geometry dump (window coordinates) for checking the wave
            const r = v => Math.round(v * 10) / 10;
            return JSON.stringify({ px: r(win.px), winW: win.winW, restLeft: win.restLeft, restW: win.restW,
                lefts: win.lefts.map(r), sizes: win.sizes.map(r), magT: r(win.magT), hover: win.hoverIndex,
                barX: r(bar.x), barW: r(bar.width), zoneX: zone.x, zoneW: zone.width });
        }
        function theme(): string {    // resolved DMS palette and roles
            return JSON.stringify({ light: Theme.light, pal: Theme.pal, font: Theme.font, radius: Theme.radius,
                text: String(Theme.text), bar: String(Theme.bar), accent: String(Theme.accent), panel: String(Theme.panel) });
        }
        function shot(path: string): void {   // save the dock window's rendering as a PNG (testing)
            scene.grabToImage(r => r.saveToFile(path));
        }
        function release(): void { win.sim = false; win.magnifying = false; leaveTimer.restart(); }
        function open(i: int): void { win.openFolder(i, true); }
        function close(): void { win.closeFolder(); }
        function enter(name: string): void {   // open sub-folder `name` of the open directory folder
            const c = folderPanel.cur;
            if (c && c.type === "dir") folderPanel.push({ type: "dir", name: name, path: Items.expand(c.path) + "/" + name, sub: true });
        }
        function back(): void { folderPanel.back(); }
        function reveal(): void { win.active = true; }
        function hide(): void { win.active = false; win.magnifying = false; win.sim = false; }
    }
}
