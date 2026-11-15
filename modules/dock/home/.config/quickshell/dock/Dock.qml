import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Io

PanelWindow {
    id: win

    // ---- geometry (window coordinates; window is anchored bottom-centre) ----
    readonly property real winW: 1400
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

    // ---- state ----
    property bool active: false          // dock revealed
    property bool magnifying: false      // pointer is in the dock; icons magnified
    property bool sim: false             // test hook (ipc)
    property real px: winW / 2
    property var sizes: []
    property var lefts: []
    property int hoverIndex: -1
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
    // Magnification layout. Slot i sits at its resting position plus the growth of every slot
    // before it, minus half of the wave's total growth. The sum runs over virtual slots outside
    // the row too, so the wave always adds the same total width: slots far from the pointer sit at
    // rest -/+ half of it wherever the pointer is (the row stays centred), and the point under the
    // pointer stays exactly under it (the wave is symmetric around it).
    function relayout() {
        const cnt = n;
        if (!cnt) { sizes = []; lefts = []; return; }
        const R = Items.iconCfg.waveSlots;
        const amp = (peak - base) * hoverT;
        const u = (px - restLeft) / pitch;
        const jmin = Math.floor(u - R) - 1;
        const m = Math.ceil(u + R) + 1 - jmin + 1;
        const w = new Array(m);
        const pre = new Array(m + 1);
        pre[0] = 0;
        for (let k = 0; k < m; k++) {
            const d = Math.abs(u - (jmin + k + 0.5));
            const b = d >= R ? 0 : 0.5 * (1 + Math.cos(Math.PI * d / R));
            w[k] = amp * Math.pow(b, 1.25);
            pre[k + 1] = pre[k] + w[k];
        }
        const half = pre[m] / 2;
        const s = new Array(cnt);
        const l = new Array(cnt);
        for (let i = 0; i < cnt; i++) {
            const k = i - jmin;
            s[i] = base + (k >= 0 && k < m ? w[k] : 0);
            l[i] = restLeft + i * pitch + pre[Math.max(0, Math.min(m, k))] - half;
        }
        sizes = s;
        lefts = l;
        const f = Math.floor(u);
        hoverIndex = (hoverT > 0.2 && f >= 0 && f < cnt) ? f : -1;
    }
    onHoverIndexChanged: if (folderHoverTimer.running) folderHoverTimer.restart()
    onNChanged: relayout()
    onRestLeftChanged: { if (hoverT === 0) { px = winW / 2; pxTarget = px; } relayout(); }
    Component.onCompleted: relayout()

    // ---- input zone ----
    Item {
        id: zone
        readonly property real hiddenW: win.restW + 80
        readonly property real shownW: win.restW + 360
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
                    win.hoverIndex = -1;
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
        onTriggered: { win.closeFolder(); if (!zoneHover.hovered) leaveTimer.restart(); }
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
        folderOpen = false;
        folderIndex = -1;
        clickOpened = false;
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
        id: dockContent
        anchors.fill: parent
        visible: win.reveal > 0.001
        transform: Translate { y: (1 - win.reveal) * (win.barH + win.bottomMargin + 14) }

        Rectangle {
            id: bar
            // centred on the screen; grows symmetrically to cover whichever end extends further
            readonly property real half: win.n
                ? Math.max(win.winW / 2 - win.lefts[0], win.lefts[win.n - 1] + win.sizes[win.n - 1] - win.winW / 2) + win.padX
                : win.restW / 2
            x: win.winW / 2 - half
            width: 2 * half
            y: win.barBottom - win.barH
            height: win.barH
            radius: Items.style.radius
            color: Items.style.background
            border.width: 1
            border.color: Items.style.border
        }

        Repeater {
            id: slotRepeater
            model: win.items
            delegate: DockItem {
                slotX: win.lefts[slot] !== undefined ? win.lefts[slot] : 0
                iconSize: win.sizes[slot] !== undefined ? win.sizes[slot] : win.base
                iconBottom: win.iconBottom
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
        Rectangle {
            id: label
            readonly property int idx: win.hoverIndex
            readonly property bool show: idx >= 0 && win.hoverT > 0.5 && !win.folderOpen && win.wantMag
            property string text: ""
            onIdxChanged: if (idx >= 0) text = Items.info(win.items[idx]).label
            opacity: show ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 110 } }
            width: labelText.implicitWidth + 24
            height: labelText.implicitHeight + 12
            radius: 10
            color: Items.style.labelBackground
            border.width: 1
            border.color: "#1affffff"
            property real cx: idx >= 0 ? win.lefts[idx] + win.sizes[idx] / 2 : win.winW / 2
            property real topY: idx >= 0 ? win.iconBottom - win.sizes[idx] - height - 10 : win.iconBottom
            x: Math.max(4, Math.min(win.winW - width - 4, cx - width / 2))
            y: topY
            Behavior on x { enabled: label.show; NumberAnimation { duration: 80; easing.type: Easing.OutCubic } }
            Text {
                id: labelText
                anchors.centerIn: parent
                text: label.text
                color: Items.style.text
                font.pixelSize: 14
                font.bold: true
                font.family: Items.style.font
            }
        }
    }

    FolderPanel {
        id: folderPanel
        open: win.folderOpen
        onCloseRequested: win.closeFolder()
        onRunNode: node => { win.closeFolder(); win.runNode(node, win.folderIndex); }
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
        function layout(): string {   // geometry dump for checking centring
            return JSON.stringify({ first: win.lefts[0], last: win.lefts[win.n - 1], lastSize: win.sizes[win.n - 1],
                barX: bar.x, barW: bar.width, barCentre: bar.x + bar.width / 2, winCentre: win.winW / 2, hover: win.hoverIndex });
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
