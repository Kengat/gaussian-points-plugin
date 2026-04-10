import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    color: "#050505"
    border.color: "#14FFFFFF"
    border.width: 1

    component TopMenuTrigger: Item {
        id: topTrigger
        required property string label
        required property string menuKey
        property bool hovered: false
        implicitWidth: triggerLabel.implicitWidth + 20
        implicitHeight: 24

        Rectangle {
            anchors.fill: parent
            radius: 6
            color: controller.activeMenuName === topTrigger.menuKey ? "#19FFFFFF" : topTrigger.hovered ? "#0DFFFFFF" : "transparent"
        }

        Text {
            id: triggerLabel
            anchors.centerIn: parent
            text: topTrigger.label
            color: controller.activeMenuName === topTrigger.menuKey || topTrigger.hovered ? "#FFFFFF" : "#A1A1AA"
            font.pixelSize: 12
            font.weight: 500
            font.family: "Outfit"
        }

        HoverHandler {
            cursorShape: Qt.PointingHandCursor
            onHoveredChanged: {
                topTrigger.hovered = hovered
                if (hovered && controller.menuPopupVisible) {
                    var point = topTrigger.mapToGlobal(0, topTrigger.height + 2)
                    controller.showMenu(topTrigger.menuKey, point.x, point.y)
                }
            }
        }

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                var point = topTrigger.mapToGlobal(0, topTrigger.height + 2)
                controller.showMenu(topTrigger.menuKey, point.x, point.y)
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        property point clickPos
        onPressed: function(mouse) {
            clickPos = Qt.point(mouse.x, mouse.y)
            controller.startDrag(mapToGlobal(mouse.x, mouse.y).x, mapToGlobal(mouse.x, mouse.y).y)
        }
        onPositionChanged: function(mouse) {
            if (pressed)
                controller.updateDrag(mapToGlobal(mouse.x, mouse.y).x, mapToGlobal(mouse.x, mouse.y).y)
        }
        onReleased: controller.endDrag()
        onDoubleClicked: controller.maximizeWindow()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 0
        spacing: 14

        RowLayout {
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

        RowLayout {
            spacing: 2
            Layout.alignment: Qt.AlignVCenter

            TopMenuTrigger { label: "File"; menuKey: "file" }
            TopMenuTrigger { label: "Edit"; menuKey: "edit" }
            TopMenuTrigger { label: "View"; menuKey: "view" }
            TopMenuTrigger { label: "Tools"; menuKey: "tools" }
            TopMenuTrigger { label: "Window"; menuKey: "window" }
            TopMenuTrigger { label: "Help"; menuKey: "help" }
        }

        Item { Layout.fillWidth: true }

        RowLayout {
            spacing: 6
            Layout.alignment: Qt.AlignVCenter
            property bool hovered: false

            Rectangle {
                width: 1
                height: 18
                color: "#0DFFFFFF"
            }

            RowLayout {
                spacing: 6
                property bool hovered: false

                IconImage { iconName: "users"; tone: parent.hovered ? "white" : "muted"; iconSize: 14 }

                Text {
                    text: "Community Gallery"
                    color: parent.hovered ? "#FFFFFF" : "#A1A1AA"
                    font.pixelSize: 12
                    font.weight: 600
                    font.family: "Outfit"
                }

                HoverHandler {
                    onHoveredChanged: parent.hovered = hovered
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        Rectangle {
            radius: 6
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

        RowLayout {
            spacing: 0
            Layout.alignment: Qt.AlignVCenter

            Repeater {
                model: [
                    { action: "minimize", icon: "minus", hoverBg: "#19FFFFFF" },
                    { action: "maximize", icon: "maximize", hoverBg: "#19FFFFFF" },
                    { action: "close", icon: "x", hoverBg: "#E81123" }
                ]

                delegate: Rectangle {
                    required property var modelData
                    property bool hovered: false

                    width: 46
                    height: 32
                    color: hovered ? modelData.hoverBg : "transparent"

                    IconImage {
                        anchors.centerIn: parent
                        iconName: modelData.icon
                        tone: parent.hovered ? "white" : "muted"
                        iconSize: 14
                    }

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false
                        onClicked: {
                            if (parent.modelData.action === "minimize")
                                controller.minimizeWindow()
                            else if (parent.modelData.action === "maximize")
                                controller.maximizeWindow()
                            else
                                controller.closeWindow()
                        }
                    }
                }
            }
        }
    }
}
