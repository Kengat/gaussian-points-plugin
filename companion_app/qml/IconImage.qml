import QtQuick 2.15
import QtQuick.Layouts 1.15

Image {
    property string iconName: ""
    property string tone: "white"
    property int iconSize: 18

    width: iconSize
    height: iconSize
    Layout.preferredWidth: iconSize
    Layout.preferredHeight: iconSize
    sourceSize.width: iconSize
    sourceSize.height: iconSize
    fillMode: Image.PreserveAspectFit
    smooth: true
    asynchronous: true
    source: iconName.length > 0 ? "../assets/icons_png/" + iconName + "-" + tone + ".png" : ""
}
