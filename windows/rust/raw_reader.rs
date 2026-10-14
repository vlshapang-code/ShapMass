//! Read Thermo .raw files. Rust twin of `shapmass/raw_reader.py`.
//!
//! Uses the `thermorawfilereader` crate, which hosts Thermo's own RawFileReader
//! in .NET 8, just like the Python version does through pythonnet.
//!
//! Scan numbers here are Thermo scan numbers (starting at 1), same as Python.
//!
//! One real difference: the crate does not expose Thermo's scan averaging, so
//! `average()` is our own (see its comment). Python uses Thermo's.

use std::io;
use std::path::Path;

use thermorawfilereader::RawFileReader;

pub struct RawFile {
    reader: RawFileReader,
    pub instrument_model: String,
    pub first_scan: usize,
    pub last_scan: usize,
    /// Retention time (minutes) of every scan; index = scan - first_scan.
    times: Vec<f64>,
    /// Thermo scan description of every scan, e.g. "FTMS + p ESI sid=20.00 Full ms [...]".
    scan_types: Vec<String>,
}

impl RawFile {
    pub fn open(path: &Path) -> io::Result<Self> {
        let mut reader = RawFileReader::open(path)?;
        let instrument_model = reader.instrument_model().model().unwrap_or("unknown").to_string();

        // Read every scan's time and type once, without loading peak data (fast).
        reader.set_signal_loading(false);
        let mut times = Vec::with_capacity(reader.len());
        let mut scan_types = Vec::with_capacity(reader.len());
        for index in 0..reader.len() {
            let scan = reader
                .get(index)
                .ok_or_else(|| io::Error::other(format!("could not read scan {}", index + 1)))?;
            times.push(scan.time());
            scan_types.push(scan.filter_string().unwrap_or("").to_string());
        }
        reader.set_signal_loading(true);

        if times.is_empty() {
            return Err(io::Error::other("file contains no mass spectra"));
        }
        Ok(Self {
            first_scan: 1,
            last_scan: times.len(),
            reader,
            instrument_model,
            times,
            scan_types,
        })
    }

    /// Total ion chromatogram as (retention times in minutes, intensities).
    pub fn tic(&self) -> (Vec<f64>, Vec<f64>) {
        let description = self.reader.tic();
        match description.data() {
            Some(data) => (
                data.time().iter().map(|&t| t as f64).collect(),
                data.intensity().iter().map(|&v| v as f64).collect(),
            ),
            None => (Vec::new(), Vec::new()),
        }
    }

    pub fn retention_time(&self, scan: usize) -> f64 {
        self.times[scan - self.first_scan]
    }

    pub fn scan_type(&self, scan: usize) -> &str {
        &self.scan_types[scan - self.first_scan]
    }

    /// Scan number closest to a retention time (minutes).
    pub fn scan_number_at(&self, retention_time: f64) -> usize {
        let i = self.times.partition_point(|&t| t < retention_time);
        let best = if i == 0 {
            0
        } else if i >= self.times.len() {
            self.times.len() - 1
        } else if retention_time - self.times[i - 1] <= self.times[i] - retention_time {
            i - 1
        } else {
            i
        };
        best + self.first_scan
    }

    /// The scan `step` (+1 or -1) away among scans of the same type, or `scan`
    /// itself if there is none (so 3-phase methods don't jump between types).
    pub fn step_scan(&self, scan: usize, step: i64) -> usize {
        let wanted = self.scan_type(scan);
        let mut s = scan as i64 + step;
        while s >= self.first_scan as i64 && s <= self.last_scan as i64 {
            if self.scan_type(s as usize) == wanted {
                return s as usize;
            }
            s += step;
        }
        scan
    }

    /// Nearest scan at or before `scan` with the same type as `like_scan`
    /// (never earlier than `like_scan`).
    pub fn snap_to_type(&self, mut scan: usize, like_scan: usize) -> usize {
        while scan > like_scan && self.scan_type(scan) != self.scan_type(like_scan) {
            scan -= 1;
        }
        scan
    }

    /// One scan as (m/z, intensity), profile points if recorded in profile.
    pub fn spectrum(&self, scan: usize) -> (Vec<f64>, Vec<f64>) {
        match self.reader.get_spectrum_data(scan - self.first_scan, false) {
            Some(data) => (
                data.mz().to_vec(),
                data.intensity().iter().map(|&v| v as f64).collect(),
            ),
            None => (Vec::new(), Vec::new()),
        }
    }

