#!/usr/bin/env python3
"""
Test script for the jogger module.
This demonstrates the jogger functionality without requiring a full curses environment.
"""

import sys
import time
from jogger import JoggerController


def test_jogger_commands():
    """Test the jogger command generation without UI."""
    print("🧪 Testing Jogger Command Generation")
    print("=" * 50)
    
    jogger = JoggerController()
    
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
    
    print("\n✅ Jogger command generation test completed!")


def test_gcode_generation():
    """Test G-code generation for different scenarios."""
    print("\n🔧 Testing G-code Generation")
    print("=" * 50)
    
    jogger = JoggerController()
    
    # Test different feed rates
    jogger.feed_rate = 500.0
    gcode = jogger.generate_gcode('x', 1.0)
    print(f"X movement at 500mm/min: {gcode}")
    
    jogger.feed_rate = 2000.0
    gcode = jogger.generate_gcode('y', -2.5)
    print(f"Y movement at 2000mm/min: {gcode}")
    
    # Test E axis
    gcode = jogger.generate_gcode('e', 5.0)
    print(f"E extrusion: {gcode}")
    
    # Test Z axis
    gcode = jogger.generate_gcode('z', -0.1)
    print(f"Z movement: {gcode}")
    
    print("\n✅ G-code generation test completed!")


def main():
    """Main test function."""
    print("🎮 Jogger Module Test Suite")
    print("=" * 50)
    
    try:
        test_jogger_commands()
        test_gcode_generation()
        
        print("\n" + "=" * 50)
        print("🎉 All tests completed successfully!")
        print("\nTo run the full interactive jogger:")
        print("python jogger.py")
        print("\nMake sure to install dependencies first:")
        print("pip install pynput")
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
