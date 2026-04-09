import QtQuick 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#050505"
    border.color: "#000000"
    border.width: 1

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: 16
        anchors.bottomMargin: 16
        spacing: 9

        Repeater {
            model: [
                { icon: "mouse-pointer-2", active: true },
                { icon: "move", active: false },
                { icon: "rotate-ccw", active: false }
            ]
            delegate: Rectangle {
                property bool hovered: false
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 36
                Layout.preferredHeight: 36
                radius: 10
                color: modelData.active ? "#1AFF5400" : hovered ? "#19FFFFFF" : "transparent"
                border.color: modelData.active ? "#4DFF5400" : "transparent"
                border.width: 1
                Behavior on color { ColorAnimation { duration: 200 } }

                IconImage {
                    anchors.centerIn: parent
                    iconName: modelData.icon
                    tone: modelData.active ? "accent" : hovered ? "white" : "muted"
                    iconSize: 16
                }

                HoverHandler { onHoveredChanged: parent.hovered = hovered; cursorShape: Qt.PointingHandCursor }
            }
        }

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 24
            Layout.preferredHeight: 1
            color: "#1AFFFFFF"
            Layout.topMargin: 4
            Layout.bottomMargin: 4
        }

        Repeater {
            model: [
                { icon: "box-select", active: false },
                { icon: "pipette", active: false }
            ]
            delegate: Rectangle {
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
                    iconName: modelData.icon
                    tone: hovered ? "white" : "muted"
                    iconSize: 16
                }

                HoverHandler { onHoveredChanged: parent.hovered = hovered; cursorShape: Qt.PointingHandCursor }
            }
        }

        Item { Layout.fillHeight: true }

        Rectangle {
            property bool hovered: false
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 36
            Layout.preferredHeight: 36
            radius: 10
            color: hovered ? "#19FFFFFF" : "transparent"
            Behavior on color { ColorAnimation { duration: 200 } }

            IconImage { anchors.centerIn: parent; iconName: "settings"; tone: parent.hovered ? "white" : "muted"; iconSize: 16 }
            HoverHandler { onHoveredChanged: parent.hovered = hovered; cursorShape: Qt.PointingHandCursor }
        }
    }
}
