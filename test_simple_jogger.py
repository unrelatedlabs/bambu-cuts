#!/usr/bin/env python3
"""
Test script for the simple jogger module (curses only).
"""

import sys
from jogger_simple import SimpleJoggerController


def test_simple_jogger():
    """Test the simple jogger functionality."""
    print("🧪 Testing Simple Jogger (Curses Only)")
    print("=" * 50)
    
    jogger = SimpleJoggerController()
    
    # Test basic movements
    print("\n📍 Testing basic movements:")
    jogger.move_axis('x', 1.0)
    jogger.move_axis('y', -2.5)
    jogger.move_axis('z', 0.5)
    jogger.move_axis('e', 10.0)
    
    print(f"\nCurrent position: {jogger.position}")
    
    # Test fine movements
    print("\n🔬 Testing fine movements:")
    jogger.step_size = 0.1
    jogger.move_axis('x', 0.1)
    jogger.move_axis('y', -0.1)
    
    print(f"Position after fine movements: {jogger.position}")
    
    # Test special functions
    print("\n🔧 Testing special functions:")
    jogger.home_xy()
    jogger.save_z_zero()
    jogger.reset_e_zero()
    
    print(f"Position after special functions: {jogger.position}")
    
    # Show G-code history
    print("\n📝 Generated G-code commands:")
    for i, gcode in enumerate(jogger.gcode_history, 1):
        print(f"{i:2d}. {gcode}")
    
    print("\n✅ Simple jogger test completed!")
    print("\n🎮 To run the interactive simple jogger:")
    print("python jogger_simple.py")


if __name__ == "__main__":
    test_simple_jogger()
