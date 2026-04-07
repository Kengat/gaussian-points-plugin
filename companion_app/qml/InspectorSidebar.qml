import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Effects

Rectangle {
    id: root
    color: "#E90A0A0D"
    border.color: "#14FFFFFF"
    border.width: 1

    readonly property var detail: (controller.state || {}).activeDetail || ({})
    readonly property var statusPanel: detail.statusPanel || ({})
    readonly property var propertiesPanel: detail.propertiesPanel || ({})
    readonly property var exportPanel: detail.exportPanel || ({})
    readonly property var photos: detail.photos || []
    readonly property var videoTile: detail.videoTile || ({})
    readonly property var consoleRows: detail.consoleRows || []
    readonly property bool consoleRunning: !!detail.consoleRunning
    property int currentTab: 0

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 56
            color: "transparent"
            border.color: "#14FFFFFF"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                anchors.topMargin: 16
                spacing: 0

                Repeater {
                    model: [
                        { text: "Inspect", icon: "activity", index: 0 },
                        { text: "Console", icon: "terminal", index: 1 },
                        { text: "Dataset", icon: "database", index: 2 }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        property bool hovered: false
                        Layout.fillWidth: true
                        Layout.preferredHeight: 40
                        color: "transparent"

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 2
                            color: root.currentTab === modelData.index ? "#FF5400" : "transparent"
                        }

                        RowLayout {
                            anchors.centerIn: parent
                            spacing: 8

                            IconImage {
                                iconName: modelData.icon
                                tone: root.currentTab === modelData.index ? "white" : hovered ? "white" : "muted"
                                iconSize: 16
                            }
                            Text {
                                text: modelData.text
                                color: root.currentTab === modelData.index ? "#FFFFFF" : hovered ? "#D4D4D8" : "#71717A"
                                font.pixelSize: 12
                                font.weight: 700
                                font.family: "Outfit"
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onEntered: parent.hovered = true
                            onExited: parent.hovered = false
                            onClicked: root.currentTab = modelData.index
                        }
                    }
                }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.currentTab

            ScrollView {
                id: inspectScroll
                clip: true
                contentWidth: availableWidth
                leftPadding: 20
                rightPadding: 20
                topPadding: 20
                bottomPadding: 20

                ColumnLayout {
                    width: inspectScroll.availableWidth
                    spacing: 24

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: "JOB PROGRESS"
                            color: "#71717A"
                            font.pixelSize: 10
                            font.weight: 800
                            font.family: "Outfit"
                        }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        radius: 16
                        color: "#111116"
                        border.color: "#0DFFFFFF"
                        border.width: 1
                        implicitHeight: 176
                        clip: true

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 4
                            color: "#0DFFFFFF"

                            Rectangle {
                                width: parent.width * ((statusPanel.progress || 0) / 100.0)
                                height: parent.height
                                color: (statusPanel.progress || 0) >= 100 ? "#16C784" : "#FF5400"
                            }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 20
                            spacing: 0
                            Item { Layout.preferredHeight: 8 }
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: statusPanel.progressLabel || "0%"
                                    color: "#FFFFFF"
                                    font.pixelSize: 30
                                    font.weight: Font.Light
                                    font.family: "Consolas"

                                }
                                Item { Layout.fillWidth: true }
                                Rectangle {
                                    width: 40
                                    height: 40
                                    radius: 20
                                    color: "#050505"
                                    border.color: (statusPanel.progress || 0) >= 100 ? "#4D16C784" : "#4DFF5400"
                                    border.width: 2
                                    IconImage {
                                        anchors.centerIn: parent
                                        iconName: (statusPanel.progress || 0) >= 100 ? "check-circle-2" : "clock-3"
                                        tone: (statusPanel.progress || 0) >= 100 ? "green" : "accent"
                                        iconSize: 20
                                    }
                                }
                            }
                            Text {
                                text: statusPanel.statusText || "Ready"
                                color: "#A1A1AA"
                                font.pixelSize: 12
                                font.family: "DM Sans 36pt"
                                Layout.topMargin: 2
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: 16
                                spacing: 10
                                Repeater {
                                    model: [
                                        { label: "Time Total", value: statusPanel.timeTotal || "--" },
                                        { label: "Final Loss", value: statusPanel.finalLoss || "--" }
                                    ]
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: 60
                                        radius: 10
                                        color: "#80050505"
                                        border.color: "#0DFFFFFF"
                                        border.width: 1
                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 10
                                            anchors.rightMargin: 10
                                            anchors.topMargin: 10
                                            anchors.bottomMargin: 10
                                            spacing: 4
                                            Text {
                                                text: modelData.label
                                                color: "#71717A"
                                                font.pixelSize: 12
                                                font.family: "DM Sans 36pt"
                                                font.weight: 500
                                            }
                                            Text {
                                                text: modelData.value
                                                color: modelData.label === "Final Loss" ? "#00F0FF" : "#FFFFFF"
                                                font.pixelSize: 16
                                                font.weight: 500
                                                font.family: "Consolas"
            
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "MODEL PROPERTIES"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.5 }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                    }

                    Repeater {
                        model: propertiesPanel.items || []
                        delegate: Rectangle {
                            required property var modelData
                            property bool hovered: false
                            Layout.fillWidth: true
                            implicitHeight: content.implicitHeight + 8
                            color: hovered ? "#0DFFFFFF" : "transparent"
                            radius: 8

                            ColumnLayout {
                                id: content
                                anchors.fill: parent
                                anchors.leftMargin: 4
                                anchors.rightMargin: 4
                                anchors.topMargin: 4
                                anchors.bottomMargin: 4
                                spacing: 4

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: modelData.label || ""
                                        color: "#A1A1AA"
                                        font.pixelSize: 13
                                        font.weight: 500
                                        font.family: "DM Sans 36pt"
                                    }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        visible: !!modelData.copyable
                                        property bool hovered: false
                                        radius: 8
                                        color: hovered ? "#14FFFFFF" : "#0DFFFFFF"
                                        border.color: hovered ? "#33FFFFFF" : "#0DFFFFFF"
                                        border.width: 1
                                        implicitWidth: copyRow.implicitWidth + 16
                                        implicitHeight: 26

                                        RowLayout {
                                            id: copyRow
                                            anchors.centerIn: parent
                                            spacing: 6
                                            Text { text: "Copy Path"; color: "#E4E4E7"; font.pixelSize: 11; font.weight: 700; font.family: "Outfit" }
                                            IconImage { iconName: "copy"; tone: parent.parent.hovered ? "accent" : "muted"; iconSize: 12 }
                                        }

                                        MouseArea {
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            onEntered: parent.hovered = true
                                            onExited: parent.hovered = false
                                            onClicked: controller.copyText(modelData.value || "")
                                        }
                                    }
                                    Text {
                                        visible: !modelData.copyable
                                        text: modelData.value || ""
                                        color: "#F4F4F5"
                                        font.pixelSize: 14
                                        font.family: "Consolas"
    
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                onEntered: parent.hovered = true
                                onExited: parent.hovered = false
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        property bool hovered: false
                        clip: true
                        radius: 16
                        color: "#751E1618"
                        border.color: "#14FFFFFF"
                        border.width: 1
                        implicitHeight: exportCol.implicitHeight + 40

                        Item {
                            id: glowContainer
                            width: 140
                            height: 140
                            anchors.top: parent.top
                            anchors.right: parent.right
                            anchors.topMargin: -40
                            anchors.rightMargin: -40

                            Rectangle {
                                id: glowSource
                                anchors.centerIn: parent
                                width: 80
                                height: 80
                                radius: 40
                                color: parent.parent.hovered ? "#00F0FF" : "#FF5400"
                                opacity: parent.parent.hovered ? 0.15 : 0.05
                                visible: false
                            }
                            MultiEffect {
                                source: glowSource
                                anchors.fill: parent
                                blurEnabled: true
                                blurMax: 64
                                blur: 1.0
                            }
                        }

                        ColumnLayout {
                            id: exportCol
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 20
                            spacing: 8
                            RowLayout {
                                spacing: 8
                                IconImage { iconName: "download"; tone: "cyan"; iconSize: 16 }
                                Text { text: "Export Options"; color: "#FFFFFF"; font.pixelSize: 14; font.weight: 700; font.family: "Outfit" }
                            }
                            Text {
                                text: exportPanel.body || ""
                                color: "#A1A1AA"
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                                font.family: "DM Sans 36pt"
                                lineHeight: 1.4
                                lineHeightMode: Text.ProportionalHeight
                                Layout.fillWidth: true
                                Layout.bottomMargin: 4
                            }

                            Repeater {
                                model: [
                                    { text: "Export to .ply file", accent: false, icon: "download" },
                                    { text: "Export directly to SketchUp", accent: true, icon: "arrow-up-right" }
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    property bool hovered: false
                                    radius: 10
                                    color: modelData.accent ? "transparent" : hovered ? "#14FFFFFF" : "#06FFFFFF"
                                    border.color: modelData.accent ? "transparent" : hovered ? "#33FFFFFF" : "#0DFFFFFF"
                                    border.width: 1
                                    implicitHeight: 38
                                    Layout.fillWidth: true
                                    opacity: ((detail.toolbar || {}).canExport) ? 1.0 : 0.45

                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0.0; color: modelData.accent ? (hovered ? "#FF6A22" : "#FF5400") : "transparent" }
                                        GradientStop { position: 1.0; color: modelData.accent ? (hovered ? "#FF4AA0" : "#FF2E93") : "transparent" }
                                    }

                                    RowLayout {
                                        anchors.centerIn: parent
                                        spacing: 8
                                        IconImage { iconName: modelData.icon; tone: modelData.accent ? "white" : "muted"; iconSize: 16 }
                                        Text {
                                            text: modelData.text
                                            color: "#FFFFFF"
                                            font.pixelSize: 13
                                            font.weight: 700
                                            font.family: "Outfit"
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: ((detail.toolbar || {}).canExport)
                                        hoverEnabled: true
                                        onEntered: parent.hovered = true
                                        onExited: parent.hovered = false
                                        onClicked: controller.openExportFolder()
                                    }
                                }
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onEntered: parent.hovered = true
                            onExited: parent.hovered = false
                        }
                    }
                }
            }

            ScrollView {
                id: consoleScroll
                clip: true
                contentWidth: availableWidth
                leftPadding: 20
                rightPadding: 20
                topPadding: 20
                bottomPadding: 20

                ColumnLayout {
                    width: consoleScroll.availableWidth
                    spacing: 16

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "LIVE OUTPUT"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.5 }
                        Rectangle { Layout.preferredWidth: 48; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            radius: 8
                            color: "#101720"
                            border.color: "#214D3D"
                            border.width: 1
                            implicitWidth: liveRow.implicitWidth + 16
                            implicitHeight: 28
                            RowLayout {
                                id: liveRow
                                anchors.centerIn: parent
                                spacing: 6
                                Rectangle { width: 6; height: 6; radius: 3; color: "#34D399" }
                                Text { text: "LIVE"; color: "#34D399"; font.pixelSize: 11; font.weight: 700; font.family: "Outfit" }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        clip: true
                        radius: 14
                        color: "#020202"
                        border.color: "#14FFFFFF"
                        border.width: 1
                        implicitHeight: 580

                        Column {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 2

                            Repeater {
                                model: consoleRows
                                delegate: Rectangle {
                                    required property var modelData
                                    property bool hovered: false
                                    clip: true
                                    width: parent.width
                                    implicitHeight: rowWrap.implicitHeight + 4
                                    radius: 4
                                    color: hovered ? "#0AFFFFFF" : "transparent"

                                    RowLayout {
                                        id: rowWrap
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.leftMargin: 4
                                        anchors.rightMargin: 4
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 8

                                        Text {
                                            visible: !!modelData.timestamp
                                            text: modelData.timestamp
                                            color: "#52525B"
                                            font.family: "Consolas"
                                            font.pixelSize: 11
        
                                            Layout.alignment: Qt.AlignTop
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: modelData.kind === "system" ? "> " + modelData.message : modelData.message
                                            wrapMode: Text.WrapAnywhere
                                            color: modelData.kind === "system" ? "#71717A" : modelData.kind === "success" ? "#34D399" : modelData.kind === "metric" ? "#00F0FF" : "#D4D4D8"
                                            font.family: "Consolas"
                                            font.pixelSize: 11
        
                                            font.italic: modelData.kind === "system"
                                            lineHeight: 1.9
                                            lineHeightMode: Text.ProportionalHeight
                                            Layout.alignment: Qt.AlignTop
                                        }
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        onEntered: parent.hovered = true
                                        onExited: parent.hovered = false
                                    }
                                }
                            }

                            Row {
                                spacing: 4
                                Text {
                                    text: "C:\\Users\\illia\\Companion>"
                                    color: "#52525B"
                                    font.family: "Consolas"
                                    font.pixelSize: 11

                                }
                                Text {
                                    text: consoleRunning ? "..." : " "
                                    color: "#A1A1AA"
                                    font.family: "Consolas"
                                    font.pixelSize: 11

                                }
                            }
                        }
                    }
                }
            }

            ScrollView {
                id: datasetScroll
                clip: true
                contentWidth: availableWidth
                leftPadding: 20
                rightPadding: 20
                topPadding: 20
                bottomPadding: 20

                ColumnLayout {
                    width: datasetScroll.availableWidth
                    spacing: 16

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "CAPTURED MEDIA"; color: "#71717A"; font.pixelSize: 10; font.weight: 800; font.family: "Outfit"; font.letterSpacing: 1.5 }
                        Rectangle { Layout.preferredWidth: 48; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            color: "transparent"
                            implicitWidth: clearAllLabel.implicitWidth
                            implicitHeight: clearAllLabel.implicitHeight
                            property bool hovered: false
                            Text {
                                id: clearAllLabel
                                text: "Clear All"
                                color: parent.hovered ? "#FFFFFF" : "#00F0FF"
                                font.pixelSize: 11
                                font.family: "Outfit"
                            }
                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                onEntered: parent.hovered = true
                                onExited: parent.hovered = false
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: mediaFlow.implicitHeight

                        Flow {
                            id: mediaFlow
                            width: parent.width
                            spacing: 8
                            property real tileSize: Math.floor((width - 16) / 3)

                            Repeater {
                                model: photos
                                delegate: Rectangle {
                                    required property var modelData
                                    property bool hovered: false
                                    width: mediaFlow.tileSize
                                    height: mediaFlow.tileSize
                                    clip: true
                                    radius: 8
                                    color: "#050505"
                                    border.color: hovered ? "#33FFFFFF" : "#14FFFFFF"
                                    border.width: 1
                                    Image {
                                        anchors.fill: parent
                                        fillMode: Image.PreserveAspectCrop
                                        source: modelData.url || ""
                                        asynchronous: true
                                        cache: false
                                        opacity: hovered ? 1.0 : 0.6
                                    }
                                    Rectangle {
                                        visible: hovered
                                        anchors.top: parent.top
                                        anchors.right: parent.right
                                        anchors.margins: 4
                                        width: 18
                                        height: 18
                                        radius: 4
                                        color: "#99000000"
                                        IconImage { anchors.centerIn: parent; iconName: "trash-2"; tone: "white"; iconSize: 12 }
                                    }
                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.bottom: parent.bottom
                                        anchors.margins: 4
                                        radius: 3
                                        color: "#99000000"
                                        implicitWidth: fileName.implicitWidth + 8
                                        implicitHeight: 14
                                        Text {
                                            id: fileName
                                            anchors.centerIn: parent
                                            text: modelData.name || ""
                                            color: "#CCFFFFFF"
                                            font.pixelSize: 8
                                            font.family: "Consolas"
        
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        onEntered: parent.hovered = true
                                        onExited: parent.hovered = false
                                    }
                                }
                            }

                            Rectangle {
                                width: mediaFlow.tileSize
                                height: mediaFlow.tileSize
                                visible: photos.length > 0
                                clip: true
                                radius: 8
                                color: "#050505"
                                border.color: "#14FFFFFF"
                                border.width: 1
                                Image {
                                    anchors.fill: parent
                                    source: videoTile.url || ""
                                    fillMode: Image.PreserveAspectCrop
                                    opacity: 0.4
                                }
                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: 4
                                    IconImage { iconName: "film"; tone: "white"; iconSize: 20 }
                                    Text { text: videoTile.fps || "30 FPS"; color: "#FFFFFF"; font.pixelSize: 9; font.weight: 700; font.family: "Outfit" }
                                }
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.bottom: parent.bottom
                                    anchors.margins: 4
                                    radius: 3
                                    color: "#99000000"
                                    implicitWidth: videoName.implicitWidth + 8
                                    implicitHeight: 14
                                    Text {
                                        id: videoName
                                        anchors.centerIn: parent
                                        text: videoTile.name || "GOPR0042.MP4"
                                        color: "#00F0FF"
                                        font.pixelSize: 8
                                        font.family: "Consolas"
    
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 52
                        property bool hovered: false
                        radius: 14
                        color: hovered ? "#0DFFFFFF" : "#05FFFFFF"
                        border.color: hovered ? "#4DFFFFFF" : "#1AFFFFFF"
                        border.width: 1

                        RowLayout {
                            anchors.centerIn: parent
                            spacing: 8
                            IconImage { iconName: "image"; tone: parent.parent.hovered ? "white" : "muted"; iconSize: 16 }
                            Text {
                                text: "Add Photos or Video"
                                color: parent.parent.hovered ? "#FFFFFF" : "#A1A1AA"
                                font.pixelSize: 13
                                font.weight: 700
                                font.family: "Outfit"
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onEntered: parent.hovered = true
                            onExited: parent.hovered = false
                            onClicked: controller.addPhotosDialog()
                        }
                    }
                }
            }
        }
    }
}
