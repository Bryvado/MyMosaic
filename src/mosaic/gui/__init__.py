"""Dear PyGui based GUI for Mosaic."""

__all__ = ["MosaicApp"]


def __getattr__(name):
    if name == "MosaicApp":
        from mosaic.gui.app import MosaicApp
        return MosaicApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
