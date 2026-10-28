import ctypes
from ctypes import wintypes
import os

class Credential(ctypes.Structure):
    _fields_ = [('Flags',wintypes.DWORD), ('Type',wintypes.DWORD), ('TargetName',wintypes.LPWSTR),
                ('Comment',wintypes.LPWSTR), ('LastWritten',wintypes.FILETIME),
                ('CredentialBlobSize',wintypes.DWORD), ('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist',wintypes.DWORD), ('AttributeCount',wintypes.DWORD), ('Attributes',ctypes.c_void_p),
                ('TargetAlias',wintypes.LPWSTR), ('UserName',wintypes.LPWSTR)]


class Credentials:
    """Native Windows generic credentials; environment override for development."""
    def __init__(self):
        self.api = ctypes.WinDLL('Advapi32.dll', use_last_error=True) if os.name == 'nt' else None
        if self.api:
            self.api.CredReadW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
            self.api.CredWriteW.argtypes = [ctypes.POINTER(Credential),wintypes.DWORD]
            self.api.CredDeleteW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD]
            self.api.CredFree.argtypes = [ctypes.c_void_p]

    @staticmethod
    def env_name(provider):
        return 'OPENAI_API_KEY' if provider == 'openai' else 'MESHY_API_KEY'

    def get(self, provider):
        override = os.environ.get(self.env_name(provider),'').strip()
        if override:
            return override
        if not self.api:
            return ''
        pointer = ctypes.POINTER(Credential)()
        if not self.api.CredReadW('BlockBuilder/'+provider,1,0,ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return ''
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
        finally:
            self.api.CredFree(pointer)

    def set(self, provider, value):
        if not self.api:
            raise ValueError('Use environment variables on non-Windows systems.')
        data = value.strip().encode('utf-8')
        blob = (ctypes.c_ubyte*len(data)).from_buffer_copy(data)
        cred = Credential()
        cred.Type = 1
        cred.TargetName = 'BlockBuilder/'+provider
        cred.UserName = provider
        cred.CredentialBlobSize = len(data)
        cred.CredentialBlob = blob
        cred.Persist = 2
        if not self.api.CredWriteW(ctypes.byref(cred),0):
            raise ctypes.WinError(ctypes.get_last_error())

    def remove(self, provider):
        if self.api and not self.api.CredDeleteW('BlockBuilder/'+provider,1,0) and ctypes.get_last_error()!=1168:
            raise ctypes.WinError(ctypes.get_last_error())
