import QtQuick

// One entry in the folder panel: 48 px icon and up to two lines of name.
Item {
    id: cell

    property string label: ""
    property string source: ""
    property var preview: []
    property bool dim: false
    property bool thumb: false
    signal tapped()

    Rectangle {
        anchors.fill: parent
        anchors.margins: 3
        radius: Theme.radius
        color: tap.pressed ? Theme.pressed : Theme.hover
        opacity: hover.hovered ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 90 } }
    }

    DockIcon {
        x: (cell.width - 128) / 2
        y: 10 + 24 - 64
        scale: 48 / 128
        source: cell.source
        label: cell.label
        preview: cell.preview
        dim: cell.dim
        thumb: cell.thumb
    }

    Text {
        x: 6
        y: 66
        width: cell.width - 12
        height: 32
        text: cell.label
        color: hover.hovered ? Theme.accent : Theme.text
        opacity: cell.dim ? 0.5 : 0.95
        font.pixelSize: 12
        font.family: Theme.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignTop
        wrapMode: Text.Wrap
        maximumLineCount: 2
        elide: Text.ElideRight
    }

    HoverHandler { id: hover }
    TapHandler { id: tap; onTapped: cell.tapped() }
}
