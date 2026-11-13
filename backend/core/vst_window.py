"""Style the standalone editor's native window without replacing its plug-in UI."""

import sys


def editor_chrome(name, dark):
    title = f"{name} · SoundBot"
    if sys.platform == "win32":
        return _once(lambda: _windows_chrome(title, dark))
    if sys.platform == "darwin":
        return _once(lambda: _mac_chrome(title, dark))
    return None


def _once(apply):
    from time import monotonic

    applied = False
    next_check = 0

    def tick():
        nonlocal applied, next_check
        now = monotonic()
        if applied or now < next_check:
            return
        next_check = now + 0.2
        try:
            applied = apply()
        except (OSError, AttributeError, ValueError):
            # Window chrome is optional. Keep the native editor functional.
            applied = True

    return tick


def _windows_chrome(title, dark):
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
    user.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT]
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
    user.SetWindowTextW(window, title)
    style_index = -16  # GWL_STYLE
    style = user.GetWindowLongPtrW(window, style_index)
    thick_frame, maximize_box, system_menu = 0x40000, 0x10000, 0x80000
    if style & thick_frame:
        user.SetWindowLongPtrW(window, style_index, style | maximize_box | system_menu)
        frame_changed = 0x20 | 0x01 | 0x02 | 0x04 | 0x10
        user.SetWindowPos(window, None, 0, 0, 0, 0, frame_changed)

    dwm = ct.WinDLL("dwmapi")
    dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD]
    for attribute, value in (
        (20, int(dark)),  # DWMWA_USE_IMMERSIVE_DARK_MODE
        (35, 0x242321 if dark else 0xF5F4F2),  # DWMWA_CAPTION_COLOR (COLORREF)
        (36, 0xF4F4F5 if dark else 0x252525),  # DWMWA_TEXT_COLOR
    ):
        setting = wt.DWORD(value)
        dwm.DwmSetWindowAttribute(window, attribute, ct.byref(setting), ct.sizeof(setting))
    return True


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
        return True
    return False
