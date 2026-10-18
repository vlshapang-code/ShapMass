"""Read mzML files (the open, vendor-neutral mass spec format).

Uses only numpy and the standard library, so unlike raw_reader.py it needs no
.NET runtime or vendor DLLs. MzmlFile has the same methods as RawFile, so the
GUI treats both the same way.

Supported: 32/64-bit float or integer arrays, uncompressed or zlib-compressed.
Not supported: MS-Numpress compression and gzipped (.mzML.gz) files.
"""
import base64
import mmap
import re
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np

# PSI-MS controlled-vocabulary accessions this reader looks for.
_MS_LEVEL = "MS:1000511"
_CENTROID = "MS:1000127"
_TOTAL_ION_CURRENT = "MS:1000285"
_POSITIVE = "MS:1000130"
_NEGATIVE = "MS:1000129"
_SCAN_START_TIME = "MS:1000016"
_FILTER_STRING = "MS:1000512"
_WINDOW_LOWER = "MS:1000501"
_WINDOW_UPPER = "MS:1000500"
_SELECTED_MZ = "MS:1000744"
_MZ_ARRAY = "MS:1000514"
_INTENSITY_ARRAY = "MS:1000515"
_SECONDS = "UO:0000010"
_ZLIB = "MS:1000574"
_DTYPES = {"MS:1000521": "<f4", "MS:1000523": "<f8", "MS:1000519": "<i4", "MS:1000522": "<i8"}
_NUMPRESS = {"MS:1002312", "MS:1002313", "MS:1002314", "MS:1002746", "MS:1002747", "MS:1002748"}

# Centroided peaks closer than this (parts per million) are merged when averaging.
_CENTROID_MERGE_PPM = 10

_SPECTRUM_START = re.compile(rb"<spectrum\s")
_SPECTRUM_END = b"</spectrum>"
_PARAM_GROUPS = re.compile(rb"<referenceableParamGroupList.*?</referenceableParamGroupList>", re.S)
_SCAN_IN_ID = re.compile(r"\bscan=(\d+)")


def _params(elem, groups):
    """{accession: cvParam element} for elem's own cvParams plus any referenced param groups."""
    found = {}
    for ref in elem.findall("referenceableParamGroupRef"):
        found.update(groups.get(ref.get("ref"), {}))
    for param in elem.findall("cvParam"):
        found[param.get("accession")] = param
    return found


def _value(params, accession):
    return float(params[accession].get("value"))


