import QtQuick
import QtQuick.Effects
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
    // number of entries of the shown level, straight from whichever view is active (an
    // imperative reset missed levels with the same count as the previous one)
    readonly property int count: groupView.item ? groupView.item.count
                               : dirLoader.item ? dirLoader.item.count : 0

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
        stack = [node];
        forceActiveFocus();
    }
    function push(node) { stack = stack.concat([node]); fade.restart(); }
    function popTo(depth) {   // keep `depth` levels
        if (depth < 1 || depth >= stack.length) return;
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
    onVisibleChanged: if (!visible) stack = []

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
                    color: Theme.textDim
                    font.pixelSize: 14
                    font.family: Theme.font
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: modelData.name
                    color: crumbHover.hovered && index < panel.stack.length - 1 ? Theme.accent : Theme.text
                    opacity: index === panel.stack.length - 1 ? 1 : 0.65
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    font.family: Theme.font
                    anchors.verticalCenter: parent.verticalCenter
                    HoverHandler { id: crumbHover }
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
