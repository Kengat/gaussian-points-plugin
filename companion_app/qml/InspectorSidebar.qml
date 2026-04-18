import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Effects
import Qt5Compat.GraphicalEffects

Rectangle {
    id: root
    color: "#E90A0A0D"
    border.color: "#14FFFFFF"
    border.width: 1

    readonly property var detail: (controller.state || {}).activeDetail || ({})
    readonly property var statusPanel: detail.statusPanel || ({})
    readonly property var trainingLossPanel: detail.trainingLoss || ({ points: [], minValue: "--", topLabel: "--", midLabel: "--", bottomLabel: "--" })
    readonly property var propertiesPanel: detail.propertiesPanel || ({})
    readonly property var basicPropertyItems: propertiesPanel.basicItems || propertiesPanel.items || []
    readonly property var propertySummaryCards: propertiesPanel.summaryCards || []
    readonly property var propertySections: propertiesPanel.sections || []
    readonly property var exportPanel: detail.exportPanel || ({})
    readonly property var photos: detail.photos || []
    readonly property var videoTile: detail.videoTile || ({})
    readonly property var liveMonitor: detail.liveMonitor || ({ state: "idle", label: "REST", detail: "No active worker.", showStopPrompt: false })
    readonly property var consoleRows: detail.consoleRows || []
    readonly property bool consoleRunning: !!detail.consoleRunning
    readonly property string liveState: liveMonitor.state || "idle"
    readonly property string liveLabel: liveMonitor.label || liveLabelFallback(liveState)
    readonly property string sketchUpExportState: exportPanel.sketchupState || "idle"
    property int currentTab: 0
    property bool advancedPropertiesOpen: false
    property int lossHoverIndex: -1

    function liveLabelFallback(state) {
        if (state === "live") return "LIVE"
        if (state === "stale") return "QUIET"
        if (state === "silent") return "CHECK"
        if (state === "failed") return "FAILED"
        return "REST"
    }

    function liveTextColor(state) {
        if (state === "live") return "#34D399"
        if (state === "stale") return "#FBBF24"
        if (state === "silent" || state === "failed") return "#FB7185"
        return "#E4E4E7"
    }

    function liveDotColor(state) {
        if (state === "live") return "#34D399"
        if (state === "stale") return "#FBBF24"
        if (state === "silent" || state === "failed") return "#FB7185"
        return "#D4D4D8"
    }

    function livePanelColor(state) {
        if (state === "live") return "#1A10B981"
        if (state === "stale") return "#1AFBBF24"
        if (state === "silent" || state === "failed") return "#1AFB7185"
        return "#0CFFFFFF"
    }

    function liveBorderColor(state) {
        if (state === "live") return "#3334D399"
        if (state === "stale") return "#40FBBF24"
        if (state === "silent" || state === "failed") return "#4DFB7185"
        return "#26FFFFFF"
    }

    function sketchUpExportLabel(state) {
        if (state === "loading") return "Processing..."
        if (state === "success") return "Export Complete"
        if (state === "failed") return "Export Failed"
        return "Export directly to SketchUp"
    }

    function sketchUpExportIcon(state) {
        if (state === "loading") return "loader-2"
        if (state === "success") return "check-circle-2"
        if (state === "failed") return "alert-triangle"
        return "arrow-up-right"
    }

    function sketchUpExportIconTone(state) {
        if (state === "loading") return "accent"
        if (state === "success") return "green"
        if (state === "failed") return "rose"
        return "white"
    }

    function propertyToneColor(tone) {
        if (tone === "cyan") return "#00F0FF"
        if (tone === "green") return "#16C784"
        if (tone === "rose") return "#FF2E93"
        if (tone === "muted") return "#A1A1AA"
        return "#FF5400"
    }

    function propertyToneBorder(tone) {
        if (tone === "cyan") return "#3300F0FF"
        if (tone === "green") return "#3316C784"
        if (tone === "rose") return "#33FF2E93"
        if (tone === "muted") return "#14FFFFFF"
        return "#33FF5400"
    }

    function hoveredLossPoint() {
        var points = trainingLossPanel.points || []
        if (lossHoverIndex < 0 || lossHoverIndex >= points.length)
            return null
        return points[lossHoverIndex]
    }

    onTrainingLossPanelChanged: {
        if (typeof lossChartCanvas !== "undefined" && lossChartCanvas)
            lossChartCanvas.requestPaint()
    }

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
                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
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
                ScrollBar.vertical.policy: ScrollBar.AlwaysOff
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

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: 176

                        Rectangle {
                            id: progressContent
                            anchors.fill: parent
                            radius: 16
                            color: "#111116"
                            border.color: "#0DFFFFFF"
                            border.width: 1
                            visible: false
                            layer.enabled: true

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

                        Rectangle {
                            id: progressMask
                            anchors.fill: parent
                            radius: 16
                            visible: false
                        }

                        OpacityMask {
                            anchors.fill: parent
                            source: progressContent
                            maskSource: progressMask
                        }
                    }

                    Item {
                        visible: basicPropertyItems.length > 0
                        Layout.fillWidth: true
                        implicitHeight: basicPropertiesColumn.implicitHeight

                        ColumnLayout {
                            id: basicPropertiesColumn
                            anchors.fill: parent
                            spacing: 10

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Text {
                                    text: "MODEL PROPERTIES"
                                    color: "#71717A"
                                    font.pixelSize: 10
                                    font.weight: 800
                                    font.family: "Outfit"
                                    font.letterSpacing: 1.5
                                }
                                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                            }

                            Repeater {
                                model: basicPropertyItems
                                delegate: Rectangle {
                                    required property var modelData
                                    property bool hovered: basicRowMouseArea.containsMouse
                                    Layout.fillWidth: true
                                    implicitHeight: basicRowColumn.implicitHeight + 10
                                    radius: 10
                                    color: hovered ? "#0DFFFFFF" : "transparent"
                                    Behavior on color { ColorAnimation { duration: 160 } }

                                    ColumnLayout {
                                        id: basicRowColumn
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
                                                property bool hovered: copyBasicMouseArea.containsMouse
                                                radius: 8
                                                color: hovered ? "#14FFFFFF" : "#0DFFFFFF"
                                                border.color: hovered ? "#26FFFFFF" : "#0DFFFFFF"
                                                border.width: 1
                                                implicitWidth: basicCopyRow.implicitWidth + 16
                                                implicitHeight: 26
                                                Behavior on color { ColorAnimation { duration: 160 } }
                                                Behavior on border.color { ColorAnimation { duration: 160 } }

                                                RowLayout {
                                                    id: basicCopyRow
                                                    anchors.centerIn: parent
                                                    spacing: 6
                                                    Text { text: "Copy Path"; color: "#E4E4E7"; font.pixelSize: 11; font.weight: 700; font.family: "Outfit" }
                                                    IconImage { iconName: "copy"; tone: parent.parent.hovered ? "accent" : "muted"; iconSize: 12 }
                                                }

                                                MouseArea {
                                                    id: copyBasicMouseArea
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
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

                                        Text {
                                            visible: false
                                            Layout.fillWidth: true
                                            text: modelData.value || ""
                                            color: "#F4F4F5"
                                            font.pixelSize: 12
                                            font.family: "Consolas"
                                            elide: Text.ElideMiddle
                                            maximumLineCount: 1
                                            wrapMode: Text.NoWrap
                                        }
                                    }

                                    MouseArea {
                                        id: basicRowMouseArea
                                        anchors.fill: parent
                                        acceptedButtons: Qt.NoButton
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                            }
                        }
                    }

                    Item {
                        id: exportPanelCard
                        Layout.fillWidth: true
                        implicitHeight: exportCol.implicitHeight + 40
                        property bool exportHovered: false

                        Rectangle {
                            id: exportContent
                            anchors.fill: parent
                            radius: 16
                            color: "#99101016"
                            border.color: "#1AFFFFFF"
                            border.width: 1
                            visible: true
                            layer.enabled: true
                            layer.effect: OpacityMask {
                                maskSource: exportMask
                            }

                            Item {
                                id: glowContainer
                                width: 132
                                height: 132
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.topMargin: -24
                                anchors.rightMargin: -24

                                Rectangle {
                                    id: glowCoreSource
                                    anchors.centerIn: parent
                                    width: 34
                                    height: 34
                                    radius: 17
                                    color: exportPanelCard.exportHovered ? "#00F0FF" : "#FF5400"
                                    visible: false
                                    opacity: 0.88
                                    Behavior on color { ColorAnimation { duration: 280; easing.type: Easing.OutCubic } }
                                }

                                MultiEffect {
                                    source: glowCoreSource
                                    anchors.fill: parent
                                    blurEnabled: true
                                    blurMax: 68
                                    blur: 1.0
                                    opacity: exportPanelCard.exportHovered ? 0.13 : 0.055
                                    Behavior on opacity { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
                                }

                                Rectangle {
                                    id: glowHaloSource
                                    anchors.centerIn: parent
                                    width: 62
                                    height: 62
                                    radius: 31
                                    color: exportPanelCard.exportHovered ? "#00F0FF" : "#FF5400"
                                    visible: false
                                    opacity: 0.30
                                    Behavior on color { ColorAnimation { duration: 280; easing.type: Easing.OutCubic } }
                                }

                                MultiEffect {
                                    source: glowHaloSource
                                    anchors.fill: parent
                                    blurEnabled: true
                                    blurMax: 86
                                    blur: 1.0
                                    opacity: exportPanelCard.exportHovered ? 0.055 : 0.026
                                    Behavior on opacity { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
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

                                Rectangle {
                                    id: exportPlyButton
                                    property bool btnHovered: false
                                    radius: 10
                                    color: btnHovered ? "#14FFFFFF" : "#06FFFFFF"
                                    border.color: btnHovered ? "#33FFFFFF" : "#0DFFFFFF"
                                    border.width: 1
                                    implicitHeight: 40
                                    Layout.fillWidth: true
                                    opacity: ((detail.toolbar || {}).canExport) ? 1.0 : 0.45
                                    Behavior on color { ColorAnimation { duration: 200 } }
                                    Behavior on border.color { ColorAnimation { duration: 200 } }

                                    RowLayout {
                                        anchors.centerIn: parent
                                        spacing: 8
                                        IconImage { iconName: "download"; tone: "muted"; iconSize: 16 }
                                        Text {
                                            text: "Export to .ply file"
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
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.btnHovered = true
                                        onExited: parent.btnHovered = false
                                        onClicked: controller.exportLatestPlyFile()
                                    }
                                }

                                Rectangle {
                                    id: sketchUpExportButton
                                    property bool btnHovered: false
                                    property real shakeOffset: 0
                                    property string exportState: root.sketchUpExportState
                                    radius: 10
                                    border.width: exportState === "idle" ? 0 : 1
                                    border.color: exportState === "loading" ? "#1AFFFFFF"
                                                                              : exportState === "success" ? "#4D16C784"
                                                                              : exportState === "failed" ? "#4DFF2E93"
                                                                              : "transparent"
                                    implicitHeight: 40
                                    Layout.fillWidth: true
                                    opacity: ((detail.toolbar || {}).canExport)
                                             ? ((sketchUpExportButton.exportState === "loading" || sketchUpExportButton.exportState === "success") ? 0.55 : 1.0)
                                             : 0.45
                                    transform: Translate { x: sketchUpExportButton.shakeOffset }
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop {
                                            position: 0.0
                                            color: sketchUpExportButton.exportState === "loading" ? "#0DFFFFFF"
                                                                                                   : sketchUpExportButton.exportState === "success" ? "#3316C784"
                                                                                                   : sketchUpExportButton.exportState === "failed" ? "#33F43F5E"
                                                                                                   : sketchUpExportButton.btnHovered ? "#FF6A22" : "#FF5400"
                                        }
                                        GradientStop {
                                            position: 1.0
                                            color: sketchUpExportButton.exportState === "loading" ? "#0DFFFFFF"
                                                                                                   : sketchUpExportButton.exportState === "success" ? "#2216C784"
                                                                                                   : sketchUpExportButton.exportState === "failed" ? "#22FF2E93"
                                                                                                   : sketchUpExportButton.btnHovered ? "#FF4AA0" : "#FF2E93"
                                        }
                                    }
                                    Behavior on border.color { ColorAnimation { duration: 180 } }

                                    onExportStateChanged: {
                                        if (exportState === "failed") {
                                            shakeAnim.restart()
                                        }
                                        if (exportState !== "loading") {
                                            sketchUpIcon.rotation = 0
                                        }
                                    }

                                    SequentialAnimation {
                                        id: shakeAnim
                                        running: false
                                        NumberAnimation { target: sketchUpExportButton; property: "shakeOffset"; to: -4; duration: 45 }
                                        NumberAnimation { target: sketchUpExportButton; property: "shakeOffset"; to: 4; duration: 70 }
                                        NumberAnimation { target: sketchUpExportButton; property: "shakeOffset"; to: -3; duration: 60 }
                                        NumberAnimation { target: sketchUpExportButton; property: "shakeOffset"; to: 3; duration: 55 }
                                        NumberAnimation { target: sketchUpExportButton; property: "shakeOffset"; to: 0; duration: 45 }
                                    }

                                    RowLayout {
                                        anchors.centerIn: parent
                                        spacing: 8

                                        IconImage {
                                            id: sketchUpIcon
                                            iconName: root.sketchUpExportIcon(sketchUpExportButton.exportState)
                                            tone: root.sketchUpExportIconTone(sketchUpExportButton.exportState)
                                            iconSize: 16
                                            transformOrigin: Item.Center
                                        }
                                        NumberAnimation {
                                            id: sketchUpLoadingSpin
                                            target: sketchUpIcon
                                            property: "rotation"
                                            from: 0
                                            to: 360
                                            duration: 1000
                                            loops: Animation.Infinite
                                            easing.type: Easing.Linear
                                            running: sketchUpExportButton.exportState === "loading"
                                        }
                                        Text {
                                            text: root.sketchUpExportLabel(sketchUpExportButton.exportState)
                                            color: sketchUpExportButton.exportState === "loading" ? "#D4D4D8"
                                                                                                  : sketchUpExportButton.exportState === "success" ? "#86EFAC"
                                                                                                  : sketchUpExportButton.exportState === "failed" ? "#FDA4AF"
                                                                                                  : "#FFFFFF"
                                            font.pixelSize: 13
                                            font.weight: 700
                                            font.family: "Outfit"
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: ((detail.toolbar || {}).canExport) && sketchUpExportButton.exportState === "idle"
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.btnHovered = true
                                        onExited: parent.btnHovered = false
                                        onClicked: controller.exportDirectlyToSketchUp()
                                    }
                                }
                            }
                        }

                        Rectangle {
                            id: exportMask
                            anchors.fill: parent
                            radius: 16
                            visible: false
                        }

                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onEntered: exportPanelCard.exportHovered = true
                            onExited: exportPanelCard.exportHovered = false
                        }
                    }

                    Item {
                        visible: propertySummaryCards.length > 0 || propertySections.length > 0
                        Layout.fillWidth: true
                        implicitHeight: advancedPropertiesColumn.implicitHeight

                        ColumnLayout {
                            id: advancedPropertiesColumn
                            anchors.fill: parent
                            spacing: 8

                            Rectangle {
                                id: advancedToggle
                                Layout.fillWidth: true
                                implicitHeight: 42
                                radius: 12
                                color: advancedToggleMouseArea.containsMouse ? "#0B0C10" : "#050505"
                                border.color: root.advancedPropertiesOpen ? "#12FFFFFF"
                                                                         : advancedToggleMouseArea.containsMouse ? "#0DFFFFFF"
                                                                                                                 : "transparent"
                                border.width: 1
                                Behavior on color { ColorAnimation { duration: 180 } }
                                Behavior on border.color { ColorAnimation { duration: 180 } }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    spacing: 8

                                    Text {
                                        text: "ADVANCED PROPERTIES"
                                        color: advancedToggleMouseArea.containsMouse ? "#D4D4D8" : "#71717A"
                                        font.pixelSize: 10
                                        font.weight: 800
                                        font.family: "Outfit"
                                        font.letterSpacing: 1.5
                                        Behavior on color { ColorAnimation { duration: 180 } }
                                    }
                                    Item { Layout.fillWidth: true }
                                    IconImage {
                                        iconName: "chevron-right"
                                        tone: advancedToggleMouseArea.containsMouse ? "white" : "muted"
                                        iconSize: 14
                                        rotation: root.advancedPropertiesOpen ? 90 : 0
                                        Behavior on rotation { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                                    }
                                }

                                MouseArea {
                                    id: advancedToggleMouseArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.advancedPropertiesOpen = !root.advancedPropertiesOpen
                                }
                            }

                            Item {
                                Layout.fillWidth: true
                                implicitHeight: root.advancedPropertiesOpen ? advancedPropertiesBody.implicitHeight : 0
                                opacity: root.advancedPropertiesOpen ? 1 : 0
                                clip: true
                                Behavior on implicitHeight { NumberAnimation { duration: 360; easing.type: Easing.OutCubic } }
                                Behavior on opacity { NumberAnimation { duration: 220 } }

                                Rectangle {
                                    id: advancedPropertiesBody
                                    width: parent.width
                                    implicitHeight: advancedPropertiesContent.implicitHeight + 24
                                    radius: 14
                                    color: "#800A0A0D"
                                    border.color: "#0DFFFFFF"
                                    border.width: 1

                                    ColumnLayout {
                                        id: advancedPropertiesContent
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: 16
                                        spacing: 16

                                        GridLayout {
                                            visible: propertySummaryCards.length > 0
                                            Layout.fillWidth: true
                                            columns: 2
                                            columnSpacing: 12
                                            rowSpacing: 12

                                            Repeater {
                                                model: propertySummaryCards
                                                delegate: Rectangle {
                                                    required property var modelData
                                                    Layout.fillWidth: true
                                                    Layout.columnSpan: propertySummaryCards.length === 1 ? 2 : 1
                                                    implicitHeight: 92
                                                    radius: 12
                                                    color: "#050505"
                                                    border.color: "#0DFFFFFF"
                                                    border.width: 1
                                                    clip: true

                                                    Rectangle {
                                                        id: cardGlowSource
                                                        width: 10
                                                        height: 10
                                                        radius: 5
                                                        anchors.top: parent.top
                                                        anchors.right: parent.right
                                                        anchors.topMargin: 4
                                                        anchors.rightMargin: 6
                                                        color: root.propertyToneColor(modelData.tone || "accent")
                                                        opacity: 1.0
                                                        visible: false
                                                    }

                                                    MultiEffect {
                                                        anchors.fill: parent
                                                        source: cardGlowSource
                                                        blurEnabled: true
                                                        blurMax: 14
                                                        blur: 1.0
                                                        opacity: 0.06
                                                    }

                                                    ColumnLayout {
                                                        anchors.fill: parent
                                                        anchors.margins: 14
                                                        spacing: 8

                                                        RowLayout {
                                                            Layout.fillWidth: true
                                                            Text {
                                                                text: modelData.label || ""
                                                                color: "#71717A"
                                                                font.pixelSize: 9
                                                                font.weight: 800
                                                                font.family: "Outfit"
                                                                font.letterSpacing: 1.2
                                                            }
                                                            Item { Layout.fillWidth: true }
                                                            IconImage {
                                                                iconName: modelData.icon || "activity"
                                                                tone: modelData.tone || "accent"
                                                                iconSize: 13
                                                            }
                                                        }

                                                        RowLayout {
                                                            Layout.fillWidth: true
                                                            spacing: 4
                                                            Text {
                                                                text: modelData.value || "--"
                                                                color: (modelData.tone || "accent") === "accent" ? "#FF5400"
                                                                                                              : (modelData.tone || "accent") === "cyan" ? "#FFFFFF"
                                                                                                              : root.propertyToneColor(modelData.tone || "accent")
                                                                font.pixelSize: 20
                                                                font.family: "Consolas"
                                                                font.weight: 500
                                                            }
                                                            Text {
                                                                visible: (modelData.unit || "").length > 0
                                                                text: modelData.unit || ""
                                                                color: "#71717A"
                                                                font.pixelSize: 9
                                                                font.weight: 700
                                                                font.family: "JetBrains Mono"
                                                                Layout.bottomMargin: 2
                                                            }
                                                            Item { Layout.fillWidth: true }
                                                        }

                                                            Rectangle {
                                                                visible: Number(modelData.progress) >= 0
                                                                Layout.fillWidth: true
                                                                Layout.preferredHeight: 4
                                                                radius: 2
                                                                color: "#14FFFFFF"
                                                                clip: true

                                                                Rectangle {
                                                                    width: Math.max(8, parent.width * Math.max(0, Math.min(1, Number(modelData.progress || 0))))
                                                                    height: parent.height
                                                                radius: 2
                                                                color: root.propertyToneColor(modelData.tone || "accent")
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        ColumnLayout {
                                            visible: (trainingLossPanel.points || []).length > 1
                                            Layout.fillWidth: true
                                            spacing: 10

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 1
                                                color: "#0DFFFFFF"
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 8

                                                Text {
                                                    text: "TRAINING LOSS"
                                                    color: "#71717A"
                                                    font.pixelSize: 10
                                                    font.weight: 800
                                                    font.family: "Outfit"
                                                    font.letterSpacing: 1.4
                                                }
                                                Item { Layout.fillWidth: true }
                                                RowLayout {
                                                    spacing: 4
                                                    rotation: 0

                                                    IconImage {
                                                        iconName: "arrow-up-right"
                                                        tone: "accent"
                                                        iconSize: 12
                                                        rotation: 180
                                                    }
                                                    Text {
                                                        text: (trainingLossPanel.minValue || "--") + " MIN"
                                                        color: "#FF5400"
                                                        font.pixelSize: 12
                                                        font.weight: 700
                                                        font.family: "JetBrains Mono"
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                id: lossChartCard
                                                Layout.fillWidth: true
                                                implicitHeight: 138
                                                radius: 12
                                                color: "#050505"
                                                border.color: "#0DFFFFFF"
                                                border.width: 1
                                                clip: true

                                                Repeater {
                                                    model: [
                                                        { anchorTop: true, label: trainingLossPanel.topLabel || "--" },
                                                        { anchorTop: false, verticalCenter: 0.5, label: trainingLossPanel.midLabel || "--" },
                                                        { anchorBottom: true, label: trainingLossPanel.bottomLabel || "--" }
                                                    ]

                                                    delegate: Item {
                                                        required property var modelData
                                                        anchors.left: parent.left
                                                        anchors.right: parent.right
                                                        anchors.top: modelData.anchorTop ? parent.top : undefined
                                                        anchors.bottom: modelData.anchorBottom ? parent.bottom : undefined
                                                        anchors.verticalCenter: modelData.verticalCenter !== undefined ? parent.verticalCenter : undefined
                                                        height: 1

                                                        Rectangle {
                                                            anchors.left: parent.left
                                                            anchors.right: parent.right
                                                            anchors.verticalCenter: parent.verticalCenter
                                                            height: 1
                                                            color: "#0DFFFFFF"
                                                            opacity: 0.2
                                                        }

                                                        Text {
                                                            anchors.left: parent.left
                                                            anchors.leftMargin: 10
                                                            anchors.verticalCenter: parent.verticalCenter
                                                            text: modelData.label || "--"
                                                            color: "#7FFFFFFF"
                                                            font.pixelSize: 8
                                                            font.family: "JetBrains Mono"
                                                        }
                                                    }
                                                }

                                                Canvas {
                                                    id: lossChartCanvas
                                                    anchors.fill: parent
                                                    anchors.margins: 1
                                                    antialiasing: true

                                                    onPaint: {
                                                        var ctx = getContext("2d")
                                                        ctx.reset()
                                                        var points = trainingLossPanel.points || []
                                                        if (points.length < 2)
                                                            return

                                                        var width = lossChartCanvas.width
                                                        var height = lossChartCanvas.height
                                                        function px(point) { return (point.xPct / 100.0) * width }
                                                        function py(point) { return (point.yPct / 100.0) * height }

                                                        ctx.beginPath()
                                                        ctx.moveTo(px(points[0]), py(points[0]))
                                                        for (var i = 1; i < points.length; ++i)
                                                            ctx.lineTo(px(points[i]), py(points[i]))
                                                        ctx.lineTo(width, height)
                                                        ctx.lineTo(0, height)
                                                        ctx.closePath()

                                                        var gradient = ctx.createLinearGradient(0, 0, 0, height)
                                                        gradient.addColorStop(0, "rgba(255, 84, 0, 0.30)")
                                                        gradient.addColorStop(1, "rgba(255, 84, 0, 0.00)")
                                                        ctx.fillStyle = gradient
                                                        ctx.fill()

                                                        ctx.beginPath()
                                                        ctx.moveTo(px(points[0]), py(points[0]))
                                                        for (var j = 1; j < points.length; ++j)
                                                            ctx.lineTo(px(points[j]), py(points[j]))
                                                        ctx.strokeStyle = "#FF5400"
                                                        ctx.lineWidth = 2
                                                        ctx.lineJoin = "round"
                                                        ctx.lineCap = "round"
                                                        ctx.stroke()
                                                    }

                                                    Connections {
                                                        target: root
                                                        function onLossHoverIndexChanged() { lossChartCanvas.requestPaint() }
                                                    }
                                                    Component.onCompleted: requestPaint()
                                                }

                                                MouseArea {
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.CrossCursor

                                                    onPositionChanged: function(mouse) {
                                                        var points = trainingLossPanel.points || []
                                                        if (!points.length) {
                                                            root.lossHoverIndex = -1
                                                            return
                                                        }
                                                        var xPct = (mouse.x / width) * 100.0
                                                        var closestIndex = 0
                                                        var bestDistance = 9999
                                                        for (var i = 0; i < points.length; ++i) {
                                                            var distance = Math.abs(points[i].xPct - xPct)
                                                            if (distance < bestDistance) {
                                                                bestDistance = distance
                                                                closestIndex = i
                                                            }
                                                        }
                                                        root.lossHoverIndex = closestIndex
                                                    }

                                                    onExited: root.lossHoverIndex = -1
                                                }

                                                Item {
                                                    id: lossHoverLayer
                                                    visible: root.hoveredLossPoint() !== null
                                                    anchors.fill: parent
                                                    property var point: root.hoveredLossPoint()

                                                    Rectangle {
                                                        visible: parent.point !== null
                                                        width: 1
                                                        anchors.top: parent.top
                                                        anchors.bottom: parent.bottom
                                                        x: ((parent.point ? parent.point.xPct : 0) / 100.0) * parent.width
                                                        color: "#80FF5400"
                                                    }

                                                    Rectangle {
                                                        visible: parent.point !== null
                                                        width: 8
                                                        height: 8
                                                        radius: 4
                                                        x: (((parent.point ? parent.point.xPct : 0) / 100.0) * parent.width) - width / 2
                                                        y: (((parent.point ? parent.point.yPct : 0) / 100.0) * parent.height) - height / 2
                                                        color: "#FFFFFF"
                                                        border.color: "#FF5400"
                                                        border.width: 2
                                                    }

                                                    Rectangle {
                                                        visible: parent.point !== null
                                                        radius: 8
                                                        color: "#E60A0A0D"
                                                        border.color: "#66FF5400"
                                                        border.width: 1
                                                        implicitWidth: lossTooltipContent.implicitWidth + 16
                                                        implicitHeight: lossTooltipContent.implicitHeight + 10
                                                        x: Math.max(8, Math.min(parent.width - width - 8, ((parent.point ? parent.point.xPct : 0) / 100.0) * parent.width + (((parent.point ? parent.point.xPct : 0) > 80) ? -width - 10 : 10)))
                                                        y: 8

                                                        ColumnLayout {
                                                            id: lossTooltipContent
                                                            anchors.centerIn: parent
                                                            spacing: 2

                                                            RowLayout {
                                                                spacing: 4
                                                                Text {
                                                                    text: "LOSS"
                                                                    color: "#71717A"
                                                                    font.pixelSize: 8
                                                                    font.weight: 700
                                                                    font.family: "Outfit"
                                                                    font.letterSpacing: 1.0
                                                                }
                                                                Text {
                                                                    text: lossHoverLayer.point ? lossHoverLayer.point.loss : "--"
                                                                    color: "#FF5400"
                                                                    font.pixelSize: 10
                                                                    font.weight: 700
                                                                    font.family: "JetBrains Mono"
                                                                }
                                                            }

                                                            RowLayout {
                                                                spacing: 4
                                                                Text {
                                                                    text: "TIME"
                                                                    color: "#71717A"
                                                                    font.pixelSize: 8
                                                                    font.weight: 700
                                                                    font.family: "Outfit"
                                                                    font.letterSpacing: 1.0
                                                                }
                                                                Text {
                                                                    text: lossHoverLayer.point ? lossHoverLayer.point.time : "--"
                                                                    color: "#D4D4D8"
                                                                    font.pixelSize: 10
                                                                    font.weight: 700
                                                                    font.family: "JetBrains Mono"
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        Repeater {
                                            model: propertySections
                                            delegate: ColumnLayout {
                                                required property var modelData
                                                Layout.fillWidth: true
                                                spacing: 8

                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 8
                                                    Text {
                                                        text: (modelData.title || "").toUpperCase()
                                                        color: "#71717A"
                                                        font.pixelSize: 10
                                                        font.weight: 800
                                                        font.family: "Outfit"
                                                        font.letterSpacing: 1.4
                                                    }
                                                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#0DFFFFFF" }
                                                }

                                                Repeater {
                                                    model: modelData.rows || []
                                                    delegate: Rectangle {
                                                        required property var modelData
                                                        property bool hovered: false
                                                        Layout.fillWidth: true
                                                        implicitHeight: rowContent.implicitHeight + 18
                                                        radius: 12
                                                        color: hovered ? "#14171D" : "#111116"
                                                        border.color: hovered ? "#1AFFFFFF" : "#12FFFFFF"
                                                        border.width: 1
                                                        Behavior on color { ColorAnimation { duration: 180 } }
                                                        Behavior on border.color { ColorAnimation { duration: 180 } }

                                                        ColumnLayout {
                                                            id: rowContent
                                                            anchors.fill: parent
                                                            anchors.leftMargin: 12
                                                            anchors.rightMargin: 12
                                                            anchors.topMargin: 10
                                                            anchors.bottomMargin: 8
                                                            spacing: 6

                                                            RowLayout {
                                                                Layout.fillWidth: true
                                                                spacing: 8

                                                                IconImage {
                                                                    iconName: modelData.icon || "box"
                                                                    tone: modelData.tone || "muted"
                                                                    iconSize: 15
                                                                }
                                                                Text {
                                                                    text: modelData.label || ""
                                                                    color: "#A1A1AA"
                                                                    font.pixelSize: 11
                                                                    font.weight: 700
                                                                    font.family: "Outfit"
                                                                }
                                                                Item { Layout.fillWidth: true }
                                                                Rectangle {
                                                                    visible: !!modelData.copyable
                                                                    property bool hovered: copyRowMouseArea.containsMouse
                                                                    radius: 8
                                                                    color: hovered ? "#10151A" : "#0A0C10"
                                                                    border.color: hovered ? root.propertyToneBorder(modelData.tone || "accent") : "#12FFFFFF"
                                                                    border.width: 1
                                                                    implicitWidth: copyRow.implicitWidth + 14
                                                                    implicitHeight: 24
                                                                    Behavior on color { ColorAnimation { duration: 180 } }
                                                                    Behavior on border.color { ColorAnimation { duration: 180 } }

                                                                    RowLayout {
                                                                        id: copyRow
                                                                        anchors.centerIn: parent
                                                                        spacing: 5
                                                                        Text {
                                                                            text: "Copy"
                                                                            color: "#E4E4E7"
                                                                            font.pixelSize: 10
                                                                            font.weight: 700
                                                                            font.family: "Outfit"
                                                                        }
                                                                        IconImage {
                                                                            iconName: "copy"
                                                                            tone: parent.parent.hovered ? (modelData.tone || "accent") : "muted"
                                                                            iconSize: 11
                                                                        }
                                                                    }

                                                                    MouseArea {
                                                                        id: copyRowMouseArea
                                                                        anchors.fill: parent
                                                                        hoverEnabled: true
                                                                        cursorShape: Qt.PointingHandCursor
                                                                        onClicked: controller.copyText(modelData.value || "")
                                                                    }
                                                                }
                                                            }

                                                            Text {
                                                                Layout.fillWidth: true
                                                                text: modelData.value || ""
                                                                color: "#F4F4F5"
                                                                font.pixelSize: 12
                                                                font.family: (modelData.copyable || modelData.mono) ? "Consolas" : "DM Sans 36pt"
                                                                wrapMode: modelData.copyable ? Text.NoWrap : Text.WordWrap
                                                                elide: modelData.copyable ? Text.ElideMiddle : Text.ElideNone
                                                                maximumLineCount: modelData.copyable ? 1 : 8
                                                            }
                                                        }

                                                        MouseArea {
                                                            anchors.fill: parent
                                                            acceptedButtons: Qt.NoButton
                                                            hoverEnabled: true
                                                            cursorShape: Qt.PointingHandCursor
                                                            onEntered: parent.hovered = true
                                                            onExited: parent.hovered = false
                                                        }
                                                    }
                                                }

                                                Rectangle {
                                                    visible: index < propertySections.length - 1
                                                    Layout.fillWidth: true
                                                    Layout.preferredHeight: 1
                                                    color: "#0DFFFFFF"
                                                    Layout.topMargin: 2
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            ScrollView {
                id: consoleScroll
                clip: true
                contentWidth: availableWidth
                ScrollBar.vertical.policy: ScrollBar.AlwaysOff
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
                            visible: !!liveMonitor.showStopPrompt
                            radius: 8
                            color: "#1AFB7185"
                            border.color: "#4DFB7185"
                            border.width: 1
                            implicitWidth: stopText.implicitWidth + 16
                            implicitHeight: 28

                            Text {
                                id: stopText
                                anchors.centerIn: parent
                                text: "Stop?"
                                color: "#FDA4AF"
                                font.pixelSize: 11
                                font.weight: 800
                                font.family: "Outfit"
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: controller.stopTraining()
                            }
                        }
                        Rectangle {
                            radius: 8
                            color: livePanelColor(liveState)
                            border.color: liveBorderColor(liveState)
                            border.width: 1
                            implicitWidth: liveRow.implicitWidth + 16
                            implicitHeight: 28
                            RowLayout {
                                id: liveRow
                                anchors.centerIn: parent
                                spacing: 6
                                Rectangle { width: 6; height: 6; radius: 3; color: liveDotColor(liveState) }
                                Text { text: liveLabel; color: liveTextColor(liveState); font.pixelSize: 11; font.weight: 700; font.family: "Outfit" }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.max(400, consoleScroll.height - 100)
                        clip: true
                        radius: 14
                        color: "#020202"
                        border.color: "#14FFFFFF"
                        border.width: 1

                        Flickable {
                            id: consoleFlick
                            anchors.fill: parent
                            anchors.margins: 16
                            contentWidth: width
                            contentHeight: consoleColumn.implicitHeight
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            flickableDirection: Flickable.VerticalFlick

                            onContentHeightChanged: {
                                if (contentHeight > height)
                                    contentY = contentHeight - height
                            }

                            ScrollBar.vertical: ScrollBar {
                                policy: ScrollBar.AlwaysOff
                            }

                            Column {
                                id: consoleColumn
                                width: parent.width
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
                                        Behavior on color { ColorAnimation { duration: 200 } }

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
                                                lineHeight: 2.5
                                                lineHeightMode: Text.ProportionalHeight
                                                Layout.alignment: Qt.AlignTop
                                            }
                                        }

                                        MouseArea {
                                            anchors.fill: parent
                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
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
                                        visible: consoleRunning
                                        text: "..."
                                        color: "#A1A1AA"
                                        font.family: "Consolas"
                                        font.pixelSize: 11
                                    }
                                    Rectangle {
                                        visible: !consoleRunning
                                        width: 6
                                        height: 12
                                        color: "#A1A1AA"
                                        anchors.verticalCenter: parent.verticalCenter

                                        SequentialAnimation on opacity {
                                            loops: Animation.Infinite
                                            NumberAnimation { to: 1; duration: 500 }
                                            NumberAnimation { to: 0; duration: 500 }
                                        }
                                    }
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
                ScrollBar.vertical.policy: ScrollBar.AlwaysOff
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
                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
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
                                    Behavior on border.color { ColorAnimation { duration: 200 } }
                                    Image {
                                        anchors.fill: parent
                                        fillMode: Image.PreserveAspectCrop
                                        source: modelData.url || ""
                                        asynchronous: true
                                        cache: false
                                        opacity: hovered ? 1.0 : 0.6
                                        Behavior on opacity { NumberAnimation { duration: 200 } }
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
                                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hovered = true
                                        onExited: parent.hovered = false
                                    }
                                }
                            }

                            Rectangle {
                                width: mediaFlow.tileSize
                                height: mediaFlow.tileSize
                                visible: (videoTile.url || "").length > 0
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
                                    Text { text: videoTile.fps || ""; color: "#FFFFFF"; font.pixelSize: 9; font.weight: 700; font.family: "Outfit" }
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
                                        text: videoTile.name || ""
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
                        Behavior on color { ColorAnimation { duration: 200 } }
                        Behavior on border.color { ColorAnimation { duration: 200 } }

                        RowLayout {
                            anchors.centerIn: parent
                            spacing: 8
                            IconImage { iconName: "image"; tone: parent.parent.hovered ? "white" : "muted"; iconSize: 16 }
                            Text {
                                text: "Add Photos or Video"
                                color: parent.parent.hovered ? "#FFFFFF" : "#A1A1AA"
                                Behavior on color { ColorAnimation { duration: 200 } }
                                font.pixelSize: 13
                                font.weight: 700
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
                }
            }
        }
    }
}
