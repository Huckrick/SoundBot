"""Style the standalone editor's native window without replacing its plug-in UI."""

import sys


def editor_chrome(name, dark, owner=None):
    title = f"{name} · SoundBot"
    if sys.platform == "win32":
        return _once(lambda: _windows_chrome(title, dark, owner), require_owner=owner is not None)
    if sys.platform == "darwin":
        return _once(lambda: _mac_chrome(title, dark))
    return None


def _once(apply, *, require_owner=False):
    from time import monotonic

    applied = None
    next_check = 0

    def tick():
        nonlocal applied, next_check
        now = monotonic()
        if applied is not None or now < next_check:
            return applied
        next_check = now + 0.2
        try:
            applied = apply() or None
        except (OSError, AttributeError, ValueError):
            if require_owner:
                raise ValueError("无法将 VST3 窗口关联到 SoundBot") from None
            # Window chrome is optional. Keep the native editor functional.
            applied = lambda: None
        return applied

    return tick


def _windows_chrome(title, dark, owner):
    import ctypes as ct
    from ctypes import wintypes as wt

    user = ct.WinDLL("user32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    proc = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user.EnumThreadWindows.argtypes = [wt.DWORD, proc, wt.LPARAM]
    user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ct.c_int]
    user.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
    user.GetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int]
    user.GetWindowLongPtrW.restype = ct.c_ssize_t
    user.SetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int, ct.c_ssize_t]
    user.SetWindowLongPtrW.restype = ct.c_ssize_t
    user.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user.GetWindow.restype = wt.HWND
    user.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
    user.GetWindowThreadProcessId.restype = wt.DWORD
    user.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT]
    user.GetWindowRect.argtypes = [wt.HWND, ct.POINTER(wt.RECT)]
    user.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    user.MonitorFromWindow.restype = wt.HANDLE
    user.ShowWindow.argtypes = [wt.HWND, ct.c_int]
    user.IsIconic.argtypes = [wt.HWND]
    user.SetForegroundWindow.argtypes = [wt.HWND]
    user.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                         ct.POINTER(ct.c_size_t)]
    user.SendMessageTimeoutW.restype = ct.c_ssize_t
    user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.SendMessageW.restype = ct.c_ssize_t
    user.GetClassLongPtrW.argtypes = [wt.HWND, ct.c_int]
    user.GetClassLongPtrW.restype = ct.c_size_t
    kernel.GetCurrentThreadId.restype = wt.DWORD
    window = None

    @proc
    def find(handle, _):
        nonlocal window
        caption = ct.create_unicode_buffer(64)
        user.GetWindowTextW(handle, caption, len(caption))
        if caption.value == "Pedalboard":
            window = handle
            return False
        return True

    user.EnumThreadWindows(kernel.GetCurrentThreadId(), find, 0)
    if window is None:
        return False
    if owner:
        owner_process = wt.DWORD()
        handle = int(owner["handle"])
        user.GetWindowThreadProcessId(handle, ct.byref(owner_process))
        if owner_process.value != owner["pid"]:
            raise OSError("SoundBot owner window has closed")
        # Keep the native top-level window and its DPI context. Win32 ownership
        # groups it with SoundBot and hides it when the main window minimizes.
        user.SetWindowLongPtrW(window, -8, handle)  # GWLP_HWNDPARENT
        if user.GetWindow(window, 4) != handle:  # GW_OWNER
            raise ct.WinError(ct.get_last_error())
        ex_style_index = -20
        ex_style = user.GetWindowLongPtrW(window, ex_style_index)
        # An owned top-level window stays grouped with the app. Drop TOOLWINDOW
        # chrome so Windows paints the native minimize and close controls.
        user.SetWindowLongPtrW(window, ex_style_index, ex_style & ~(0x40000 | 0x80))
        for size, class_icon in ((0, -34), (1, -14)):
            result = ct.c_size_t()
            user.SendMessageTimeoutW(handle, 0x007f, size, 0, 0x2, 150, ct.byref(result))
            icon = result.value or user.GetClassLongPtrW(handle, class_icon)
            if icon:
                user.SendMessageW(window, 0x0080, size, icon)
    user.SetWindowTextW(window, title)
    style_index = -16  # GWL_STYLE
    style = user.GetWindowLongPtrW(window, style_index)
    caption, system_menu, minimize_box, thick_frame = 0xc00000, 0x80000, 0x20000, 0x40000
    user.SetWindowLongPtrW(window, style_index, (style | caption | system_menu | minimize_box) & ~0x10000)
    frame_changed = 0x20 | 0x04 | 0x10
    if owner:
        class MonitorInfo(ct.Structure):
            _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD)]

        user.GetMonitorInfoW.argtypes = [wt.HANDLE, ct.POINTER(MonitorInfo)]
        parent, editor = wt.RECT(), wt.RECT()
        monitor = MonitorInfo(size=ct.sizeof(MonitorInfo))
        if (user.GetWindowRect(handle, ct.byref(parent)) and user.GetWindowRect(window, ct.byref(editor)) and
                user.GetMonitorInfoW(user.MonitorFromWindow(handle, 2), ct.byref(monitor))):
            work = monitor.work
            width, height = editor.right - editor.left, editor.bottom - editor.top
            if style & thick_frame and width > 0 and height > 0:
                max_width = min((parent.right - parent.left) * 0.85, work.right - work.left)
                max_height = min((parent.bottom - parent.top) * 0.85, work.bottom - work.top)
                ratio = min(1, max_width / width, max_height / height)
                width, height = max(1, round(width * ratio)), max(1, round(height * ratio))
            left = max(work.left, min(parent.right - width - 36, work.right - width))
            top = max(work.top, min(parent.top + 72, work.bottom - height))
            user.SetWindowPos(window, None, left, top, width, height, frame_changed)
        else:
            user.SetWindowPos(window, None, 0, 0, 0, 0, frame_changed | 0x01 | 0x02)
    else:
        user.SetWindowPos(window, None, 0, 0, 0, 0, frame_changed | 0x01 | 0x02)

    def focus():
        if user.IsIconic(window):
            user.ShowWindow(window, 9)  # SW_RESTORE
        user.SetWindowPos(window, None, 0, 0, 0, 0, 0x01 | 0x02)
        user.SetForegroundWindow(window)

    try:
        dwm = ct.WinDLL("dwmapi")
    except OSError:
        return focus
    dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD]
    for attribute, value in (
        (20, int(dark)),  # DWMWA_USE_IMMERSIVE_DARK_MODE
        (34, 0x393939 if dark else 0xD6D6D6),  # DWMWA_BORDER_COLOR
        (35, 0x161616 if dark else 0xF4F4F4),  # DWMWA_CAPTION_COLOR (COLORREF)
        (36, 0xF4F4F4 if dark else 0x202020),  # DWMWA_TEXT_COLOR
        (33, 2),  # DWMWA_WINDOW_CORNER_PREFERENCE: round on Windows 11
    ):
        setting = wt.DWORD(value)
        dwm.DwmSetWindowAttribute(window, attribute, ct.byref(setting), ct.sizeof(setting))
    return focus


