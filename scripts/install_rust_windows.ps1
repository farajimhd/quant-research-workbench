param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Test-Command($Name) {
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if ((Test-Command "cargo") -and -not $Force) {
    Write-Host "Rust/Cargo already available:"
    cargo --version
    rustc --version
    Write-Host "Use -Force to reinstall/update through rustup."
    exit 0
}

$installer = Join-Path $env:TEMP "rustup-init.exe"
$url = "https://win.rustup.rs/x86_64"

Write-Host "Downloading official rustup installer from $url"
Invoke-WebRequest -Uri $url -OutFile $installer

Write-Host "Installing stable Rust toolchain. This installs rustup, rustc, and cargo."
Write-Host "If prompted for Visual Studio C++ Build Tools, accept the default MSVC toolchain setup."
& $installer -y --default-toolchain stable

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if ($env:PATH -notlike "*$cargoBin*") {
    $env:PATH = "$cargoBin;$env:PATH"
}

Write-Host "Installing standard Rust components."
rustup component add rustfmt clippy

Write-Host ""
Write-Host "Rust installation complete. Open a new PowerShell window, then verify:"
Write-Host "  rustc --version"
Write-Host "  cargo --version"
Write-Host ""
Write-Host "For this repo:"
Write-Host "  cargo check --manifest-path services\qmd-gateway\Cargo.toml"
