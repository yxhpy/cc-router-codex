param(
    [string]$Target = ".",
    [string]$Repo = "yxhpy/cc-router-codex",
    [string]$Ref = "main",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

function Find-Python {
    $candidates = @("python", "py", "python3")
    foreach ($name in $candidates) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $cmd) {
            return $cmd.Source
        }
    }
    throw "Python was not found on PATH. Install Python 3.11+ and rerun this installer."
}

$targetPath = (Resolve-Path -LiteralPath $Target -ErrorAction SilentlyContinue)
if ($null -eq $targetPath) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    $targetPath = Resolve-Path -LiteralPath $Target
}

$python = Find-Python
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-router-codex-" + [System.Guid]::NewGuid().ToString("N"))
$zipPath = Join-Path $tempRoot "source.zip"
$extractPath = Join-Path $tempRoot "extract"
$archiveUrl = "https://github.com/$Repo/archive/refs/heads/$Ref.zip"

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    Write-Host "Downloading $archiveUrl"
    Invoke-WebRequest -UseBasicParsing -Uri $archiveUrl -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force
    $sourceDir = Get-ChildItem -LiteralPath $extractPath -Directory | Select-Object -First 1
    if ($null -eq $sourceDir) {
        throw "Unable to locate extracted repository directory."
    }

    $installArgs = @((Join-Path $sourceDir.FullName "install.py"), "--target", $targetPath.Path)
    if ($Yes) {
        $installArgs += "-y"
    }

    & $python @installArgs
    if ($LASTEXITCODE -ne 0) {
        throw "install.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
