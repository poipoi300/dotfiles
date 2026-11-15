import QtQuick
import Qt.labs.folderlistmodel

// One dock slot. Geometry (left/size) comes from the magnification layout in Dock.qml;
// the icon itself is a fixed 128 px item that is only scaled, so no relayout happens.
Item {
    id: cell

    required property var modelData
    readonly property var node: modelData
    required property int index
    readonly property int slot: index
    property real slotX: 0
    property real iconSize: 30
    property real iconBottom: 0

    readonly property var info: Items.info(node)
    readonly property bool isFolder: node.type === "group" || node.type === "dir"
    readonly property bool running: !isFolder && Windows.matching(info.keys).length > 0
    readonly property bool urgent: !isFolder && Windows.urgent(info.keys)
    readonly property bool present: info.present
    readonly property bool hasIcon: node.icon !== undefined && node.icon !== ""
    property var dirPreview: []
    readonly property var preview: node.type === "group" ? Items.groupPreview(node) : dirPreview

    signal tapped()
    function bounce() { hop.restart(); }

    x: slotX
    y: iconBottom - iconSize
    width: iconSize
    height: iconSize

    Item {
        id: body
        width: parent.width
        height: parent.height

        DockIcon {
            x: cell.iconSize / 2 - 64
            y: cell.iconSize - 128
            transformOrigin: Item.Bottom
            scale: cell.iconSize / 128
            source: cell.info.icon
            label: cell.info.label
            preview: cell.hasIcon ? [] : cell.preview
            dim: !cell.present
        }
    }

    // running indicator
    Rectangle {
        width: 4
        height: 4
        radius: 2
        x: cell.iconSize / 2 - 2
        y: cell.iconSize + 4
        color: "white"
        opacity: cell.running ? 0.95 : 0
        Behavior on opacity { NumberAnimation { duration: 150 } }
    }

    // launch bounce (once) and urgent attention (repeats while urgent)
    SequentialAnimation {
        id: hop
        NumberAnimation { target: body; property: "y"; to: -30; duration: 170; easing.type: Easing.OutQuad }
        NumberAnimation { target: body; property: "y"; to: 0; duration: 380; easing.type: Easing.OutBounce }
    }
    SequentialAnimation {
        running: cell.urgent
        loops: Animation.Infinite
        onRunningChanged: if (!running) body.y = 0
        NumberAnimation { target: body; property: "y"; to: -26; duration: 200; easing.type: Easing.OutQuad }
        NumberAnimation { target: body; property: "y"; to: 0; duration: 420; easing.type: Easing.OutBounce }
        NumberAnimation { target: body; property: "y"; to: -14; duration: 150; easing.type: Easing.OutQuad }
        NumberAnimation { target: body; property: "y"; to: 0; duration: 300; easing.type: Easing.OutBounce }
        PauseAnimation { duration: 900 }
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: cell.tapped()
    }

    // directory folders: shallow listing, used for existence check and the mini-grid preview
    Loader {
        active: cell.node.type === "dir" && cell.info.present
        sourceComponent: FolderListModel {
            id: flm
            folder: "file://" + Items.expand(cell.node.path)
            showDirsFirst: true
            showDotAndDotDot: false
            showHidden: false
            sortField: FolderListModel.Name
            function refresh() {
                const out = [];
                for (let i = 0; i < Math.min(4, count); i++) {
                    const name = get(i, "fileName");
                    const isDir = get(i, "fileIsDir");
                    const path = get(i, "filePath");
                    out.push({
                        icon: (!isDir && Items.hasThumb(name)) ? "file://" + path : Items.fileIcon(name, isDir),
                        dim: false
                    });
                }
                cell.dirPreview = out;
            }
            onStatusChanged: refresh()
            onCountChanged: refresh()
        }
    }
}
