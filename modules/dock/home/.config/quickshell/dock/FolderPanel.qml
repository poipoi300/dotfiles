import QtQuick
import QtQuick.Effects
import Qt.labs.folderlistmodel

// The expanded folder: a grid above the dock that grows out of the folder icon. Group
// folders list config items, directory folders list a filesystem path (lazily, one level at
// a time). Opening a sub-folder pushes onto `stack` and shows it in this same panel.
Item {
    id: panel

    property bool open: false          // latched open (takes keyboard focus)
    property real g: 0                 // growth 0..1, driven by the dock's open intent
    property real dropY: 0             // offset while dropping away with the dock
    property real anchorCx: 0          // x (parent coordinates) the panel grows out of
    property real bottomY: 0           // y of the panel's bottom edge
    property var stack: []             // [{type, name, items|path}]
    readonly property var cur: stack.length ? stack[stack.length - 1] : null
    readonly property var root: stack.length ? stack[0] : null
    // number of entries of the shown level, straight from whichever view is active (an
    // imperative reset missed levels with the same count as the previous one)
    readonly property int count: groupView.item ? groupView.item.count
                               : dirLoader.item ? dirLoader.item.count : 0

    signal closeRequested()
    signal runNode(var node)
    signal navigating()                // about to show another level (geometry still the old one)

    readonly property real cellW: 96
    readonly property real cellH: 104
    readonly property real pad: 14
    readonly property real headerH: 44

    readonly property int cols: count <= 4 ? Math.max(count, 2) : count <= 9 ? 3 : count <= 16 ? 4 : 5
    readonly property int rows: Math.max(1, Math.ceil(count / cols))
    readonly property int shownRows: Math.min(rows, 4)

    function openNode(node) {
        stack = [node];
        if (open) forceActiveFocus();
    }
    function clear() { if (!visible) stack = []; }
    onOpenChanged: if (open) forceActiveFocus()
    function push(node) { navigating(); stack = stack.concat([node]); fade.restart(); }
    function fadeIn() { fade.restart(); }
    function popTo(depth) {   // keep `depth` levels
        if (depth < 1 || depth >= stack.length) return;
        navigating();
        stack = stack.slice(0, depth);
        fade.restart();
    }
    function back() { if (stack.length > 1) popTo(stack.length - 1); else closeRequested(); }

    visible: g > 0.01
    opacity: Math.min(1, g * 1.6)
    onVisibleChanged: if (!visible) stack = []

    // Wide enough for the grid and the breadcrumb (up to the window width). Size changes animate
    // only between levels of an open panel: while it grows out of the icon (contents still
    // loading) they snap, so the growth stays one motion.
    readonly property real gridW: cols * cellW
    readonly property real maxW: (parent ? parent.width : gridW + 2 * pad) - 16
    width: Math.min(maxW, Math.max(gridW, headerBox.naturalHeaderW) + 2 * pad)
    height: headerH + shownRows * cellH + pad
    onMaxWChanged: headerBox.recalc()
    Behavior on width { enabled: panel.open && panel.g > 0.99; NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
    Behavior on height { enabled: panel.open && panel.g > 0.99; NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
    x: Math.max(8, Math.min(anchorCx - width / 2, (parent ? parent.width : 0) - width - 8))
    y: bottomY - height

    transform: [
        Scale {
            origin.x: panel.anchorCx - panel.x
            origin.y: panel.height + 24
            xScale: 0.15 + 0.85 * panel.g
            yScale: 0.15 + 0.85 * panel.g
        },
        Translate { y: panel.dropY }
    ]

    focus: open
    Keys.onEscapePressed: panel.closeRequested()
    Keys.onPressed: ev => {
        if (ev.key === Qt.Key_Backspace) { panel.back(); ev.accepted = true; }
    }

    RectangularShadow {
        anchors.fill: bg
        offset.y: 6
        blur: 28
        spread: -4
        radius: bg.radius
        color: Theme.shadow
    }
    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radius * 1.5
        color: Theme.panel
        border.width: 1
        border.color: Theme.border
    }

    // swallow stray taps on the panel background
    TapHandler { }

    // ---- header: back button + breadcrumb ----
    // The back button stays pinned on the left; if the path is wider than the window-limited
    // panel, middle crumbs elide first (then root, then current) so nothing is clipped away.
    Item {
        id: headerBox
        x: panel.pad
        width: panel.width - 2 * panel.pad
        height: panel.headerH

        property real naturalHeaderW: 0
        property var crumbMaxW: []

        function recalc() {
            const n = crumbs.count;
            const backW = panel.stack.length > 1 ? 28 + 6 : 0;
            const nat = [];
            let sepTotal = 0;
            for (let i = 0; i < n; i++) {
                const it = crumbs.itemAt(i);
                nat.push(it ? Math.ceil(it.naturalTextW) : 0);
                if (i > 0) sepTotal += (it ? Math.ceil(it.sepW) : 14) + 12; // sep + inner & row spacing
            }
            let textSum = 0;
            for (let i = 0; i < n; i++) textSum += nat[i];
            naturalHeaderW = backW + sepTotal + textSum;

            const avail = Math.max(0, (panel.maxW - 2 * panel.pad) - backW - sepTotal);
            const out = nat.slice();
            let over = textSum - avail;
            // 1. Elide middle crumbs first so root and current stay readable.
            if (over > 0 && n > 2) {
                let midSpare = 0;
                for (let i = 1; i < n - 1; i++) midSpare += Math.max(0, out[i] - 18);
                if (midSpare > 0) {
                    const take = Math.min(over, midSpare);
                    let taken = 0;
                    for (let i = 1; i < n - 1; i++) {
                        const sp = Math.max(0, out[i] - 18);
                        const cut = Math.min(sp, Math.ceil(sp * take / midSpare), take - taken);
                        out[i] -= cut;
                        taken += cut;
                    }
                    over -= taken;
                }
            }
            // 2. If still over, elide the root crumb.
            if (over > 0 && n > 1) {
                const cut = Math.min(over, Math.max(0, out[0] - 32));
                out[0] -= cut;
                over -= cut;
            }
            // 3. If still over, elide the current crumb so it never overflows.
            if (over > 0 && n > 0) {
                out[n - 1] = Math.max(24, out[n - 1] - over);
            }
            crumbMaxW = out;
        }

        Rectangle {
            id: backBtn
            visible: panel.stack.length > 1
            width: 28
            height: 28
            radius: 14
            anchors.verticalCenter: parent.verticalCenter
            color: backTap.pressed ? Theme.pressed : backHover.hovered ? Theme.hover : Theme.tile
            Behavior on color { ColorAnimation { duration: 90 } }
            Text {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: -1
                text: "‹"
                color: backHover.hovered ? Theme.accent : Theme.text
                font.pixelSize: 20
                font.bold: true
                font.family: Theme.font
            }
            HoverHandler { id: backHover }
            TapHandler { id: backTap; onTapped: panel.back() }
        }

        Row {
            id: header
            x: backBtn.visible ? backBtn.width + 6 : 0
            width: Math.max(0, parent.width - x)
            height: panel.headerH
            spacing: 6
            clip: true

            Repeater {
                id: crumbs
                model: panel.stack
                onCountChanged: headerBox.recalc()
                delegate: Row {
                    required property var modelData
                    required property int index
                    readonly property real naturalTextW: crumbLabel.implicitWidth
                    readonly property real sepW: sep.visible ? sep.implicitWidth : 0
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Component.onCompleted: headerBox.recalc()
                    Text {
                        id: sep
                        visible: index > 0
                        text: "›"
                        color: Theme.textDim
                        font.pixelSize: 14
                        font.family: Theme.font
                        anchors.verticalCenter: parent.verticalCenter
                        onImplicitWidthChanged: headerBox.recalc()
                    }
                    Text {
                        id: crumbLabel
                        readonly property real maxW: index < headerBox.crumbMaxW.length
                            ? headerBox.crumbMaxW[index] : implicitWidth
                        width: Math.min(implicitWidth, maxW)
                        text: modelData.name
                        elide: index === panel.stack.length - 1 ? Text.ElideMiddle : Text.ElideRight
                        color: crumbHover.hovered && index < panel.stack.length - 1 ? Theme.accent : Theme.text
                        opacity: index === panel.stack.length - 1 ? 1 : 0.65
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        font.family: Theme.font
                        anchors.verticalCenter: parent.verticalCenter
                        onImplicitWidthChanged: headerBox.recalc()
                        HoverHandler { id: crumbHover }
                        TapHandler { enabled: index < panel.stack.length - 1; onTapped: panel.popTo(index + 1) }
                    }
                }
            }
        }
    }

    // ---- body ----
    Item {
        id: body
        x: (panel.width - width) / 2
        y: panel.headerH
        width: panel.cols * panel.cellW
        height: panel.shownRows * panel.cellH
        clip: true

        NumberAnimation on opacity { id: fade; from: 0; to: 1; duration: 95; running: false }

        Text {
            anchors.centerIn: parent
            visible: panel.count === 0 && panel.cur !== null
            text: panel.cur && panel.cur.type === "dir" && !Items.dirExists(panel.cur) && !panel.cur.sub ? "Folder not available" : "Empty"
            color: Theme.textDim
            font.pixelSize: 13
            font.family: Theme.font
        }

        // group folder
        Loader {
            id: groupView
            anchors.fill: parent
            active: panel.cur !== null && panel.cur.type === "group"
            sourceComponent: GridView {
                cellWidth: panel.cellW
                cellHeight: panel.cellH
                boundsBehavior: Flickable.StopAtBounds
                model: panel.cur && panel.cur.items ? panel.cur.items : []
                delegate: FolderCell {
                    required property var modelData
                    readonly property var info: Items.info(modelData)
                    readonly property bool folder: modelData.type === "group" || modelData.type === "dir"
                    width: panel.cellW
                    height: panel.cellH
                    label: info.label
                    source: info.icon || (modelData.type === "dir" ? Items.iconUrl("folder") : "")
                    preview: modelData.type === "group" && !modelData.icon ? Items.groupPreview(modelData) : []
                    dim: !info.present
                    onTapped: {
                        if (modelData.type === "group" || modelData.type === "dir") panel.push(modelData);
                        else panel.runNode(modelData);
                    }
                }
            }
        }

        // directory folder
        Loader {
            id: dirLoader
            anchors.fill: parent
            active: panel.cur !== null && panel.cur.type === "dir" && (panel.cur.sub === true || Items.dirExists(panel.cur))
            sourceComponent: GridView {
                id: grid
                cellWidth: panel.cellW
                cellHeight: panel.cellH
                boundsBehavior: Flickable.StopAtBounds
                cacheBuffer: panel.cellH * 2
                model: FolderListModel {
                    id: flm
                    folder: panel.cur && panel.cur.path ? "file://" + Items.expand(panel.cur.path) : ""
                    showDirsFirst: true
                    showDotAndDotDot: false
                    showHidden: false
                    sortField: FolderListModel.Name
                    caseSensitive: false
                }
                delegate: FolderCell {
                    required property string fileName
                    required property string filePath
                    required property bool fileIsDir
                    width: panel.cellW
                    height: panel.cellH
                    label: fileName
                    thumb: !fileIsDir && Items.behavior.thumbnails && Items.hasThumb(fileName)
                    source: thumb ? "file://" + filePath : Items.fileIcon(fileName, fileIsDir)
                    onTapped: {
                        if (fileIsDir) panel.push({ type: "dir", name: fileName, path: filePath, sub: true });
                        else { Items.openFile(filePath); panel.closeRequested(); }
                    }
                }
            }
        }
    }
}
