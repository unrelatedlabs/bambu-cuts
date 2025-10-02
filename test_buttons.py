#!/usr/bin/env python3
"""
Test the button layout for the Textual jogger.
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Header, Footer, Static


class ButtonTestApp(App):
    """Simple app to test button layout."""
    
    CSS = """
    Screen {
        layout: vertical;
    }
    
    .controls-panel {
        height: 12;
        border: solid $primary;
        margin: 1;
        padding: 1;
    }
    
    .axis-controls {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1;
        margin: 1 0;
        height: auto;
    }
    
    .move-button {
        min-width: 6;
        min-height: 3;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        
        with Container(classes="controls-panel"):
            yield Static("🎯 Movement Controls", classes="header")
            
            # X/Y Controls
            with Container(classes="axis-controls"):
                yield Button("Y+", classes="move-button", id="y-plus")
                yield Button("X+", classes="move-button", id="x-plus")
                yield Button("Y+", classes="move-button", id="y-plus-fine")
                
                yield Button("X-", classes="move-button", id="x-minus")
                yield Button("XY", classes="move-button", id="center")
                yield Button("X+", classes="move-button", id="x-plus-fine")
                
                yield Button("Y-", classes="move-button", id="y-minus")
                yield Button("X-", classes="move-button", id="x-minus-fine")
                yield Button("Y-", classes="move-button", id="y-minus-fine")
            
            # Z Controls
            with Container(classes="axis-controls"):
                yield Button("Z+", classes="move-button", id="z-plus")
                yield Button("Z", classes="move-button", id="z-label")
                yield Button("Z+", classes="move-button", id="z-plus-fine")
                
                yield Button("", classes="move-button", id="z-empty1")
                yield Button("Z", classes="move-button", id="z-label2")
                yield Button("", classes="move-button", id="z-empty2")
                
                yield Button("Z-", classes="move-button", id="z-minus")
                yield Button("Z", classes="move-button", id="z-label3")
                yield Button("Z-", classes="move-button", id="z-minus-fine")
            
            # E Controls
            with Container(classes="axis-controls"):
                yield Button("E+", classes="move-button", id="e-plus")
                yield Button("E", classes="move-button", id="e-label")
                yield Button("E+", classes="move-button", id="e-plus-fine")
                
                yield Button("", classes="move-button", id="e-empty1")
                yield Button("E", classes="move-button", id="e-label2")
                yield Button("", classes="move-button", id="e-empty2")
                
                yield Button("E-", classes="move-button", id="e-minus")
                yield Button("E", classes="move-button", id="e-label3")
                yield Button("E-", classes="move-button", id="e-minus-fine")


def main():
    """Test the button layout."""
    print("Testing button layout...")
    app = ButtonTestApp()
    app.run()


if __name__ == "__main__":
    main()
