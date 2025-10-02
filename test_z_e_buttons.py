#!/usr/bin/env python3
"""
Test Z and E buttons visibility in Textual.
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Header, Footer, Static


class ZETestApp(App):
    """Simple app to test Z and E buttons."""
    
    CSS = """
    Screen {
        layout: vertical;
    }
    
    .main-container {
        layout: horizontal;
        height: 1fr;
        margin: 1;
    }
    
    .dpad-container {
        layout: vertical;
        margin: 1 0;
        height: auto;
        align: center middle;
        width: 1fr;
    }
    
    .z-e-controls {
        layout: vertical;
        margin: 0 0 0 2;
        width: 25;
        height: auto;
    }
    
    .dpad-button {
        min-width: 6;
        min-height: 3;
        margin: 1;
        background: $primary;
        color: $text;
    }
    
    .move-button {
        min-width: 8;
        min-height: 3;
        margin: 1;
        background: $primary;
        color: $text;
        border: solid $primary;
    }
    
    .move-button:hover {
        background: $secondary;
        border: solid $secondary;
    }
    
    .center-button {
        min-width: 6;
        min-height: 3;
        margin: 1;
        background: $warning;
        color: $text;
    }
    
    .empty-button {
        min-width: 6;
        min-height: 3;
        margin: 1;
        background: transparent;
        color: transparent;
        border: none;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        
        with Container(classes="main-container"):
            # Left side: D-pad for X/Y Movement
            with Container(classes="dpad-container"):
                # Top row (Y+)
                with Horizontal():
                    yield Button("", classes="empty-button", id="empty-top-left")
                    yield Button("Y+", classes="dpad-button", id="y-plus")
                    yield Button("", classes="empty-button", id="empty-top-right")
                
                # Middle row (X- and X+)
                with Horizontal():
                    yield Button("X-", classes="dpad-button", id="x-minus")
                    yield Button("XY", classes="center-button", id="center")
                    yield Button("X+", classes="dpad-button", id="x-plus")
                
                # Bottom row (Y-)
                with Horizontal():
                    yield Button("", classes="empty-button", id="empty-bottom-left")
                    yield Button("Y-", classes="dpad-button", id="y-minus")
                    yield Button("", classes="empty-button", id="empty-bottom-right")
            
            # Right side: Z and E Controls (Vertical)
            with Vertical(classes="z-e-controls"):
                # Z Controls
                yield Static("Z Movement:", classes="header")
                yield Button("Z+", classes="move-button", id="z-plus")
                yield Button("Z-", classes="move-button", id="z-minus")
                yield Button("Z+", classes="move-button", id="z-plus-fine")
                yield Button("Z-", classes="move-button", id="z-minus-fine")
                
                # E Controls
                yield Static("E Movement:", classes="header")
                yield Button("E+", classes="move-button", id="e-plus")
                yield Button("E-", classes="move-button", id="e-minus")
                yield Button("E+", classes="move-button", id="e-plus-fine")
                yield Button("E-", classes="move-button", id="e-minus-fine")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        print(f"Button pressed: {button_id}")


def main():
    """Test the Z and E buttons."""
    print("Testing Z and E buttons visibility...")
    app = ZETestApp()
    app.run()


if __name__ == "__main__":
    main()

