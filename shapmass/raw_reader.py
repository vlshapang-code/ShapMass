"""Read Thermo .raw files through Thermo's RawFileReader (.NET 8).

This is the ONLY module that talks to .NET. The rest of the app works with
plain Python numbers and numpy arrays, so the GUI never needs to know about
pythonnet or Thermo's API.

Requirements (see vendor/thermo/ and .gitignore for where these come from):
    - .NET 8 runtime in ~/.dotnet (or set DOTNET_ROOT)
    - Thermo DLLs in vendor/thermo/lib
"""
import os
import sys
from pathlib import Path

import numpy as np
from pythonnet import load

# --- Start the .NET runtime and load Thermo's DLLs (runs once, on import) ---
# pythonnet locates the runtime via the DOTNET_ROOT environment variable.
os.environ.setdefault("DOTNET_ROOT", str(Path.home() / ".dotnet"))
_THERMO_LIB = Path(__file__).resolve().parent.parent / "vendor" / "thermo" / "lib"

load("coreclr")

import clr  # noqa: E402  (only importable after load())

sys.path.append(str(_THERMO_LIB))
clr.AddReference("ThermoFisher.CommonCore.Data")
clr.AddReference("ThermoFisher.CommonCore.RawFileReader")

from ThermoFisher.CommonCore.Data.Business import (  # noqa: E402
    ChromatogramSignal,
    ChromatogramTraceSettings,
    Device,
    TraceType,
)
from ThermoFisher.CommonCore.RawFileReader import RawFileReaderAdapter  # noqa: E402


class RawFile:
    """An open Thermo .raw file. Use as a context manager or call close()."""

    def __init__(self, path):
        self.path = Path(path)
        self._raw = RawFileReaderAdapter.FileFactory(str(self.path))
        if self._raw.IsError:
            raise IOError(f"Could not open {self.path}: {self._raw.FileError.ErrorMessage}")
        if self._raw.GetInstrumentCountOfType(Device.MS) == 0:
            raise IOError(f"{self.path} contains no mass spectrometer data")
        self._raw.SelectInstrument(Device.MS, 1)

        header = self._raw.RunHeaderEx
        self.first_scan = header.FirstSpectrum
        self.last_scan = header.LastSpectrum
        self.instrument_model = str(self._raw.GetInstrumentData().Model)

    def tic(self):
        """Total ion chromatogram over all scans.

        Returns (retention_times_min, intensities, scan_numbers) as numpy arrays.
        """
        settings = ChromatogramTraceSettings(TraceType.TIC)
        data = self._raw.GetChromatogramData([settings], self.first_scan, self.last_scan)
        signal = ChromatogramSignal.FromChromatogramData(data)[0]
        return (
            np.array(list(signal.Times), dtype=float),
            np.array(list(signal.Intensities), dtype=float),
            np.array(list(signal.Scans), dtype=int),
        )

    def close(self):
        self._raw.Dispose()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
