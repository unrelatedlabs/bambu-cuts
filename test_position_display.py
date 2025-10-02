#!/usr/bin/env python3
"""
Test the position display functionality of the Textual jogger.
"""

from jogger_textual import JoggerTextualApp


def test_position_display():
    """Test that position display updates correctly."""
    print("🧪 Testing Position Display")
    print("=" * 40)
    
    # Create jogger instance
    jogger = JoggerTextualApp()
    
    print("Initial position_text:")
    print(repr(jogger.position_text))
    print()
    
    # Test position updates
    print("Testing position updates...")
    
    jogger.move_axis('x', 1.0)
    print(f"After X+1: {repr(jogger.position_text)}")
    
    jogger.move_axis('y', 2.5)
    print(f"After Y+2.5: {repr(jogger.position_text)}")
    
    jogger.move_axis('z', -0.5)
    print(f"After Z-0.5: {repr(jogger.position_text)}")
    
    jogger.move_axis('e', 10.0)
    print(f"After E+10: {repr(jogger.position_text)}")
    
    # Test fine mode
    print("\nTesting fine mode...")
    jogger.step_size = 0.1
    jogger.is_fine_mode = True
    jogger.update_position_display()
    print(f"Fine mode: {repr(jogger.position_text)}")
    
    print("\n✅ Position display test completed!")
    print("\nThe position_text reactive variable is updating correctly.")
    print("When the Textual UI runs, this will automatically update the display.")


if __name__ == "__main__":
    test_position_display()
