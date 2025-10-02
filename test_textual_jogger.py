#!/usr/bin/env python3
"""
Test script for the Textual jogger module.
"""

import sys
from jogger_textual import JoggerTextualApp


def test_textual_jogger():
    """Test the Textual jogger functionality."""
    print("🧪 Testing Textual Jogger")
    print("=" * 50)
    
    # Create app instance (without running it)
    app = JoggerTextualApp()
    
    # Test basic movements
    print("\n📍 Testing basic movements:")
    app.move_axis('x', 1.0)
    app.move_axis('y', -2.5)
    app.move_axis('z', 0.5)
    app.move_axis('e', 10.0)
    
    print(f"\nCurrent position: {app.position}")
    
    # Test fine movements
    print("\n🔬 Testing fine movements:")
    app.step_size = 0.1
    app.is_fine_mode = True
    app.move_axis('x', 0.1)
    app.move_axis('y', -0.1)
    
    print(f"Position after fine movements: {app.position}")
    
    # Test special functions
    print("\n🔧 Testing special functions:")
    app.home_xy()
    app.save_z_zero()
    app.reset_e_zero()
    
    print(f"Position after special functions: {app.position}")
    
    # Show G-code history
    print("\n📝 Generated G-code commands:")
    for i, gcode in enumerate(app.gcode_history, 1):
        print(f"{i:2d}. {gcode}")
    
    print("\n✅ Textual jogger test completed!")
    print("\n🎮 To run the interactive Textual jogger:")
    print("python jogger_textual.py")
    print("\n🎮 Controls:")
    print("   • Arrow keys: Move X/Y")
    print("   • U/J keys: Move Z up/down")
    print("   • E/D keys: Extrude/Retract")
    print("   • Space: Toggle fine/coarse step size")
    print("   • H: Home XY")
    print("   • Z: Save Z to zero")
    print("   • R: Reset E to zero")
    print("   • Q: Quit")
    print("   • Mouse: Click buttons for movement")


if __name__ == "__main__":
    test_textual_jogger()
