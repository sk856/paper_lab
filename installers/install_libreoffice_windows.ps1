param(
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

function Find-Soffice {
  $candidates = @()
  $candidates += $env:SOFFICE_PATH
  $candidates += (Join-Path $PSScriptRoot "..\tools\LibreOffice\program\soffice.exe")
  $candidates += (Join-Path $PSScriptRoot "..\tools\LibreOfficePortable\App\libreoffice\program\soffice.exe")
  $candidates += @(
    "C:\Program Files\LibreOffice\program\soffice.exe",
    "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
  )
  $candidates = @($candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

  $command = Get-Command soffice -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  if ($candidates.Count -gt 0) { return (Resolve-Path -LiteralPath $candidates[0]).Path }
  return ""
}

$existing = Find-Soffice
if ($existing) {
  Write-Host "[LibreOffice] Found: $existing"
  exit 0
}

if ($CheckOnly) {
  Write-Host "[LibreOffice] Not found"
  exit 1
}

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
  Write-Host "[LibreOffice] winget not found. Please install LibreOffice manually:"
  Write-Host "https://www.libreoffice.org/download/download-libreoffice/"
  exit 1
}

Write-Host "[LibreOffice] Installing via winget..."
winget install --id TheDocumentFoundation.LibreOffice --source winget --accept-package-agreements --accept-source-agreements

$installed = Find-Soffice
if (-not $installed) {
  Write-Host "[LibreOffice] Install finished, but soffice.exe was not found. Restart the terminal or install manually."
  exit 1
}

Write-Host "[LibreOffice] Installed: $installed"
