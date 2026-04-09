import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#050505"
    border.color: "#14FFFFFF"
    border.width: 1

    // Drag area for frameless window
    MouseArea {
        anchors.fill: parent
        property point clickPos
        onPressed: function(mouse) { clickPos = Qt.point(mouse.x, mouse.y); controller.startDrag(mapToGlobal(mouse.x, mouse.y).x, mapToGlobal(mouse.x, mouse.y).y) }
        onPositionChanged: function(mouse) { if (pressed) controller.updateDrag(mapToGlobal(mouse.x, mouse.y).x, mapToGlobal(mouse.x, mouse.y).y) }
        onReleased: controller.endDrag()
        onDoubleClicked: controller.maximizeWindow()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 0
        spacing: 14

        RowLayout {
            id: brandRow
            spacing: 8

            IconImage { iconName: "box"; tone: "accent"; iconSize: 14 }

            Text {
                text: "Gaussian Points Studio"
                color: "#E4E4E7"
                font.pixelSize: 13
                font.weight: 700
                font.family: "Outfit"
            }
        }

        Repeater {
            model: ["File", "Edit", "View", "Tools", "Window", "Help"]
            delegate: Item {
                implicitWidth: label.implicitWidth
                implicitHeight: label.implicitHeight
                Layout.alignment: Qt.AlignVCenter
                property bool hovered: false

                Text {
                    id: label
                    anchors.centerIn: parent
                    text: modelData
                    color: parent.hovered ? "#FFFFFF" : "#A1A1AA"
                    font.pixelSize: 12
                    font.weight: 600
                    font.family: "Outfit"
                    Behavior on color { ColorAnimation { duration: 200 } }
                }

                HoverHandler { onHoveredChanged: parent.hovered = hovered; cursorShape: Qt.PointingHandCursor }
            }
        }

        Item { Layout.fillWidth: true }

        RowLayout {
            spacing: 6
            Layout.alignment: Qt.AlignVCenter
            property bool hovered: false

            IconImage { iconName: "users"; tone: "muted"; iconSize: 14 }
            Text {
                text: "Community Gallery"
                color: parent.hovered ? "#FFFFFF" : "#A1A1AA"
                font.pixelSize: 12
                font.weight: 600
                font.family: "Outfit"
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            HoverHandler { onHoveredChanged: parent.hovered = hovered; cursorShape: Qt.PointingHandCursor }
        }

        Rectangle {
            radius: 8
            color: "#1A00F0FF"
            border.color: "#3300F0FF"
            border.width: 1
            implicitWidth: signInRow.implicitWidth + 16
            implicitHeight: 24

            RowLayout {
                id: signInRow
                anchors.centerIn: parent
                spacing: 6

                IconImage { iconName: "user-circle-2"; tone: "cyan"; iconSize: 14 }
                Text {
                    text: "Sign In"
                    color: "#00F0FF"
                    font.pixelSize: 12
                    font.weight: 700
                    font.family: "Outfit"
                }
            }

            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }

        // Window controls
        RowLayout {
            spacing: 0
            Layout.alignment: Qt.AlignVCenter

            Repeater {
                model: [
                    { action: "minimize", icon: "minus",    hoverBg: "#19FFFFFF" },
                    { action: "maximize", icon: "maximize", hoverBg: "#19FFFFFF" },
                    { action: "close",    icon: "x",        hoverBg: "#E81123"   }
                ]
                delegate: Rectangle {
                    required property var modelData
                    property bool hovered: false
                    width: 46
                    height: 32
                    color: hovered ? modelData.hoverBg : "transparent"
                    Behavior on color { ColorAnimation { duration: 150 } }

                    IconImage {
                        anchors.centerIn: parent
                        iconName: modelData.icon
                        tone: parent.hovered && modelData.action === "close" ? "white" : parent.hovered ? "white" : "muted"
                        iconSize: 14
                    }

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false
                        onClicked: {
                            if (modelData.action === "minimize") controller.minimizeWindow()
                            else if (modelData.action === "maximize") controller.maximizeWindow()
                            else controller.closeWindow()
                        }
                    }
                }
            }
        }
    }
}
