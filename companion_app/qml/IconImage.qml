import QtQuick 2.15
import QtQuick.Layouts 1.15
import Qt5Compat.GraphicalEffects

Item {
    id: root
    property string iconName: ""
    property string tone: "white"
    property int iconSize: 18

    width: iconSize
    height: iconSize
    Layout.preferredWidth: iconSize
    Layout.preferredHeight: iconSize

    readonly property color toneColor: {
        switch (tone) {
            case "accent": return "#FF5400"
            case "cyan":   return "#00F0FF"
            case "green":  return "#16C784"
            case "rose":   return "#FF2E93"
            case "muted":  return "#71717A"
            case "dim":    return "#27272A"
            default:       return "#FFFFFF"
        }
    }

    Image {
        id: svgSource
        anchors.fill: parent
        sourceSize.width: root.iconSize
        sourceSize.height: root.iconSize
        fillMode: Image.PreserveAspectFit
        smooth: true
        asynchronous: true
        visible: false
        source: root.iconName.length > 0 ? "../assets/icons/" + root.iconName + ".svg" : ""
    }

    ColorOverlay {
        anchors.fill: svgSource
        source: svgSource
        color: root.toneColor
        Behavior on color { ColorAnimation { duration: 200 } }
    }
}
