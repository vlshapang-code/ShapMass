use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let nethost = manifest.join("../../vendor/nethost/win-x64");
    println!("cargo:rustc-link-search=native={}", nethost.display());
}
