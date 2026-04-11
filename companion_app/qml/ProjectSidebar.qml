import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: sidebar
    color: "#E90A0A0D"
    border.color: "#14FFFFFF"
    border.width: 1

    readonly property var appState: controller.state || ({})
    readonly property string activeToolName: controller.activeTool || "projects"
    property string selectionMode: "point"
    property string moveStep: "0.1"
    property bool snapToGrid: true
    property bool clipEnabled: true
    property string colorTarget: "albedo"

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.bottomMargin: 16
        visible: activeToolName === "projects"
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

    Item {
        anchors.fill: parent
        visible: activeToolName !== "projects"

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 64
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: activeToolName === "select" ? "#0D00F0FF" : activeToolName === "move" ? "#0D16C784" : activeToolName === "transform" ? "#0DA855F7" : activeToolName === "clip" ? "#0DF59E0B" : "#0DFF2E93" }
                GradientStop { position: 1.0; color: "transparent" }
            }

            RowLayout {
                anchors.left: parent.left
                anchors.leftMargin: 24
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                IconImage {
                    iconName: activeToolName === "select" ? "mouse-pointer-2" : activeToolName === "move" ? "move" : activeToolName === "transform" ? "rotate-ccw" : activeToolName === "clip" ? "box-select" : "pipette"
                    tone: activeToolName === "select" ? "cyan" : activeToolName === "move" ? "green" : activeToolName === "color" ? "rose" : "white"
                    iconSize: 16
                }

                Text {
                    text: activeToolName === "select" ? "SELECT TOOL" : activeToolName === "move" ? "MOVE TOOL" : activeToolName === "transform" ? "TRANSFORM" : activeToolName === "clip" ? "CLIPPING BOX" : "COLOR PICKER"
                    color: "#FFFFFF"
                    font.pixelSize: 11
                    font.weight: 800
                    font.family: "Outfit"
                    font.letterSpacing: 1
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: "#14FFFFFF"
            }
        }

        Flickable {
            id: toolsFlick
            anchors.top: parent.top
            anchors.topMargin: 64
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            clip: true
            contentWidth: width
            contentHeight: toolsBody.height + 24

            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
                background: Item {}
                contentItem: Rectangle { implicitWidth: 5; radius: 999; color: "#1FFFFFFF" }
            }

            Column {
                id: toolsBody
                width: toolsFlick.width
                spacing: 20
                topPadding: 20
                bottomPadding: 24

                Item {
                    visible: activeToolName === "select"
                    width: parent.width
                    height: visible ? 210 : 0

                    Column {
                        x: 20
                        width: parent.width - 40
                        spacing: 12

                        Text { text: "SELECTION MODE"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Rectangle {
                            width: parent.width
                            height: 40
                            radius: 10
                            color: "#66000000"
                            border.color: "#14FFFFFF"
                            border.width: 1

                            Row {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 4

                                Repeater {
                                    model: [{ key: "point", label: "Point" }, { key: "brush", label: "Brush" }, { key: "lasso", label: "Lasso" }]

                                    delegate: Rectangle {
                                        width: (parent.width - 8) / 3
                                        height: parent.height
                                        radius: 8
                                        color: selectionMode === modelData.key ? "#19FFFFFF" : "transparent"
                                        border.color: selectionMode === modelData.key ? "#1FFFFFFF" : "transparent"
                                        border.width: 1

                                        Text { anchors.centerIn: parent; text: modelData.label; color: selectionMode === modelData.key ? "#FFFFFF" : "#71717A"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: selectionMode = modelData.key }
                                    }
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            Text { text: "BRUSH SIZE"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }
                            Item { width: parent.width - brushValue.width - 70; height: 1 }
                            Text { id: brushValue; text: "15 px"; color: "#00F0FF"; font.pixelSize: 11; font.weight: 700; font.family: "JetBrains Mono" }
                        }

                        Rectangle {
                            width: parent.width
                            height: 6
                            radius: 3
                            color: "#27272A"
                            Rectangle { width: parent.width * 0.42; height: parent.height; radius: parent.radius; color: "#00F0FF" }
                            Rectangle { x: parent.width * 0.42 - 6; y: -3; width: 12; height: 12; radius: 6; color: "#FFFFFF" }
                        }

                        Rectangle { width: parent.width; height: 1; color: "#0DFFFFFF" }

                        Repeater {
                            model: [{ label: "Invert Selection", key: "CTRL+I", danger: false }, { label: "Select All", key: "CTRL+A", danger: false }, { label: "Clear Selection", key: "ESC", danger: true }]

                            delegate: Rectangle {
                                width: parent.width
                                height: 40
                                radius: 10
                                color: modelData.danger ? "#1AF43F5E" : "#08FFFFFF"
                                border.color: modelData.danger ? "#33F43F5E" : "#14FFFFFF"
                                border.width: 1

                                Text { anchors.left: parent.left; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: modelData.danger ? "#FB7185" : "#FFFFFF"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                                Text { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: modelData.key; color: "#71717A"; font.pixelSize: 9; font.weight: 700; font.family: "JetBrains Mono" }
                            }
                        }
                    }
                }

                Item {
                    visible: activeToolName === "move"
                    width: parent.width
                    height: visible ? 220 : 0

                    Column {
                        x: 20
                        width: parent.width - 40
                        spacing: 10

                        Text { text: "POSITION (RELATIVE)"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Repeater {
                            model: ["X", "Y", "Z"]

                            delegate: Rectangle {
                                width: parent.width
                                height: 38
                                radius: 8
                                color: "#050505"
                                border.color: "#19FFFFFF"
                                border.width: 1

                                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData; color: "#16C784"; font.pixelSize: 10; font.weight: 800; font.family: "JetBrains Mono" }
                                Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: "0.000"; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 500; font.family: "JetBrains Mono" }
                            }
                        }

                        Row {
                            width: parent.width
                            Text { text: "Snap to Grid"; color: "#D4D4D8"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                            Item { width: parent.width - 108; height: 1 }

                            Rectangle {
                                width: 32
                                height: 18
                                radius: 9
                                color: snapToGrid ? "#16C784" : "#27272A"
                                Rectangle { x: snapToGrid ? 15 : 3; y: 3; width: 12; height: 12; radius: 6; color: "#FFFFFF"; Behavior on x { NumberAnimation { duration: 140 } } }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: snapToGrid = !snapToGrid }
                            }
                        }

                        Row {
                            width: parent.width
                            Text { text: "Step Size"; color: "#D4D4D8"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                            Item { width: parent.width - 118; height: 1 }

                            Repeater {
                                model: ["0.1", "1.0"]

                                delegate: Rectangle {
                                    width: 42
                                    height: 26
                                    radius: 7
                                    color: moveStep === modelData ? "#19FFFFFF" : "#050505"
                                    border.color: moveStep === modelData ? "#1FFFFFFF" : "#14FFFFFF"
                                    border.width: 1
                                    Text { anchors.centerIn: parent; text: modelData; color: moveStep === modelData ? "#FFFFFF" : "#A1A1AA"; font.pixelSize: 10; font.weight: 700; font.family: "JetBrains Mono" }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: moveStep = modelData }
                                }
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: 42
                            radius: 10
                            color: "#1A16C784"
                            border.color: "#4D16C784"
                            border.width: 1
                            Text { anchors.centerIn: parent; text: "Apply Transform"; color: "#16C784"; font.pixelSize: 12; font.weight: 700; font.family: "Outfit" }
                        }
                    }
                }

                Item {
                    visible: activeToolName === "transform"
                    width: parent.width
                    height: visible ? 180 : 0

                    Column {
                        x: 20
                        width: parent.width - 40
                        spacing: 12

                        Text { text: "ROTATION (DEGREES)"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Row {
                            width: parent.width
                            spacing: 8

                            Repeater {
                                model: ["PITCH (X)", "YAW (Y)", "ROLL (Z)"]

                                delegate: Rectangle {
                                    width: (parent.width - 16) / 3
                                    height: 62
                                    radius: 8
                                    color: "#050505"
                                    border.color: "#19FFFFFF"
                                    border.width: 1

                                    Text { anchors.top: parent.top; anchors.topMargin: 9; anchors.horizontalCenter: parent.horizontalCenter; text: modelData; color: "#A855F7"; font.pixelSize: 9; font.weight: 700; font.family: "Outfit" }
                                    Text { anchors.bottom: parent.bottom; anchors.bottomMargin: 10; anchors.horizontalCenter: parent.horizontalCenter; text: "0"; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 500; font.family: "JetBrains Mono" }
                                }
                            }
                        }

                        Text { text: "SCALE (MULTIPLIER)"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Rectangle {
                            width: parent.width
                            height: 40
                            radius: 8
                            color: "#050505"
                            border.color: "#19FFFFFF"
                            border.width: 1

                            Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: "XYZ"; color: "#71717A"; font.pixelSize: 9; font.weight: 800; font.family: "JetBrains Mono" }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "1.00"; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 500; font.family: "JetBrains Mono" }
                        }

                        Rectangle {
                            width: parent.width
                            height: 42
                            radius: 10
                            color: "#27272A"
                            border.color: "#19FFFFFF"
                            border.width: 1
                            Text { anchors.centerIn: parent; text: "Reset Transformations"; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                        }
                    }
                }

                Item {
                    visible: activeToolName === "clip"
                    width: parent.width
                    height: visible ? 180 : 0

                    Column {
                        x: 20
                        width: parent.width - 40
                        spacing: 12

                        Row {
                            width: parent.width
                            Text { text: "Enable Clipping"; color: "#D4D4D8"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                            Item { width: parent.width - 116; height: 1 }

                            Rectangle {
                                width: 32
                                height: 18
                                radius: 9
                                color: clipEnabled ? "#F59E0B" : "#27272A"
                                Rectangle { x: clipEnabled ? 15 : 3; y: 3; width: 12; height: 12; radius: 6; color: "#FFFFFF"; Behavior on x { NumberAnimation { duration: 140 } } }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: clipEnabled = !clipEnabled }
                            }
                        }

                        Text { text: "BOUNDS"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Repeater {
                            model: [{ label: "MIN", value: "-1.20 / -0.84 / -0.62" }, { label: "MAX", value: "1.20 / 0.84 / 0.62" }]

                            delegate: Rectangle {
                                width: parent.width
                                height: 40
                                radius: 8
                                color: "#050505"
                                border.color: "#19FFFFFF"
                                border.width: 1

                                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: "#F59E0B"; font.pixelSize: 9; font.weight: 800; font.family: "JetBrains Mono" }
                                Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData.value; color: "#FFFFFF"; font.pixelSize: 11; font.weight: 500; font.family: "JetBrains Mono" }
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: 42
                            radius: 10
                            color: "#08FFFFFF"
                            border.color: "#14FFFFFF"
                            border.width: 1
                            Text { anchors.centerIn: parent; text: "Reset Clip Box"; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                        }
                    }
                }

                Item {
                    visible: activeToolName === "color"
                    width: parent.width
                    height: visible ? 220 : 0

                    Column {
                        x: 20
                        width: parent.width - 40
                        spacing: 12

                        Rectangle {
                            width: parent.width
                            height: 92
                            radius: 14
                            color: "#050505"
                            border.color: "#19FFFFFF"
                            border.width: 1

                            Rectangle {
                                x: 16
                                y: 16
                                width: 60
                                height: 60
                                radius: 14
                                gradient: Gradient {
                                    orientation: Gradient.Vertical
                                    GradientStop { position: 0.0; color: "#FF8BCB" }
                                    GradientStop { position: 1.0; color: "#FF2E93" }
                                }
                            }

                            Text { anchors.left: parent.left; anchors.leftMargin: 92; anchors.top: parent.top; anchors.topMargin: 22; text: "Sampled Color"; color: "#FFFFFF"; font.pixelSize: 14; font.weight: 700; font.family: "Outfit" }
                            Text { anchors.left: parent.left; anchors.leftMargin: 92; anchors.top: parent.top; anchors.topMargin: 48; text: "#FF2E93"; color: "#FF2E93"; font.pixelSize: 12; font.weight: 700; font.family: "JetBrains Mono" }
                        }

                        Text { text: "TARGET"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.4 }

                        Row {
                            width: parent.width
                            spacing: 8

                            Repeater {
                                model: [{ key: "albedo", label: "Albedo" }, { key: "opacity", label: "Opacity" }]

                                delegate: Rectangle {
                                    width: (parent.width - 8) / 2
                                    height: 36
                                    radius: 9
                                    color: colorTarget === modelData.key ? "#19FFFFFF" : "#050505"
                                    border.color: colorTarget === modelData.key ? "#1FFFFFFF" : "#14FFFFFF"
                                    border.width: 1

                                    Text { anchors.centerIn: parent; text: modelData.label; color: colorTarget === modelData.key ? "#FFFFFF" : "#A1A1AA"; font.pixelSize: 12; font.weight: 600; font.family: "Outfit" }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: colorTarget = modelData.key }
                                }
                            }
                        }

                        Repeater {
                            model: [{ label: "R", value: "255", tone: "#FB7185" }, { label: "G", value: "46", tone: "#34D399" }, { label: "B", value: "147", tone: "#00F0FF" }]

                            delegate: Rectangle {
                                width: parent.width
                                height: 38
                                radius: 8
                                color: "#050505"
                                border.color: "#19FFFFFF"
                                border.width: 1

                                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: modelData.tone; font.pixelSize: 10; font.weight: 800; font.family: "JetBrains Mono" }
                                Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData.value; color: "#FFFFFF"; font.pixelSize: 12; font.weight: 500; font.family: "JetBrains Mono" }
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: 42
                            radius: 10
                            color: "#1AFF2E93"
                            border.color: "#4DFF2E93"
                            border.width: 1
                            Text { anchors.centerIn: parent; text: "Apply Sampled Color"; color: "#FF2E93"; font.pixelSize: 12; font.weight: 700; font.family: "Outfit" }
                        }
                    }
                }
            }
        }
    }
}
