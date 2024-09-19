def classFactory(iface):
    from .layer_reveal import LayerRevealPlugin
    return LayerRevealPlugin(iface)
