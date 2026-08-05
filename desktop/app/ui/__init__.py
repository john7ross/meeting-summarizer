"""PySide UI layer: main window, dialogs and the QSS theme.

The visual design reproduces the Electron app's VS Code-style look using the
same colour tokens, so switching the rendering engine to Qt does not change the
appearance. Windows are split into focused modules (the settings dialog, the
queue panel, etc.) as each is ported, to avoid monolithic files.
"""
