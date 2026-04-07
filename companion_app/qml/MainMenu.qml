import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#050505"
    border.color: "#14FFFFFF"
    border.width: 1

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 12
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
                }

                HoverHandler { onHoveredChanged: parent.hovered = hovered }
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
            }
            HoverHandler { onHoveredChanged: parent.hovered = hovered }
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

            HoverHandler { }
        }
    }
}
