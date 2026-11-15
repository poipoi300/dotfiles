import QtQuick
import Qt.labs.folderlistmodel

// The expanded folder: a grid above the dock that grows out of the folder icon. Group
// folders list config items, directory folders list a filesystem path (lazily, one level at
// a time). Opening a sub-folder pushes onto `stack` and shows it in this same panel.
Item {
    id: panel

    property bool open: false
    property real anchorCx: 0          // x (parent coordinates) the panel grows out of
    property real bottomY: 0           // y of the panel's bottom edge
    property var stack: []             // [{type, name, items|path}]
    readonly property var cur: stack.length ? stack[stack.length - 1] : null
    readonly property bool hovered: hover.hovered
    property int count: 0

    signal closeRequested()
    signal runNode(var node)

    readonly property real cellW: 96
    readonly property real cellH: 104
    readonly property real pad: 14
    readonly property real headerH: 44

    readonly property int cols: count <= 4 ? Math.max(count, 2) : count <= 9 ? 3 : count <= 16 ? 4 : 5
    readonly property int rows: Math.max(1, Math.ceil(count / cols))
    readonly property int shownRows: Math.min(rows, 4)

    function openNode(node) {
        count = 0;
        stack = [node];
        forceActiveFocus();
    }
    function push(node) { count = 0; stack = stack.concat([node]); fade.restart(); }
    function popTo(depth) {   // keep `depth` levels
        if (depth < 1 || depth >= stack.length) return;
        count = 0;
        stack = stack.slice(0, depth);
        fade.restart();
    }
    function back() { if (stack.length > 1) popTo(stack.length - 1); else closeRequested(); }

    // grow animation
    property real g: open ? 1 : 0
    Behavior on g {
        NumberAnimation {
            duration: panel.open ? 240 : 140
            easing.type: panel.open ? Easing.OutBack : Easing.InCubic
            easing.overshoot: 1.2
        }
    }
    visible: g > 0.01
    opacity: Math.min(1, g * 1.6)
    onVisibleChanged: if (!visible) { stack = []; count = 0; }

    width: cols * cellW + 2 * pad
    height: headerH + shownRows * cellH + pad
    Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    x: Math.max(8, Math.min(anchorCx - width / 2, (parent ? parent.width : 0) - width - 8))
    y: bottomY - height

    transform: Scale {
        origin.x: panel.anchorCx - panel.x
        origin.y: panel.height + 24
        xScale: 0.15 + 0.85 * panel.g
        yScale: 0.15 + 0.85 * panel.g
    }

    HoverHandler { id: hover }

    focus: open
    Keys.onEscapePressed: panel.closeRequested()
    Keys.onPressed: ev => {
        if (ev.key === Qt.Key_Backspace) { panel.back(); ev.accepted = true; }
    }

    Rectangle {
        anchors.fill: parent
        radius: 18
        color: Items.style.panelBackground
        border.width: 1
        border.color: Items.style.border
    }

    // swallow stray taps on the panel background
    TapHandler { }

    // ---- header: back button + breadcrumb ----
    Row {
        id: header
        x: panel.pad
        height: panel.headerH
        spacing: 6

        Rectangle {
            visible: panel.stack.length > 1
            width: 28
            height: 28
            radius: 14
            anchors.verticalCenter: parent.verticalCenter
            color: backTap.pressed || backHover.hovered ? "#33ffffff" : "#1affffff"
            Text {
                anchors.centerIn: parent
                text: "‹"
                color: Items.style.text
                font.pixelSize: 20
                font.bold: true
                font.family: Items.style.font
            }
            HoverHandler { id: backHover }
            TapHandler { id: backTap; onTapped: panel.back() }
        }

        Repeater {
            model: panel.stack
            delegate: Row {
                required property var modelData
                required property int index
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Text {
                    visible: index > 0
                    text: "›"
                    color: "#99ffffff"
                    font.pixelSize: 14
                    font.family: Items.style.font
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: modelData.name
                    color: Items.style.text
                    opacity: index === panel.stack.length - 1 ? 1 : 0.6
                    font.pixelSize: 14
                    font.bold: true
                    font.family: Items.style.font
                    anchors.verticalCenter: parent.verticalCenter
                    TapHandler { enabled: index < panel.stack.length - 1; onTapped: panel.popTo(index + 1) }
                }
            }
        }
    }

    // ---- body ----
    Item {
        id: body
        x: panel.pad
        y: panel.headerH
        width: panel.cols * panel.cellW
        height: panel.shownRows * panel.cellH
        clip: true

        NumberAnimation on opacity { id: fade; from: 0; to: 1; duration: 140; running: false }

        Text {
            anchors.centerIn: parent
            visible: panel.count === 0 && panel.cur !== null
            text: panel.cur && panel.cur.type === "dir" && !Items.dirExists(panel.cur) && !panel.cur.sub ? "Folder not available" : "Empty"
            color: "#99ffffff"
            font.pixelSize: 13
            font.family: Items.style.font
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
                model: panel.cur.items || []
                onCountChanged: panel.count = count
                Component.onCompleted: panel.count = count
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
                    folder: "file://" + Items.expand(panel.cur.path)
                    showDirsFirst: true
                    showDotAndDotDot: false
                    showHidden: false
                    sortField: FolderListModel.Name
                    onCountChanged: panel.count = count
                }
                Component.onCompleted: panel.count = flm.count
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
