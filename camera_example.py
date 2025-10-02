#!/usr/bin/env python3
"""
Simple example of using the A1 Mini camera frame extractor
"""

from camera_frame_extractor import A1MiniCameraExtractor
from config import PRINTER_IP, ACCESS_CODE

def main():
    print("A1 Mini Camera Frame Extractor Example")
    print("=" * 40)
    
    # Create extractor using config.py settings
    extractor = A1MiniCameraExtractor()
    
    print("Choose an option:")
    print("1. Extract single frame (auto method)")
    print("2. Extract single frame (RTSP method)")
    print("3. Live camera view")
    print("4. Test simple RTSP connection")
    
    choice = input("\nEnter choice (1-4): ").strip()
    
    if choice == "1":
        # Extract a frame (will try all methods automatically)
        success = extractor.extract_frame()
        if success:
            print("\n✓ Frame extraction successful!")
            print("Check the current directory for the captured frame image.")
        else:
            print("\n✗ Frame extraction failed.")
            print("Please check your printer connection and camera settings.")
    
    elif choice == "2":
        # Extract using RTSP method specifically
        success = extractor.extract_frame(method="rtsp")
        if success:
            print("\n✓ RTSP frame extraction successful!")
        else:
            print("\n✗ RTSP frame extraction failed.")
    
    elif choice == "3":
        # Live camera view
        success = extractor.live_camera_view()
        if success:
            print("\n✓ Live view completed successfully!")
        else:
            print("\n✗ Live view failed.")
    
    elif choice == "4":
        # Test simple RTSP connection
        from test_camera_simple import test_rtsp_connection
        success = test_rtsp_connection()
        if success:
            print("\n✓ Simple RTSP test completed!")
        else:
            print("\n✗ Simple RTSP test failed.")
    
    else:
        print("Invalid choice. Please run again and select 1-4.")

if __name__ == "__main__":
    main()
