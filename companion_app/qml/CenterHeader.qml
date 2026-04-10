import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#800A0A0D"
    border.color: "#14FFFFFF"
    border.width: 1

    readonly property var detail: (controller.state || {}).activeDetail || ({})
    readonly property var header: detail.header || ({})
    readonly property var toolbar: detail.toolbar || ({})

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 22
        anchors.rightMargin: 16
        spacing: 12

        Text {
            text: header.title || "No project"
            color: "#FFFFFF"
            font.pixelSize: 18
            font.weight: 700
            font.family: "Outfit"
        }

        Rectangle {
            radius: 8
            border.width: 1
            border.color: header.status === "ready" ? "#3316C784" : header.status === "failed" ? "#33F43F5E" : header.status === "running" || header.status === "queued" ? "#33FF5400" : "#3371717A"
            color: header.status === "ready" ? "#1A16C784" : header.status === "failed" ? "#1AF43F5E" : header.status === "running" || header.status === "queued" ? "#1AFF5400" : "#1A71717A"
            implicitWidth: badgeRow.implicitWidth + 20
            implicitHeight: 28

            RowLayout {
                id: badgeRow
                anchors.centerIn: parent
                spacing: 6

                IconImage {
                    iconName: "activity"
                    tone: header.status === "ready" ? "green" : header.status === "failed" ? "rose" : "accent"
                    iconSize: 14
                }

                Text {
                    text: String(header.status || "idle").toUpperCase()
                    color: header.status === "ready" ? "#16C784" : header.status === "failed" ? "#F43F5E" : header.status === "running" || header.status === "queued" ? "#FF5400" : "#71717A"
                    font.pixelSize: 11
                    font.weight: 700
                    font.family: "Consolas"
                }
            }
        }

        Item { Layout.fillWidth: true }

        Rectangle {
            id: datasetButton
            property bool hovered: false
            radius: 10
            implicitHeight: 34
            implicitWidth: datasetRow.implicitWidth + 32
            color: hovered ? "#0DFFFFFF" : "transparent"
            border.color: "#1AFFFFFF"
            border.width: 1
            Behavior on color { ColorAnimation { duration: 200 } }

            RowLayout {
                id: datasetRow
                anchors.centerIn: parent
                spacing: 8
                IconImage { iconName: "image"; tone: datasetButton.hovered ? "white" : "muted"; iconSize: 16 }
                Text {
                    text: "Add Dataset"
                    color: "#FFFFFF"
                    font.pixelSize: 13
                    font.weight: 600
                    font.family: "Outfit"
                }
            }

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onEntered: parent.hovered = true
                onExited: parent.hovered = false
                onClicked: controller.addPhotosDialog()
            }
        }

        Rectangle {
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            Layout.preferredWidth: 1
            Layout.preferredHeight: 24
            color: "#1AFFFFFF"
        }

        Rectangle {
            color: "#66000000"
            border.color: "#0DFFFFFF"
            border.width: 1
            radius: 12
            implicitHeight: 40
            implicitWidth: controlsRow.implicitWidth + 12

            RowLayout {
                id: controlsRow
                anchors.centerIn: parent
                spacing: 6

                Rectangle {
                    id: trainButton
                    property bool hovered: false
                    radius: 8
                    implicitHeight: 32
                    implicitWidth: trainRow.implicitWidth + 28
                    color: hovered ? "#FF6A22" : "#FF5400"
                    opacity: toolbar.canTrain ? 1.0 : 0.45
                    Behavior on color { ColorAnimation { duration: 200 } }

                    RowLayout {
                        id: trainRow
                        anchors.centerIn: parent
                        spacing: 8
                        IconImage { iconName: "play"; tone: "white"; iconSize: 16 }
                        Text {
                            text: "Train Model"
                            color: "#FFFFFF"
                            font.pixelSize: 14
                            font.weight: 700
                            font.family: "Outfit"
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: !!toolbar.canTrain
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false
                        onClicked: controller.startTrainingDialog(false)
                    }
                }

                Rectangle {
                    id: restartButton
                    property bool hovered: false
                    radius: 8
                    implicitWidth: 32
                    implicitHeight: 32
                    color: hovered ? "#14FFFFFF" : "transparent"
                    opacity: toolbar.canTrain ? 1.0 : 0.45
                    Behavior on color { ColorAnimation { duration: 200 } }

                    IconImage {
                        anchors.centerIn: parent
                        iconName: "rotate-ccw"
                        tone: restartButton.hovered ? "white" : "muted"
                        iconSize: 16
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: !!toolbar.canTrain
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onEntered: restartButton.hovered = true
                        onExited: restartButton.hovered = false
                        onClicked: controller.restartTrainingDialog()
                    }
                }

                Rectangle {
                    id: stopButton
                    property bool hovered: false
                    radius: 8
                    implicitWidth: 32
                    implicitHeight: 32
                    color: hovered ? "#14FFFFFF" : "transparent"
                    opacity: toolbar.canStop ? 1.0 : 0.45
                    Behavior on color { ColorAnimation { duration: 200 } }

                    IconImage {
                        anchors.centerIn: parent
                        iconName: "square"
                        tone: stopButton.hovered ? "white" : "rose"
                        iconSize: 16
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: !!toolbar.canStop
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onEntered: stopButton.hovered = true
                        onExited: stopButton.hovered = false
                        onClicked: controller.stopTraining()
                    }
                }
            }
        }
    }
}
