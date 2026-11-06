"""Short-lived client credentials, never process arguments or inherited MYSQL_PWD."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


def _restrict_folder(path):
    if os.name != "nt":
        os.chmod(path, 0o700)
        return
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(pointer),ctypes.POINTER(wintypes.ULONG)]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.GetSecurityDescriptorDacl.argtypes = [pointer,ctypes.POINTER(wintypes.BOOL),ctypes.POINTER(pointer),ctypes.POINTER(wintypes.BOOL)]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,pointer,pointer,pointer,pointer]
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    descriptor = pointer()
    sddl = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):
        raise OSError("Protection du dossier d'options MySQL impossible.")
    try:
        present, defaulted, acl = wintypes.BOOL(),wintypes.BOOL(),pointer()
        if not api.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(acl),ctypes.byref(defaulted)) or not present:
            raise OSError("Protection du dossier d'options MySQL impossible.")
        if api.SetNamedSecurityInfoW(str(path),1,0x80000004,None,None,acl,None):
            raise OSError("Protection du dossier d'options MySQL impossible.")
    finally:
        kernel.LocalFree(descriptor)


def _quoted(value):
    text = str(value)
    if "\x00" in text:
        raise ValueError("Valeur d'option MySQL non représentable.")
    for old,new in (("\\","\\\\"),("\"","\\\""),("\n","\\n"),("\r","\\r"),("\t","\\t"),("\b","\\b")):
        text = text.replace(old,new)
    return '"' + text + '"'


@contextmanager
def private_mysql_options(settings):
    with tempfile.TemporaryDirectory(prefix="sme_mysql_options_") as folder:
        folder = Path(folder)
        _restrict_folder(folder)  # Must succeed before any credential is written.
        path = folder / "client.cnf"
        values = {"host":settings.mysql_host,"port":settings.mysql_port,
                  "user":settings.mysql_user,"password":settings.mysql_password,
                  "default-character-set":"utf8mb4"}
        content = "[client]\n" + "".join(f"{key}={_quoted(value)}\n" for key,value in values.items())
        descriptor = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,"w",encoding="utf-8",newline="\n") as file:
            file.write(content)
        env = {key:value for key,value in os.environ.items() if key.upper() != "MYSQL_PWD"}
        # MySQL 8 reads .mylogin.cnf even with --defaults-file. Isolate it too.
        env["MYSQL_TEST_LOGIN_FILE"] = str(folder / "unused.mylogin.cnf")
        yield path, env