    /// Average scans first..=last that have the same type as `first`.
    ///
    /// Our own averaging (Thermo's isn't available from Rust): every scan is
    /// linearly interpolated onto the m/z points of the scan with the most
    /// points, then the intensities are averaged. Where a scan has a gap in its
    /// profile data (no points recorded), it contributes zero there rather than
    /// a line drawn across the gap. Thermo's algorithm is smarter about the
    /// m/z grid, so peak shapes can differ slightly from the Python version.
    pub fn average(&self, first: usize, last: usize) -> (Vec<f64>, Vec<f64>) {
        let wanted = self.scan_type(first);
        let spectra: Vec<(Vec<f64>, Vec<f64>)> = (first..=last)
            .filter(|&s| self.scan_type(s) == wanted)
            .map(|s| self.spectrum(s))
            .filter(|(mz, _)| mz.len() >= 2)
            .collect();
        let Some(grid) = spectra.iter().map(|(mz, _)| mz).max_by_key(|mz| mz.len()).cloned() else {
            return (Vec::new(), Vec::new());
        };

        let mut sum = vec![0.0; grid.len()];
        for (mz, intensity) in &spectra {
            add_interpolated(&grid, mz, intensity, &mut sum);
        }
        let n = spectra.len() as f64;
        (grid, sum.into_iter().map(|v| v / n).collect())
    }
}

/// Add `intensity` (sampled at `mz`) onto `sum` (sampled at `grid`), using
/// linear interpolation. Both m/z arrays must be sorted.
fn add_interpolated(grid: &[f64], mz: &[f64], intensity: &[f64], sum: &mut [f64]) {
    let mut j = 0; // mz[j] <= x < mz[j + 1]
    for (i, &x) in grid.iter().enumerate() {
        if x < mz[0] || x > mz[mz.len() - 1] {
            continue;
        }
        while j + 1 < mz.len() - 1 && mz[j + 1] < x {
            j += 1;
        }
        let (x0, x1) = (mz[j], mz[j + 1]);
        // Typical spacing of the grid here; a source gap much wider than that is a
        // region with no recorded signal, not something to interpolate across.
        let local = if i + 1 < grid.len() { grid[i + 1] - x } else { x - grid[i.saturating_sub(1)] };
        if x1 - x0 > 4.0 * local.max(f64::EPSILON) {
            continue;
        }
        let t = if x1 > x0 { (x - x0) / (x1 - x0) } else { 0.0 };
        sum[i] += intensity[j] + t * (intensity[j + 1] - intensity[j]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NIST: &str = "../../Raw Files/NISTmAB_QE_C_3_phase_200ng_on_column_1.raw";

    #[test]
    fn reads_the_nist_file_like_python() {
        let raw = RawFile::open(Path::new(NIST)).expect("open");
        assert_eq!(raw.last_scan, 1163);
        let (rt, tic) = raw.tic();
        assert_eq!(rt.len(), 1163);
        println!("model {} | TIC max {:.3e} | RT {:.2}-{:.2}", raw.instrument_model,
                 tic.iter().cloned().fold(0.0, f64::max), rt[0], rt[rt.len() - 1]);

        let scan = raw.scan_number_at(7.5);
        println!("scan at 7.5 min = {scan}: {}", raw.scan_type(scan));
        assert_eq!(raw.step_scan(scan, 1), scan + 3); // 3 interleaved scan types

        let first = raw.scan_number_at(8.0);
        let last = raw.snap_to_type(raw.scan_number_at(8.5), first);
        let t = std::time::Instant::now();
        let (mz, intensity) = raw.average(first, last);
        println!("average {first}-{last}: {} points, max {:.0}, {:?}", mz.len(),
                 intensity.iter().cloned().fold(0.0, f64::max), t.elapsed());
        assert!(mz.len() > 1000);
    }

    #[test]
    fn opens_every_raw_file_in_the_data_folder() {
        let Ok(dir) = std::fs::read_dir("../../Raw Files") else { return };
        let mut failures = Vec::new();
        for entry in dir.flatten() {
            let path = entry.path();
            if path.extension().is_some_and(|e| e == "raw") {
                let t = std::time::Instant::now();
                match RawFile::open(&path) {
                    Ok(raw) => println!("ok   {:>5} scans  {:>5.1}s  {}", raw.last_scan, t.elapsed().as_secs_f32(),
                                        path.file_name().unwrap().to_string_lossy()),
                    Err(e) => {
                        println!("FAIL {e}  {}", path.display());
                        failures.push(path);
                    }
                }
            }
        }
        assert!(failures.is_empty(), "{} files failed to open", failures.len());
    }
}
