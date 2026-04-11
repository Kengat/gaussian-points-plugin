import QtQuick 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#050505"
    border.color: "#000000"
    border.width: 1

    readonly property string activeToolName: controller.activeTool || "projects"

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: 16
        anchors.bottomMargin: 16
        spacing: 9

        Repeater {
            model: [
                { tool: "projects", icon: "folder" },
                { tool: "__projects_sep__", icon: "" },
                { tool: "select", icon: "mouse-pointer-2" },
                { tool: "move", icon: "move" },
                { tool: "transform", icon: "rotate-ccw" },
                { tool: "__tools_sep__", icon: "" },
                { tool: "clip", icon: "box-select" },
                { tool: "color", icon: "pipette" }
            ]

            delegate: Item {
                readonly property bool separator: String(modelData.tool).indexOf("__") === 0

                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: separator ? 16 : 36
                Layout.preferredHeight: separator ? 10 : 36

                Rectangle {
                    id: railButton
                    anchors.centerIn: parent
                    width: separator ? 16 : 36
                    height: separator ? 1 : 36
                    radius: separator ? 1 : 10
                    color: separator ? "#1AFFFFFF" : hovered ? "#19FFFFFF" : "transparent"
                    border.color: separator ? "transparent" : active ? "#66FF5400" : "transparent"
                    border.width: separator ? 0 : 1
                    property bool active: !separator && activeToolName === modelData.tool
                    property bool hovered: false
                    gradient: !separator && active ? railGradient : undefined

                    Gradient {
                        id: railGradient
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: "#1AFF5400" }
                        GradientStop { position: 1.0; color: "#26FF5400" }
                    }

                    Behavior on color { ColorAnimation { duration: 200 } }
                    Behavior on border.color { ColorAnimation { duration: 200 } }

                    IconImage {
                        visible: !separator
                        anchors.centerIn: parent
                        iconName: modelData.icon
                        tone: railButton.active ? "accent" : railButton.hovered ? "white" : "muted"
                        iconSize: 16
                    }

                    MouseArea {
                        visible: !separator
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onEntered: railButton.hovered = true
                        onExited: railButton.hovered = false
                        onClicked: controller.setActiveTool(modelData.tool)
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }

        Rectangle {
            id: settingsButton
            property bool hovered: false
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 36
            Layout.preferredHeight: 36
            radius: 10
            color: hovered ? "#19FFFFFF" : "transparent"
            border.color: "transparent"
            border.width: 1
            Behavior on color { ColorAnimation { duration: 200 } }

            IconImage {
                anchors.centerIn: parent
                iconName: "settings"
                tone: settingsButton.hovered ? "white" : "muted"
                iconSize: 16
            }

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: settingsButton.hovered = true
                onExited: settingsButton.hovered = false
            }
        }
    }
}
