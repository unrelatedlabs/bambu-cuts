"""
Bambu Cuts - Cutter and Plotter for Bambu Lab Printers

A package for controlling Bambu Lab 3D printers as CNC cutters and plotters.
Includes tools for SVG/DXF to G-code conversion and a web-based control interface.
"""

__version__ = "0.1.0"

from .config import load_config, save_config, update_config, get_config
from .gcodetools import GCodeTools, CuttingParameters
from .dxf2svg import convert_dxf_to_svg
from .compress_3mf import process_3mf

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
