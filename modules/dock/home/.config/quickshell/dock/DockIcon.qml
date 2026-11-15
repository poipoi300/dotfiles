import QtQuick

// One icon drawn at a fixed 128 px reference size; callers scale it with `scale`, which
// keeps resizing on the GPU. Shows, in order: an image, a mini-grid folder preview, or a
// letter tile when nothing resolves.
Item {
    id: root

    property string source: ""        // icon url ("" = none)
    property string label: ""
    property var preview: []          // [{icon, dim}] shown as a 2x2 grid when there is no source
    property bool dim: false
    property bool thumb: false        // source is a file thumbnail: crop to a rounded square

    readonly property real ref: 128
    width: ref
    height: ref
    opacity: dim ? 0.35 : 1

    readonly property bool imageOk: img.status === Image.Ready

    Image {
        id: img
        anchors.fill: parent
        visible: root.source !== "" && status !== Image.Error
        source: root.source
        sourceSize: Qt.size(root.thumb ? 192 : 128, root.thumb ? 192 : 128)
        fillMode: root.thumb ? Image.PreserveAspectCrop : Image.PreserveAspectFit
        asynchronous: true
        mipmap: true
        smooth: true
    }

    // mini-grid folder preview
    Rectangle {
        anchors.fill: parent
        anchors.margins: 6
        radius: 26
        visible: root.source === "" && root.preview.length > 0 || (root.source !== "" && img.status === Image.Error && root.preview.length > 0)
        color: "#38ffffff"
        border.width: 3
        border.color: "#40ffffff"
        Grid {
            anchors.centerIn: parent
            columns: 2
            spacing: 8
            Repeater {
                model: root.preview.slice(0, 4)
                delegate: Item {
                    required property var modelData
                    width: 44
                    height: 44
                    opacity: modelData.dim ? 0.4 : 1
                    Image {
                        anchors.fill: parent
                        source: parent.modelData.icon
                        sourceSize: Qt.size(64, 64)
                        asynchronous: true
                        mipmap: true
                    }
                }
            }
        }
    }

    // nothing to show: letter tile
    Rectangle {
        anchors.fill: parent
        anchors.margins: 8
        radius: 28
        color: "#3a3a52"
        visible: (root.source === "" || img.status === Image.Error) && root.preview.length === 0
        Text {
            anchors.centerIn: parent
            text: root.label.length ? root.label[0].toUpperCase() : "?"
            color: "#cdd6f4"
            font.pixelSize: 64
            font.bold: true
        }
    }
}
