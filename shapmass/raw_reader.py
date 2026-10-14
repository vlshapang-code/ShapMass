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

from ThermoFisher.CommonCore.Data import Extensions  # noqa: E402
from ThermoFisher.CommonCore.Data.Business import (  # noqa: E402
    ChromatogramSignal,
    ChromatogramTraceSettings,
    Device,
    TraceType,
)
from ThermoFisher.CommonCore.RawFileReader import RawFileReaderAdapter  # noqa: E402


def _to_numpy(net_array):
    """Copy a .NET double[] into a numpy array."""
    return np.array(list(net_array), dtype=float)


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

    def scan_number_at(self, retention_time):
        """Scan number closest to a retention time (minutes)."""
        return int(self._raw.ScanNumberFromRetentionTime(retention_time))

    def retention_time(self, scan):
        return float(self._raw.RetentionTimeFromScanNumber(scan))

    def scan_type(self, scan):
        """Thermo's scan description, e.g. 'FTMS + p ESI sid=20.00 Full ms [700.0000-3000.0000]'."""
        return str(self._raw.GetScanEventStringForScanNumber(scan))

    def spectrum(self, scan):
        """One scan as (mz, intensity) numpy arrays (profile points if recorded in profile)."""
        segmented = self._raw.GetSegmentedScanFromScanNumber(scan, None)
        return _to_numpy(segmented.Positions), _to_numpy(segmented.Intensities)

    def average(self, first_scan, last_scan):
        """Average scans first_scan..last_scan, using Thermo's own averaging.

        Only scans with the same scan type as first_scan are included, so
        methods that alternate scan types (e.g. 3-phase in-source CID) aren't mixed.
        Returns (mz, intensity) numpy arrays.
        """
        scan_filter = self._raw.GetFilterForScanNumber(first_scan)
        averaged = Extensions.AverageScansInScanRange(
            self._raw, first_scan, last_scan, scan_filter, None, None
        )
        if averaged is None:
            raise ValueError(f"No scans to average between {first_scan} and {last_scan}")
        segmented = averaged.SegmentedScan
        return _to_numpy(segmented.Positions), _to_numpy(segmented.Intensities)

    def close(self):
        self._raw.Dispose()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
