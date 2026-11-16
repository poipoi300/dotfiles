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
    // PluginComponent supplies the same BasePill as Clock, Media and Weather.
    // Segments add only native text and interaction layers, never another box.
    component Segment: Item {
        id: segment
        objectName: "audioEq-" + value
        required property string label
        required property string value
        required property string action
        readonly property bool active: root.selected === value
        implicitWidth: caption.implicitWidth + Theme.spacingM * 2
        implicitHeight: root.widgetThickness
        width: implicitWidth
        height: implicitHeight

        StyledText {
            id: caption
            anchors.centerIn: parent
            text: segment.label
            color: segment.active ? Theme.primary : Theme.widgetTextColor
            font.pixelSize: Theme.barTextSize(root.barThickness, root.barConfig?.fontScale, root.barConfig?.maximizeWidgetText)
            font.weight: segment.active ? Font.DemiBold : Font.Normal
        }
        StateLayer {
            stateColor: Theme.widgetTextColor
            cornerRadius: Theme.cornerRadius
            tooltipText: (segment.value === "headphones" ? "Headphones" : "Speakers") + (segment.active ? " EQ · click to turn off" : " EQ")
            onClicked: root.select(segment.action)
        }
    }
    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS
            Segment { label: "H"; value: "headphones"; action: "toggle-headphones" }
            Segment { label: "S"; value: "speakers"; action: "toggle-speakers" }
        }
    }
    verticalBarPill: Component {
        Column {
            spacing: Theme.spacingXS
            Segment { label: "H"; value: "headphones"; action: "toggle-headphones" }
            Segment { label: "S"; value: "speakers"; action: "toggle-speakers" }
        }
    }
}