def _mac_chrome(title, dark):
    import ctypes as ct

    objc = ct.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ct.c_char_p]
    objc.objc_getClass.restype = ct.c_void_p
    objc.sel_registerName.argtypes = [ct.c_char_p]
    objc.sel_registerName.restype = ct.c_void_p
    ptr = ct.c_void_p

    def send(target, selector, result=ptr, *args):
        types = tuple(kind for kind, _ in args)
        call = ct.CFUNCTYPE(result, ptr, ptr, *types)(("objc_msgSend", objc))
        return call(target, objc.sel_registerName(selector), *(value for _, value in args))

    app = send(objc.objc_getClass(b"NSApplication"), b"sharedApplication")
    windows = send(app, b"windows")
    for index in range(send(windows, b"count", ct.c_size_t)):
        window = send(windows, b"objectAtIndex:", ptr, (ct.c_size_t, index))
        if send(send(window, b"title"), b"UTF8String", ct.c_char_p) != b"Pedalboard":
            continue
        string_class = objc.objc_getClass(b"NSString")
        label = send(string_class, b"stringWithUTF8String:", ptr, (ct.c_char_p, title.encode()))
        send(window, b"setTitle:", None, (ptr, label))
        mode = b"NSAppearanceNameDarkAqua" if dark else b"NSAppearanceNameAqua"
        appearance_name = send(string_class, b"stringWithUTF8String:", ptr, (ct.c_char_p, mode))
        appearance = send(objc.objc_getClass(b"NSAppearance"), b"appearanceNamed:", ptr, (ptr, appearance_name))
        send(window, b"setAppearance:", None, (ptr, appearance))
        def focus():
            send(window, b"deminiaturize:", None, (ptr, None))
            send(window, b"makeKeyAndOrderFront:", None, (ptr, None))
            send(app, b"activateIgnoringOtherApps:", None, (ct.c_bool, True))

        return focus
    return False
