#!/usr/bin/env python3
"""
Demo script for the Textual jogger module.
Shows how to use the Textual jogger programmatically and demonstrates its features.
"""

import time
from jogger_textual import JoggerTextualApp


def demo_textual_jogger_features():
    """Demonstrate the Textual jogger features programmatically."""
    print("🎮 Textual Jogger Module Demo")
    print("=" * 60)
    
    # Create jogger instance
    jogger = JoggerTextualApp()
    
    print("📍 Starting position:", jogger.position)
    print()
    
    # Demo 1: Basic X/Y movement
    print("🎯 Demo 1: Basic X/Y Movement")
    print("-" * 30)
    jogger.move_axis('x', 5.0)
    jogger.move_axis('y', 3.0)
    print(f"Position after X+5, Y+3: {jogger.position}")
    print()
    
    # Demo 2: Z axis movement
    print("🎯 Demo 2: Z Axis Movement")
    print("-" * 30)
    jogger.move_axis('z', 2.0)
    print(f"Position after Z+2: {jogger.position}")
    print()
    
    # Demo 3: E axis (extruder) movement
    print("🎯 Demo 3: E Axis (Extruder) Movement")
    print("-" * 30)
    jogger.move_axis('e', 10.0)
    print(f"Position after E+10: {jogger.position}")
    print()
    
    # Demo 4: Fine movements
    print("🎯 Demo 4: Fine Movements (0.1mm steps)")
    print("-" * 30)
    jogger.step_size = 0.1
    jogger.is_fine_mode = True
    jogger.move_axis('x', 0.5)
    jogger.move_axis('y', -0.3)
    jogger.move_axis('z', 0.1)
    print(f"Position after fine movements: {jogger.position}")
    print()
    
    # Demo 5: Special functions
    print("🎯 Demo 5: Special Functions")
    print("-" * 30)
    print("Before special functions:", jogger.position)
    jogger.home_xy()
    print("After home XY:", jogger.position)
    jogger.save_z_zero()
    print("After save Z to zero:", jogger.position)
    jogger.reset_e_zero()
    print("After reset E to zero:", jogger.position)
    print()
    
    # Demo 6: Different feed rates
    print("🎯 Demo 6: Different Feed Rates")
    print("-" * 30)
    jogger.feed_rate = 500.0
    jogger.move_axis('x', 1.0)
    print(f"Slow movement (500mm/min): {jogger.gcode_history[-1]}")
    
    jogger.feed_rate = 2000.0
    jogger.move_axis('y', 1.0)
    print(f"Fast movement (2000mm/min): {jogger.gcode_history[-1]}")
    print()
    
    # Show all generated G-code
    print("📝 All Generated G-code Commands:")
    print("-" * 30)
    for i, gcode in enumerate(jogger.gcode_history, 1):
        print(f"{i:2d}. {gcode}")
    
    print()
    print("✅ Demo completed!")
    print()
    print("🚀 To run the interactive Textual jogger with full UI:")
    print("   python jogger_textual.py")
    print()
    print("🎮 Controls in interactive mode:")
    print("   • Arrow keys: Move X/Y")
    print("   • U/J keys: Move Z up/down")
    print("   • E/D keys: Extrude/Retract")
    print("   • Space: Toggle fine/coarse step size")
    print("   • H: Home XY")
    print("   • Z: Save Z to zero")
    print("   • R: Reset E to zero")
    print("   • Q: Quit")
    print("   • Mouse: Click buttons for movement")
    print()
    print("✨ Features of Textual jogger:")
    print("   • Beautiful modern UI with buttons and panels")
    print("   • Real-time position display")
    print("   • G-code command history")
    print("   • Mouse and keyboard support")
    print("   • No external permissions required")
    print("   • Cross-platform compatibility")


if __name__ == "__main__":
    demo_textual_jogger_features()
