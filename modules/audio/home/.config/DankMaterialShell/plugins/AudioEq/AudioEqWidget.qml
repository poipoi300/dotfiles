import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root
    layerNamespacePlugin: "audio-eq"
    readonly property string executable: Quickshell.env("HOME") + "/.local/bin/audio-eq"
    readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state"
    property string selected: "off"
    property bool refreshPending: false

    function refresh() {
        if (statusProcess.running) {
            refreshPending = true;
        } else {
            refreshPending = false;
            statusProcess.running = true;
        }
    }
    function select(command) {
        Quickshell.execDetached([executable, command]);
    }
    Component.onCompleted: refresh()

    FileView {
        path: root.stateHome + "/audio-eq/state"
        watchChanges: true
        onFileChanged: { reload(); root.refresh(); }
        onLoaded: root.refresh()
    }
    Process {
        id: statusProcess
        command: [root.executable, "status"]
        onExited: { if (root.refreshPending) root.refresh(); }
        stdout: StdioCollector {
            onStreamFinished: {
                try { root.selected = JSON.parse(text).output; }
                catch (error) { console.warn("Audio EQ status:", error); }
            }
        }
    }
    component Segment: Rectangle {
        objectName: "audioEq-" + value
        required property string label
        required property string value
        required property string action
        width: 32
        height: 26
        radius: 5
        color: root.selected === value ? Theme.primary : Theme.surfaceContainerHigh
        StyledText {
            anchors.centerIn: parent
            text: parent.label
            color: root.selected === parent.value ? Theme.primaryText : Theme.surfaceText
            font.pixelSize: Theme.fontSizeSmall
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.select(parent.action)
        }
    }
    horizontalBarPill: Component {
        Row {
            spacing: 2
            Segment { label: "H"; value: "headphones"; action: "toggle-headphones" }
            Segment { label: "off"; value: "off"; action: "off" }
            Segment { label: "S"; value: "speakers"; action: "toggle-speakers" }
        }
    }
    verticalBarPill: Component {
        Column {
            spacing: 2
            Segment { label: "H"; value: "headphones"; action: "toggle-headphones" }
            Segment { label: "off"; value: "off"; action: "off" }
            Segment { label: "S"; value: "speakers"; action: "toggle-speakers" }
        }
    }
}
