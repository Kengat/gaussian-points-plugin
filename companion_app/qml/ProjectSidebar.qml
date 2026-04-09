import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: sidebar
    color: "#E90A0A0D"
    border.color: "#14FFFFFF"
    border.width: 1

    readonly property var appState: controller.state || ({})

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.bottomMargin: 16
        spacing: 12

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            color: "transparent"

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: "PROJECT EXPLORER"
                color: "#E4E4E7"
                font.pixelSize: 11
                font.weight: 800
                font.family: "Outfit"
                font.letterSpacing: 1
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: -16
                anchors.rightMargin: -16
                height: 1
                color: "#14FFFFFF"
            }
        }

        Rectangle {
            property bool hovered: false
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            radius: 10
            color: hovered ? "#14FFFFFF" : "#08FFFFFF"
            border.color: "#19FFFFFF"
            border.width: 1

            Behavior on color { ColorAnimation { duration: 180 } }

            RowLayout {
                anchors.centerIn: parent
                spacing: 8
                IconImage { iconName: "camera"; tone: parent.parent.hovered ? "white" : "muted"; iconSize: 16 }
                Text {
                    text: "New Project"
                    color: "#FFFFFF"
                    font.pixelSize: 14
                    font.weight: 700
                    font.family: "Outfit"
                }
            }

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: parent.hovered = true
                onExited: parent.hovered = false
                onClicked: controller.newProjectDialog()
            }
        }

        Text {
            text: "RECENT LOCAL PROJECTS"
            color: "#71717A"
            font.pixelSize: 10
            font.weight: 800
            font.family: "Outfit"
            font.letterSpacing: 1.5
            Layout.topMargin: 4
            Layout.leftMargin: 4
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            model: appState.projects || []

            delegate: Rectangle {
                id: delegateRoot
                required property var modelData
                readonly property bool active: (appState.activeProjectId || "") === modelData.id
                property bool hovered: delegateHover.hovered
                property bool showActions: hovered && modelData.status !== "running" && modelData.status !== "queued"
                property color bgStart: active ? "#1AFF5400" : hovered ? "#0AFFFFFF" : "transparent"
                property color bgEnd: active ? "#00FF5400" : hovered ? "#0AFFFFFF" : "transparent"
                property color borderCol: active ? "#33FF5400" : "transparent"
                width: ListView.view.width
                height: 52
                radius: 12
                border.color: borderCol
                border.width: 1

                Behavior on bgStart { ColorAnimation { duration: 200 } }
                Behavior on bgEnd { ColorAnimation { duration: 200 } }
                Behavior on borderCol { ColorAnimation { duration: 200 } }

                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: bgStart }
                    GradientStop { position: 1.0; color: bgEnd }
                }

                HoverHandler { id: delegateHover }

                Rectangle {
                    visible: active
                    x: -1
                    y: parent.height * 0.2
                    width: 3
                    height: parent.height * 0.6
                    radius: 2
                    color: "#FF5400"

                    Rectangle {
                        anchors.centerIn: parent
                        width: 10
                        height: parent.height + 4
                        radius: 5
                        color: "#30FF5400"
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: controller.selectProject(modelData.id)
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 12

                    IconImage {
                        iconName: "folder"
                        tone: active ? "accent" : hovered ? "white" : "muted"
                        iconSize: 16
                    }

                    Text {
                        Layout.fillWidth: true
                        text: modelData.name
                        property color textCol: active ? "#FFFFFF" : hovered ? "#FFFFFF" : "#A1A1AA"
                        color: textCol
                        font.pixelSize: 13
                        font.weight: 500
                        font.family: "Outfit"
                        elide: Text.ElideRight
                        Behavior on textCol { ColorAnimation { duration: 200 } }
                    }

                    Item {
                        width: 56
                        height: parent.height
                        Layout.alignment: Qt.AlignVCenter

                        // Status indicator
                        RowLayout {
                            anchors.centerIn: parent
                            spacing: 6
                            opacity: showActions ? 0.0 : 1.0
                            Behavior on opacity { NumberAnimation { duration: 150 } }

                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: modelData.status === "ready" ? "#16C784" : modelData.status === "failed" ? "#F43F5E" : modelData.status === "running" || modelData.status === "queued" ? "#FF5400" : "#71717A"
                            }

                            Text {
                                text: String(modelData.status || "idle").toUpperCase()
                                property color textCol: active ? "#FFFFFF" : "#71717A"
                                color: textCol
                                font.pixelSize: 10
                                font.weight: 700
                                font.family: "Consolas"
                                Behavior on textCol { ColorAnimation { duration: 200 } }
                            }
                        }

                        // Action buttons
                        Row {
                            anchors.centerIn: parent
                            spacing: 2
                            opacity: showActions ? 1.0 : 0.0
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: 150 } }

                            Rectangle {
                                width: 24; height: 24; radius: 6
                                color: renameArea.containsMouse ? "#19FFFFFF" : "transparent"
                                Behavior on color { ColorAnimation { duration: 120 } }
                                IconImage {
                                    anchors.centerIn: parent
                                    iconName: "pen-square"; iconSize: 14
                                    tone: renameArea.containsMouse ? "white" : "muted"
                                }
                                MouseArea {
                                    id: renameArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: controller.showRenameDialog(modelData.id, modelData.name)
                                }
                            }

                            Rectangle {
                                width: 24; height: 24; radius: 6
                                color: deleteArea.containsMouse ? "#33F43F5E" : "transparent"
                                Behavior on color { ColorAnimation { duration: 120 } }
                                IconImage {
                                    anchors.centerIn: parent
                                    iconName: "trash-2"; iconSize: 14
                                    tone: deleteArea.containsMouse ? "rose" : "muted"
                                }
                                MouseArea {
                                    id: deleteArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: controller.showDeleteDialog(modelData.id, modelData.name)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
