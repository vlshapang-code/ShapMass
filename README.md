# ShapMass
real software for real analyst

## Layout

```
mac/        macOS version
windows/    Windows version (Windows-specific input, menus and paths)
            Both have the same layout:
  main.py, main.rs     entry points
  python/              the other .py files, requirements.txt
  rust/                the other .rs files, Cargo.toml (Windows also has build.rs)
vendor/thermo/lib/   Thermo RawFileReader .NET 8 DLLs (not in the repo, shared by both)
vendor/nethost/      libnethost.lib for the Windows Rust build (MIT, from NuGet)
Raw Files/           instrument data (not in the repo, shared by both)
```

Both platforms need the .NET 8 runtime and the Thermo DLLs from
https://github.com/thermofisherlsms/RawFileReader (Libs/NetCore/Net8) in `vendor/thermo/lib`.

## Run

macOS (from the repo root):

```
.venv/bin/python mac/main.py
cd mac/rust && cargo run --release
```

Windows (from the repo root):

```
.venv\Scripts\python windows\main.py
cd windows\rust; cargo run --release
```

The Windows Rust build doesn't need Visual Studio. `windows\rust\Cargo.toml` turns off
thermorawfilereader's `nethost-download` feature (it needs a C compiler) and `build.rs` links
`vendor\nethost\win-x64\libnethost.lib` instead (from the MIT-licensed NuGet package
`Microsoft.NETCore.App.Host.win-x64` 8.0.x). Without the Visual Studio C++ tools, use
[xwin](https://github.com/Jake-Shadle/xwin) to fetch the MSVC CRT and Windows SDK libraries
and point `rust-lld` at them in `%USERPROFILE%\.cargo\config.toml`:

```
xwin --accept-license --arch x86_64 --variant desktop splat --output %USERPROFILE%\xwin-sdk --disable-symlinks --use-winsysroot-style
```

```toml
[target.x86_64-pc-windows-msvc]
linker = "rust-lld"
rustflags = [
    "-Lnative=C:/Users/<you>/xwin-sdk/VC/Tools/MSVC/<version>/lib/x86_64",
    "-Lnative=C:/Users/<you>/xwin-sdk/Windows Kits/10/Lib/<version>/um/x86_64",
    "-Lnative=C:/Users/<you>/xwin-sdk/Windows Kits/10/Lib/<version>/ucrt/x86_64",
    "-Clink-arg=/IGNORE:4099",
]
```