class MzmlFile:
    """An open mzML file. Use as a context manager or call close()."""

    def __init__(self, path):
        self.path = Path(path)
        self.instrument_model = "mzML"
        self._file = open(self.path, "rb")
        try:
            # Memory-map the file so spectra are read on demand, not all loaded at once.
            self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self._index_spectra()
        except Exception:
            self.close()
            raise

    def _index_spectra(self):
        """One pass over the file: where each spectrum is, plus its RT, type and TIC."""
        starts = [m.start() for m in _SPECTRUM_START.finditer(self._mm)]
        if not starts:
            raise IOError(f"{self.path.name} contains no spectra")
        groups_xml = _PARAM_GROUPS.search(self._mm, 0, starts[0])
        self._groups = {}
        if groups_xml:
            for group in ET.fromstring(groups_xml.group()).findall("referenceableParamGroup"):
                self._groups[group.get("id")] = _params(group, {})

        self._spans, self._types, self._centroid = [], [], []
        rts, tics, native_scans = [], [], []
        for pos, start in enumerate(starts):
            end = self._mm.find(_SPECTRUM_END, start)
            if end < 0:
                raise IOError(f"{self.path.name} is truncated (spectrum {pos + 1} never ends)")
            self._spans.append((start, end + len(_SPECTRUM_END)))
            spec = self._parse(pos)
            params = _params(spec, self._groups)
            scan = spec.find("scanList/scan")
            scan_params = _params(scan, self._groups) if scan is not None else {}

            if _SCAN_START_TIME in scan_params:
                rt = _value(scan_params, _SCAN_START_TIME)
                if scan_params[_SCAN_START_TIME].get("unitAccession") == _SECONDS:
                    rt /= 60
            else:
                rt = float(pos)
            rts.append(rt)

            if _TOTAL_ION_CURRENT in params:
                tics.append(_value(params, _TOTAL_ION_CURRENT))
            else:
                tics.append(float(self._array(spec, _INTENSITY_ARRAY).sum()))

            self._centroid.append(_CENTROID in params)
            self._types.append(self._describe(spec, params, scan, scan_params))
            match = _SCAN_IN_ID.search(spec.get("id", ""))
            native_scans.append(int(match.group(1)) if match else None)

        # Use the instrument's scan numbers (e.g. "scan=1234" in Thermo-converted files)
        # when every spectrum has one and they increase; otherwise just number 1, 2, 3...
        if None not in native_scans and all(a < b for a, b in zip(native_scans, native_scans[1:])):
            scans = native_scans
        else:
            scans = list(range(1, len(starts) + 1))
        self._scans = np.array(scans, dtype=int)
        self._pos = {s: p for p, s in enumerate(scans)}
        self._rts = np.array(rts, dtype=float)
        self._tics = np.array(tics, dtype=float)
        self.first_scan = int(self._scans[0])
        self.last_scan = int(self._scans[-1])

    def _describe(self, spec, params, scan, scan_params):
        """The scan type: the vendor filter string if present, else one built from the metadata."""
        if _FILTER_STRING in scan_params:
            return scan_params[_FILTER_STRING].get("value")
        level = params[_MS_LEVEL].get("value") if _MS_LEVEL in params else "?"
        polarity = "+" if _POSITIVE in params else "-" if _NEGATIVE in params else ""
        parts = [f"MS{level}", polarity, "c" if _CENTROID in params else "p"]
        ion = spec.find("precursorList/precursor/selectedIonList/selectedIon")
        if ion is not None:
            ion_params = _params(ion, self._groups)
            if _SELECTED_MZ in ion_params:
                parts.append(f"{_value(ion_params, _SELECTED_MZ):.4f}")
        window = scan.find("scanWindowList/scanWindow") if scan is not None else None
        if window is not None:
            window_params = _params(window, self._groups)
            if _WINDOW_LOWER in window_params and _WINDOW_UPPER in window_params:
                parts.append(f"[{_value(window_params, _WINDOW_LOWER):.4f}-"
                             f"{_value(window_params, _WINDOW_UPPER):.4f}]")
        return " ".join(p for p in parts if p)

    def _parse(self, pos):
        start, end = self._spans[pos]
        return ET.fromstring(self._mm[start:end])

    def _array(self, spec, kind):
        """Decode one binary data array (m/z or intensity) of a spectrum to a float numpy array."""
        for array in spec.iterfind("binaryDataArrayList/binaryDataArray"):
            params = _params(array, self._groups)
            if kind not in params:
                continue
            if _NUMPRESS & params.keys():
                raise IOError(f"{self.path.name} uses MS-Numpress compression, which ShapMass "
                              "can't read. Re-convert it without Numpress.")
            dtype = next((_DTYPES[a] for a in params if a in _DTYPES), None)
            if dtype is None:
                raise IOError(f"{self.path.name} uses an unsupported binary data type")
            data = base64.b64decode(array.findtext("binary") or "")
            if _ZLIB in params:
                data = zlib.decompress(data)
            return np.frombuffer(data, dtype=dtype).astype(float)
        return np.zeros(0)

    def _read(self, pos):
        spec = self._parse(pos)
        return self._array(spec, _MZ_ARRAY), self._array(spec, _INTENSITY_ARRAY)

    def tic(self):
        """Total ion chromatogram over all scans.

        Returns (retention_times_min, intensities, scan_numbers) as numpy arrays.
        """
        return self._rts, self._tics, self._scans

    def scan_number_at(self, retention_time):
        """Scan number closest to a retention time (minutes)."""
        return int(self._scans[np.argmin(np.abs(self._rts - retention_time))])

    def retention_time(self, scan):
        return float(self._rts[self._pos[scan]])

    def scan_type(self, scan):
        return self._types[self._pos[scan]]

    def step_scan(self, scan, step):
        """The scan `step` (+1 or -1) away among scans of the same type, or `scan` if none."""
        pos = self._pos[scan]
        wanted = self._types[pos]
        p = pos + step
        while 0 <= p < len(self._types):
            if self._types[p] == wanted:
                return int(self._scans[p])
            p += step
        return scan

    def snap_to_type(self, scan, like_scan):
        """Nearest scan at or before `scan` with the same type as `like_scan`
        (never earlier than like_scan)."""
        pos, like = self._pos[scan], self._pos[like_scan]
        while pos > like and self._types[pos] != self._types[like]:
            pos -= 1
        return int(self._scans[pos])

    def spectrum(self, scan):
        """One scan as (mz, intensity) numpy arrays."""
        return self._read(self._pos[scan])

    def average(self, first_scan, last_scan):
        """Average scans first_scan..last_scan of the same type as first_scan.

        Returns (mz, intensity) numpy arrays.
        """
        first, last = self._pos[first_scan], self._pos[last_scan]
        spectra = [self._read(p) for p in range(first, last + 1)
                   if self._types[p] == self._types[first]]
        if self._centroid[first]:
            return _average_centroids(spectra)
        return _average_profiles(spectra)

    def close(self):
        if getattr(self, "_mm", None) is not None:
            self._mm.close()
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _average_profiles(spectra):
    """Average profile spectra by interpolating each onto the union of all their m/z points."""
    grid = np.unique(np.concatenate([mz for mz, _ in spectra]))
    total = np.zeros_like(grid)
    for mz, intensity in spectra:
        if mz.size:
            total += np.interp(grid, mz, intensity, left=0, right=0)
    return grid, total / len(spectra)


def _average_centroids(spectra):
    """Average centroided spectra, merging peaks within _CENTROID_MERGE_PPM of each other."""
    mz = np.concatenate([m for m, _ in spectra])
    intensity = np.concatenate([i for _, i in spectra])
    if mz.size == 0:
        return mz, intensity
    order = np.argsort(mz, kind="stable")
    mz, intensity = mz[order], intensity[order]
    new_peak = np.diff(mz) > mz[1:] * _CENTROID_MERGE_PPM * 1e-6
    starts = np.concatenate(([0], np.flatnonzero(new_peak) + 1))
    summed = np.add.reduceat(intensity, starts)
    mean_mz = np.add.reduceat(mz, starts) / np.diff(np.append(starts, mz.size))
    peak_mz = np.divide(np.add.reduceat(mz * intensity, starts), summed,
                        out=mean_mz, where=summed > 0)
    return peak_mz, summed / len(spectra)
