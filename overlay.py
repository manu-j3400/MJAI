"""UnamOS floating overlay — bare minimum, definitely visible."""
import logging
from AppKit import (
    NSPanel, NSTextField, NSColor, NSFont,
    NSMakeRect, NSBackingStoreBuffered,
    NSTextAlignmentCenter, NSScreen,
)

_W, _H  = 660, 64
_LEVEL  = 200   # above everything

log = logging.getLogger(__name__)


class Overlay:
    def __init__(self):
        self._win   = None
        self._label = None

    def _best_screen(self):
        screens = list(NSScreen.screens())
        return max(screens, key=lambda s: s.frame().size.width) if screens else NSScreen.mainScreen()

    def _pos(self):
        screen = self._best_screen()
        sf = screen.frame()
        vf = screen.visibleFrame()
        x  = sf.origin.x + (sf.size.width - _W) / 2
        # Just below menu bar — top of visible frame minus bar height and a gap
        y  = vf.origin.y + vf.size.height - _H - 10
        return x, y, sf

    def _build(self):
        x, y, sf = self._pos()

        # Borderless panel, fully opaque so it MUST be visible
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _W, _H), 128, NSBackingStoreBuffered, False
        )
        win.setLevel_(_LEVEL)
        win.setOpaque_(True)
        win.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.10, 0.14, 1.0)
        )
        win.setHasShadow_(True)
        win.setIgnoresMouseEvents_(True)
        win.setCollectionBehavior_(1)   # NSWindowCollectionBehaviorCanJoinAllSpaces

        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 0, _W - 40, _H))
        lbl.setAlignment_(NSTextAlignmentCenter)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setTextColor_(NSColor.whiteColor())
        lbl.setFont_(NSFont.boldSystemFontOfSize_(17))
        win.contentView().addSubview_(lbl)

        self._win   = win
        self._label = lbl
        log.info("Overlay built x=%.0f y=%.0f screen=%.0fx%.0f", x, y, sf.size.width, sf.size.height)

    def _reposition(self):
        x, y, _ = self._pos()
        self._win.setFrame_display_(NSMakeRect(x, y, _W, _H), False)

    def show(self, text: str, color=None):
        if self._win is None:
            self._build()
        else:
            self._reposition()
        self._label.setStringValue_(text)
        self._win.setAlphaValue_(1.0)
        self._win.orderFrontRegardless()
        log.info("Overlay show: %s", text)

    def update(self, text: str, color=None):
        if self._win is None:
            self.show(text, color); return
        self._label.setStringValue_(text)
        log.info("Overlay update: %s", text)

    def hide(self):
        if self._win is None:
            return
        self._win.setAlphaValue_(0.0)
        self._win.orderOut_(None)
        log.info("Overlay hidden.")
