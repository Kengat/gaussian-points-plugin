import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: overlay
    color: "transparent"

    readonly property var appState: controller.state || ({})
    readonly property var dialog: appState.dialog || ({})
    readonly property bool showRename: dialog.kind === "rename"
    readonly property bool showDelete: dialog.kind === "delete"
    readonly property bool active: showRename || showDelete

    // Pass mouse events through when no dialog is active
    enabled: active

    // Click-away area (transparent — no dim)
    MouseArea {
        anchors.fill: parent
        visible: active
        onClicked: controller.closeDialog()
    }

    // ════════════════════════════════════════
    //  Rename Dialog
    // ════════════════════════════════════════
    // Rename card shadow
    Rectangle {
        visible: showRename
        anchors.centerIn: parent
        width: renameCard.width + 40
        height: renameCard.height + 40
        radius: 30
        color: "#60000000"
    }

    Rectangle {
        id: renameCard
        visible: showRename
        anchors.centerIn: parent
        width: 380
        height: renameContent.implicitHeight
        radius: 16
        color: "#18181B"
        border.color: "#28FFFFFF"
        border.width: 1

        // Block clicks from passing through
        MouseArea { anchors.fill: parent }

        ColumnLayout {
            id: renameContent
            anchors.left: parent.left
            anchors.right: parent.right
            spacing: 0

            // Header
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 52
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 24
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Rename Project"
                    color: "#FFFFFF"
                    font.pixelSize: 15
                    font.weight: 700
                    font.family: "Outfit"
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: "#14FFFFFF" }

            // Body
            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: 24
                Layout.topMargin: 20
                Layout.bottomMargin: 24
                spacing: 12

                Text {
                    text: "Project name"
                    color: "#A1A1AA"
                    font.pixelSize: 12
                    font.weight: 500
                    font.family: "Outfit"
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 40
                    radius: 10
                    color: "#0AFFFFFF"
                    border.color: renameInput.activeFocus ? "#FF5400" : "#19FFFFFF"
                    border.width: 1
                    Behavior on border.color { ColorAnimation { duration: 150 } }

                    TextInput {
                        id: renameInput
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        verticalAlignment: TextInput.AlignVCenter
                        color: "#FFFFFF"
                        selectionColor: "#FF5400"
                        selectedTextColor: "#FFFFFF"
                        font.pixelSize: 13
                        font.weight: 500
                        font.family: "Outfit"
                        clip: true
                        onAccepted: {
                            if (text.trim().length > 0) {
                                controller.renameProject(overlay.dialog.projectId, text.trim())
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 10

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        width: cancelRenameText.implicitWidth + 32
                        height: 34
                        radius: 8
                        color: cancelRenameArea.containsMouse ? "#14FFFFFF" : "#0AFFFFFF"
                        border.color: "#19FFFFFF"; border.width: 1
                        Behavior on color { ColorAnimation { duration: 120 } }
                        Text {
                            id: cancelRenameText
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: "#A1A1AA"
                            font.pixelSize: 12
                            font.weight: 600
                            font.family: "Outfit"
                        }
                        MouseArea {
                            id: cancelRenameArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: controller.closeDialog()
                        }
                    }

                    Rectangle {
                        width: confirmRenameText.implicitWidth + 32
                        height: 34
                        radius: 8
                        color: confirmRenameArea.containsMouse ? "#FF6A1A" : "#FF5400"
                        Behavior on color { ColorAnimation { duration: 120 } }
                        Text {
                            id: confirmRenameText
                            anchors.centerIn: parent
                            text: "Rename"
                            color: "#FFFFFF"
                            font.pixelSize: 12
                            font.weight: 700
                            font.family: "Outfit"
                        }
                        MouseArea {
                            id: confirmRenameArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (renameInput.text.trim().length > 0) {
                                    controller.renameProject(overlay.dialog.projectId, renameInput.text.trim())
                                }
                            }
                        }
                    }
                }
            }
        }

        // Auto-focus input when dialog opens
        onVisibleChanged: {
            if (visible) {
                renameInput.text = overlay.dialog.projectName || ""
                renameInput.selectAll()
                renameInput.forceActiveFocus()
            }
        }
    }

    // ════════════════════════════════════════
    //  Delete Dialog
    // ════════════════════════════════════════
    // Delete card shadow
    Rectangle {
        visible: showDelete
        anchors.centerIn: parent
        width: deleteCard.width + 40
        height: deleteCard.height + 40
        radius: 30
        color: "#60000000"
    }

    Rectangle {
        id: deleteCard
        visible: showDelete
        anchors.centerIn: parent
        width: 400
        height: deleteContent.implicitHeight
        radius: 16
        color: "#18181B"
        border.color: "#28FFFFFF"
        border.width: 1

        MouseArea { anchors.fill: parent }

        ColumnLayout {
            id: deleteContent
            anchors.left: parent.left
            anchors.right: parent.right
            spacing: 0

            // Header
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 52
                RowLayout {
                    anchors.left: parent.left
                    anchors.leftMargin: 24
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10
                    Rectangle {
                        width: 28; height: 28; radius: 8
                        color: "#1AF43F5E"
                        IconImage {
                            anchors.centerIn: parent
                            iconName: "trash-2"; iconSize: 14; tone: "rose"
                        }
                    }
                    Text {
                        text: "Delete Project"
                        color: "#FFFFFF"
                        font.pixelSize: 15
                        font.weight: 700
                        font.family: "Outfit"
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: "#14FFFFFF" }

            // Body
            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: 24
                Layout.topMargin: 20
                Layout.bottomMargin: 24
                spacing: 16

                Text {
                    Layout.fillWidth: true
                    text: "Are you sure you want to delete \"" + (overlay.dialog.projectName || "") + "\"? This removes the project entry but keeps files on disk."
                    color: "#A1A1AA"
                    font.pixelSize: 13
                    font.weight: 400
                    font.family: "Outfit"
                    wrapMode: Text.WordWrap
                    lineHeight: 1.4
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 10

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        width: cancelDeleteText.implicitWidth + 32
                        height: 34
                        radius: 8
                        color: cancelDeleteArea.containsMouse ? "#14FFFFFF" : "#0AFFFFFF"
                        border.color: "#19FFFFFF"; border.width: 1
                        Behavior on color { ColorAnimation { duration: 120 } }
                        Text {
                            id: cancelDeleteText
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: "#A1A1AA"
                            font.pixelSize: 12
                            font.weight: 600
                            font.family: "Outfit"
                        }
                        MouseArea {
                            id: cancelDeleteArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: controller.closeDialog()
                        }
                    }

                    Rectangle {
                        width: confirmDeleteText.implicitWidth + 32
                        height: 34
                        radius: 8
                        color: confirmDeleteArea.containsMouse ? "#FF4060" : "#F43F5E"
                        Behavior on color { ColorAnimation { duration: 120 } }
                        Text {
                            id: confirmDeleteText
                            anchors.centerIn: parent
                            text: "Delete"
                            color: "#FFFFFF"
                            font.pixelSize: 12
                            font.weight: 700
                            font.family: "Outfit"
                        }
                        MouseArea {
                            id: confirmDeleteArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: controller.deleteProject(overlay.dialog.projectId)
                        }
                    }
                }
            }
        }
    }

    // Handle Escape key
    Keys.onEscapePressed: {
        if (active) controller.closeDialog()
    }
    focus: active
}
