import os
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter
from PyQt5 import uic
from PyQt5.QtWidgets import QDialog, QAction, QVBoxLayout, QListWidget, QListWidgetItem, QPushButton, QFileDialog
from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsVectorLayer, QgsMessageLog, Qgis, QgsWkbTypes, QgsLayerTreeGroup, QgsSymbolLayer, QgsApplication, QgsGeometryGeneratorSymbolLayer
from qgis.gui import QgsMapTool, QgsRubberBand

class LayerRevealPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.tool = None
        self.dialog = None

    def initGui(self):
        self.action = QAction("Layer Reveal", self.iface.mainWindow())
        self.iface.addPluginToMenu("&Layer Reveal", self.action)
        self.action.triggered.connect(self.show_dialog)

    def unload(self):
        self.iface.removePluginMenu("&Layer Reveal", self.action)

    def show_dialog(self):
        if not self.dialog:
            self.dialog = LayerRevealDialog(self)
        self.dialog.show()

class LayerSelectionDialog(QDialog):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.selected_layers = []

        self.setWindowTitle("Select Layers")
        self.setGeometry(100, 100, 300, 400)

        # Crear el layout
        layout = QVBoxLayout()
        self.layer_list_widget = QListWidget()

        # Agregar las capas como checkboxes
        for layer in QgsProject.instance().mapLayers().values():
            item = QListWidgetItem(layer.name())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)  # Habilitar checkboxes
            item.setCheckState(Qt.Unchecked)  # Inicialmente desmarcado
            self.layer_list_widget.addItem(item)

        # Botón de aceptar
        accept_button = QPushButton("Accept")
        accept_button.clicked.connect(self.accept_selection)

        # Añadir widgets al layout
        layout.addWidget(self.layer_list_widget)
        layout.addWidget(accept_button)

        self.setLayout(layout)

    def accept_selection(self):
        # Obtener las capas seleccionadas
        self.selected_layers = [item.text() for item in self.layer_list_widget.findItems("", Qt.MatchContains) if
                                item.checkState() == Qt.Checked]
        self.accept()

    def get_selected_layers(self):
        return self.selected_layers

