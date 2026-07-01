"""
Bambu Cuts - Cutter and Plotter for Bambu Lab Printers

A package for controlling Bambu Lab 3D printers as CNC cutters and plotters.
Includes tools for SVG/DXF to G-code conversion and a web-based control interface.
"""

__version__ = "0.1.0"

from .config import load_config, save_config, update_config, get_config

__all__ = [
    'load_config',
    'save_config',
    'update_config',
    'get_config',
    'GCodeTools',
    'CuttingParameters',
    'convert_dxf_to_svg',
    'process_3mf',
]


def __getattr__(name):
    """Lazy-load heavier helpers so unrelated commands can start quickly."""
    if name in {'GCodeTools', 'CuttingParameters'}:
        from .gcodetools import GCodeTools, CuttingParameters

        globals()['GCodeTools'] = GCodeTools
        globals()['CuttingParameters'] = CuttingParameters
        return globals()[name]

    if name == 'convert_dxf_to_svg':
        from .dxf2svg import convert_dxf_to_svg

        globals()['convert_dxf_to_svg'] = convert_dxf_to_svg
        return convert_dxf_to_svg

    if name == 'process_3mf':
        from .compress_3mf import process_3mf

        globals()['process_3mf'] = process_3mf
        return process_3mf

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
