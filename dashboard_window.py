"""
UnamOS native dashboard window — WKWebView embedded in a floating NSPanel.
Toggle show/hide via menubar. Loads http://127.0.0.1:7700.
"""
import logging
from AppKit import (
    NSPanel, NSColor, NSMakeRect, NSBackingStoreBuffered,
    NSScreen, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskFullSizeContentView,
    NSTitledWindowMask, NSClosableWindowMask, NSResizableWindowMask,
)
from WebKit import WKWebView, WKWebViewConfiguration
from Foundation import NSURL, NSURLRequest

log = logging.getLogger(__name__)

_W, _H = 900, 640
_URL = "http://127.0.0.1:7700"


class DashboardWindow:
    def __init__(self):
        self._win = None
        self._webview = None

    def _build(self):
        screen = NSScreen.mainScreen()
        sf = screen.frame()
        x = sf.origin.x + (sf.size.width - _W) / 2
        y = sf.origin.y + (sf.size.height - _H) / 2

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskFullSizeContentView
        )

        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _W, _H),
            style,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("UnamOS")
        win.setTitlebarAppearsTransparent_(True)
        win.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.05, 0.07, 1.0))
        win.setLevel_(8)  # floating above normal windows
        win.setCollectionBehavior_(1 | 64)  # CanJoinAllSpaces | FullScreenAuxiliary

        cfg = WKWebViewConfiguration.alloc().init()
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, _W, _H), cfg
        )
        webview.setAutoresizingMask_(18)  # flexible width + height
        win.contentView().addSubview_(webview)

        self._win = win
        self._webview = webview
        log.info("Dashboard window built")

    def _load(self):
        url = NSURL.URLWithString_(_URL)
        req = NSURLRequest.requestWithURL_(url)
        self._webview.loadRequest_(req)

    def toggle(self):
        if self._win is None:
            self._build()
            self._load()
            self._win.makeKeyAndOrderFront_(None)
            return

        if self._win.isVisible():
            self._win.orderOut_(None)
        else:
            self._load()
            self._win.makeKeyAndOrderFront_(None)

    def show(self):
        if self._win is None:
            self._build()
        self._load()
        self._win.makeKeyAndOrderFront_(None)

    def hide(self):
        if self._win:
            self._win.orderOut_(None)