class LayerRevealDialog(QDialog):
    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin
        ui_path = os.path.join(os.path.dirname(__file__), 'UILayer.ui')
        uic.loadUi(ui_path, self)

        # Llenar el combobox con las capas del proyecto para el BOTTOM LAYER
        self.load_layers_into_combobox()

        # Conectar el ComboBox al método de selección
        self.comboBoxSaveOption.currentIndexChanged.connect(self.check_save_option)

        # Conectar el botón "Select Layer" a la función que abre el diálogo de selección de capas superiores
        self.pushButton_2.clicked.connect(self.open_layer_selection_dialog)

        # Conectar el botón "Apply Effect" a la función que aplica el efecto
        self.pushButton.clicked.connect(self.apply_effect)

        self.save_location = None  # Variable para almacenar la ubicación de guardado
        self.selected_layers = []  # Capas superiores seleccionadas

    def open_layer_selection_dialog(self):
        dialog = LayerSelectionDialog(self.plugin.iface)
        if dialog.exec_():
            self.selected_layers = dialog.get_selected_layers()

    def check_save_option(self):
        # Verificar si se selecciona "Guardar como archivo"
        if self.comboBoxSaveOption.currentText() == "Save as file":
            self.maskSavePath.setEnabled(True)  # Habilitar el campo para la ruta
        else:
            self.maskSavePath.setEnabled(False)  # Deshabilitar si no es necesario
            self.maskSavePath.clear()  # Limpiar si el usuario cambia la opción

    def apply_effect(self):
        # Obtener el nombre del grupo ingresado por el usuario
        group_name = self.lineEdit.text().strip()
        if not group_name:
            group_name = "LayerReveal"  # Nombre por defecto

        # Obtener la capa inferior seleccionada
        bottom_layer_name = self.comboBox.currentText()

        # Verificar si se seleccionaron capas superiores e inferiores
        if not bottom_layer_name or not self.selected_layers:
            self.plugin.iface.messageBar().pushMessage("Error", "Select at least one bottom and top layer", level=Qgis.Warning)
            return

        # Crear la máscara y aplicar efectos
        self.create_mask(self.selected_layers, self.mQgsDoubleSpinBox.value(),
                        self.mQgsProjectionSelectionWidget.crs().authid(), temporary=True)

        # Configurar la simbología de la capa de máscara
        self.configure_mask_symbology()

        # Agrupar capas y aplicar modos de mezcla
        self.group_layers_and_apply_blending(group_name, bottom_layer_name, self.selected_layers)

        # Activar la herramienta para seguir el cursor y actualizar la máscara
        self.plugin.tool = RevealMapTool(self.plugin.canvas, self.mask_layer, self.mQgsDoubleSpinBox.value())
        self.plugin.canvas.setMapTool(self.plugin.tool)

    def create_mask(self, layers, size, crs, temporary):
        # Verificar si ya existe una capa de máscara para evitar duplicados
        existing_mask_layers = QgsProject.instance().mapLayersByName("Mask Layer")
        for mask_layer in existing_mask_layers:
            QgsProject.instance().removeMapLayer(mask_layer)

        if temporary:
            mask_layer = QgsVectorLayer(f"Polygon?crs={crs}", "Mask Layer", "memory")
        else:
            # Crear la capa en el archivo seleccionado por el usuario (GeoPackage o Shapefile)
            mask_layer = QgsVectorLayer(f"Polygon?crs={crs}", "Mask Layer", "ogr")
            mask_layer.dataProvider().setDataSourceUri(self.save_location)

        pr = mask_layer.dataProvider()

        # Crear la geometría de buffer alrededor del centro del lienzo o el cursor
        center_point = self.plugin.canvas.extent().center()
        geom = QgsGeometry.fromPointXY(center_point).buffer(size, 10)
        feature = QgsFeature()
        feature.setGeometry(geom)
        pr.addFeature(feature)

        # Añadir la capa de máscara al proyecto
        QgsProject.instance().addMapLayer(mask_layer)
        self.mask_layer = mask_layer


    def load_layers_into_combobox(self):
        # Obtener todas las capas del proyecto
        layers = QgsProject.instance().mapLayers().values()

        # Limpiar cualquier elemento que pueda estar en el comboBox (por si acaso)
        self.comboBox.clear()

        # Llenar el comboBox con los nombres de las capas
        for layer in layers:
            self.comboBox.addItem(layer.name())

    def configure_mask_symbology(self):
        # Obtener el símbolo de la capa de máscara
        symbol = self.mask_layer.renderer().symbol()
        
        # Crear un generador de geometría que use polígonos invertidos
        inverted_polygon_symbol = QgsGeometryGeneratorSymbolLayer.create({
            'geometry': 'buffer(@canvas_cursor_point, {})'.format(self.mQgsDoubleSpinBox.value()),
            'geometry_type': 2,  # Polygon type
            'inverted': '1'      # Inverted Polygons
        })
        
        # Cambiar el símbolo actual a un generador de geometría invertido
        symbol.changeSymbolLayer(0, inverted_polygon_symbol)
        
        # Aplicar y actualizar la capa
        self.mask_layer.triggerRepaint()


    def group_layers_and_apply_blending(self, group_name, bottom_layer_name, top_layers):
        # Obtener la raíz del árbol de capas
        root = QgsProject.instance().layerTreeRoot()
        
        # Eliminar un grupo con el mismo nombre si ya existe
        existing_groups = [g for g in root.findGroups() if g.name() == group_name]
        if existing_groups:
            root.removeChildNode(existing_groups[0])

        # Crear el grupo con el nombre proporcionado por el usuario
        group = root.addGroup(group_name)

        # Agregar la capa BOTTOM al grupo primero (se revelará dentro del círculo)
        bottom_layer = QgsProject.instance().mapLayersByName(bottom_layer_name)[0]
        group.addLayer(bottom_layer)

        # Agregar las capas TOP seleccionadas al grupo
        for layer_name in top_layers:
            top_layer = QgsProject.instance().mapLayersByName(layer_name)[0]
            group.addLayer(top_layer)

        # Agregar la capa de máscara al grupo
        group.addLayer(self.mask_layer)

        # Aplicar el modo de mezcla "Inverse Mask Below" a la capa de máscara
        self.mask_layer.setBlendMode(QPainter.CompositionMode_DestinationIn)

        # Asegurar que el grupo de capas se renderice como un grupo
        group_node = root.findGroup(group_name)
        group_node.setCustomProperty("blend_mode", QPainter.CompositionMode_SourceOver)  


    def configure_canvas_refresh(self):
        # Configurar la capa para que se actualice automáticamente a intervalos regulares
        if hasattr(self, 'timer'):
            self.timer.stop()  # Detener el temporizador si ya existe uno en ejecución

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_canvas)
        self.timer.start(int(self.mQgsDoubleSpinBox_2.value() * 1000))  

    def refresh_canvas(self):
        self.plugin.canvas.refresh()

class RevealMapTool(QgsMapTool):
    def __init__(self, canvas, layer, size):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.size = size
        self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)

    def canvasMoveEvent(self, event):
        # Capturar la posición del cursor en el lienzo
        point = self.toMapCoordinates(event.pos())
        geom = QgsGeometry.fromPointXY(point).buffer(self.size, 10)
        self.rubberBand.setToGeometry(geom, self.layer)
        
        # Actualizar la capa de máscara con la nueva geometría
        self.update_mask_layer(point)

    def update_mask_layer(self, point):
        # Crear un nuevo buffer basado en la posición del cursor
        geom = QgsGeometry.fromPointXY(point).buffer(self.size, 10)
        feature = QgsFeature()
        feature.setGeometry(geom)
        
        # Limpiar la capa de máscara existente y agregar el nuevo círculo
        self.layer.dataProvider().truncate()  # Eliminar geometrías existentes
        self.layer.dataProvider().addFeature(feature)
        self.layer.triggerRepaint()
