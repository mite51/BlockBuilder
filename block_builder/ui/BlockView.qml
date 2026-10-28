import QtQuick
import QtQuick3D
import QtQuick3D.AssetUtils

Rectangle {
    id: root
    color: "#171e28"
    property url modelSource: ""
    property bool unitMode: false
    property bool showBounds: false
    property bool tiled: false
    property bool spinning: false
    property bool orthographic: false
    property real yaw: -35
    property real pitch: 22
    property real distance: 3.0
    property string loadError: ""
    property real fitScale: 1
    property vector3d modelCenter: Qt.vector3d(0,0,0)
    property bool wireframe: false
    property bool highQuality: false
    function cameraPreset(name) {
        spinning = false;
        orthographic = name !== "Orbit";
        if (name === "Front") { yaw = 0; pitch = 0; }
        else if (name === "Side") { yaw = -90; pitch = 0; }
        else if (name === "Top") { yaw = 0; pitch = 90; }
        else { yaw = -35; pitch = 22; }
        distance = tiled ? 6 : 3.0;
    }
    View3D {
        id: view
        anchors.fill: parent
        camera: root.orthographic ? ortho : perspective
        environment: SceneEnvironment {
            backgroundMode: SceneEnvironment.Color
            clearColor: root.color
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: root.highQuality ? SceneEnvironment.High : SceneEnvironment.Medium
            debugSettings: DebugSettings { wireframeEnabled: root.wireframe }
        }
        PerspectiveCamera { id: perspective; z: root.distance; fieldOfView: 35; clipNear: 0.01; clipFar: 100 }
        OrthographicCamera {
            id: ortho; z: root.distance; clipNear: 0.01; clipFar: 100
            horizontalMagnification: Math.min(view.width,view.height)/root.distance
            verticalMagnification: horizontalMagnification
        }
        DirectionalLight { eulerRotation: Qt.vector3d(-35,-35,0); brightness: 1.3; ambientColor: "#666666" }
        DirectionalLight { eulerRotation: Qt.vector3d(25,140,0); brightness: 0.7 }
        Node {
            eulerRotation: Qt.vector3d(root.pitch,root.yaw,0)
            Node {
                id: fitted
                scale: root.unitMode ? Qt.vector3d(1,1,1) : Qt.vector3d(root.fitScale,root.fitScale,root.fitScale)
                position: root.unitMode ? Qt.vector3d(0,0,0) : Qt.vector3d(-root.modelCenter.x*root.fitScale,-root.modelCenter.y*root.fitScale,-root.modelCenter.z*root.fitScale)
                RuntimeLoader {
                    id: loader
                    source: root.modelSource
                    onStatusChanged: {
                        root.loadError = status === RuntimeLoader.Error ? errorString : "";
                    }
                    onBoundsChanged: {
                            let lo = bounds.minimum;
                            let hi = bounds.maximum;
                            root.modelCenter = Qt.vector3d((lo.x+hi.x)/2,(lo.y+hi.y)/2,(lo.z+hi.z)/2);
                            root.fitScale = 1/Math.max(hi.x-lo.x,hi.y-lo.y,hi.z-lo.z,0.00001);
                    }
                }
            }
            Repeater3D {
                model: root.tiled && root.unitMode ? 8 : 0
                delegate: Node {
                    required property int index
                    property var offsets: [[-1,0,-1],[0,0,-1],[1,0,-1],[-1,0,0],[1,0,0],[-1,0,1],[0,0,1],[1,0,1]]
                    position: Qt.vector3d(offsets[index][0],0,offsets[index][2])
                    RuntimeLoader { source: root.modelSource }
                }
            }
        }
    }
    // Render the placement guide in its own transparent pass so protruding
    // geometry cannot hide the logical cell edges.
    View3D {
        anchors.fill: parent
        visible: root.showBounds
        camera: root.orthographic ? guideOrtho : guidePerspective
        environment: SceneEnvironment {
            backgroundMode: SceneEnvironment.Transparent
            antialiasingMode: SceneEnvironment.MSAA
        }
        PerspectiveCamera { id: guidePerspective; z: root.distance; fieldOfView: 35; clipNear: 0.01; clipFar: 100 }
        OrthographicCamera {
            id: guideOrtho; z: root.distance; clipNear: 0.01; clipFar: 100
            horizontalMagnification: Math.min(view.width,view.height)/root.distance
            verticalMagnification: horizontalMagnification
        }
        Node {
            eulerRotation: Qt.vector3d(root.pitch,root.yaw,0)
            Repeater3D {
                model: root.showBounds ? 12 : 0
                delegate: Model {
                    required property int index
                    property int axis: Math.floor(index/4)
                    property int corner: index%4
                    source: "#Cube"
                    position: axis===0 ? Qt.vector3d(0,corner<2?-.5:.5,corner%2?-.5:.5)
                              : axis===1 ? Qt.vector3d(corner<2?-.5:.5,0,corner%2?-.5:.5)
                              : Qt.vector3d(corner<2?-.5:.5,corner%2?-.5:.5,0)
                    scale: axis===0 ? Qt.vector3d(.01004,.000035,.000035)
                           : axis===1 ? Qt.vector3d(.000035,.01004,.000035)
                           : Qt.vector3d(.000035,.000035,.01004)
                    materials: PrincipledMaterial { baseColor: "#59ead0"; lighting: PrincipledMaterial.NoLighting }
                }
            }
        }
    }
    MouseArea {
        anchors.fill: parent
        property real lastX
        property real lastY
        onPressed: mouse => { lastX=mouse.x; lastY=mouse.y; root.spinning=false; }
        onPositionChanged: mouse => {
            if (pressed) {
                root.yaw += (mouse.x-lastX)*0.5;
                root.pitch = Math.max(-89,Math.min(89,root.pitch+(mouse.y-lastY)*0.5));
                lastX=mouse.x; lastY=mouse.y;
            }
        }
        onWheel: wheel => { root.distance=Math.max(1.2,Math.min(15,root.distance-wheel.angleDelta.y/120*0.3)); }
        onDoubleClicked: root.cameraPreset("Orbit")
    }
    Timer { interval: 30; repeat: true; running: root.spinning && root.visible; onTriggered: root.yaw += 0.35 }
    Text {
        anchors.centerIn: parent; width: parent.width-30
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        color: "#b5c5d8"; font.pixelSize: 13
        text: root.loadError || (root.modelSource.toString()==="" ? "3D preview" : "")
    }
    Text {
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 10
        color: "#9cacc0"; font.pixelSize: 10
        text: root.modelSource.toString() ? "Drag to orbit · Scroll to zoom" : ""
    }
}
