# Install KC VENDETTA BOUNCE globally for Claude Code on Windows.
#
#   powershell -ExecutionPolicy Bypass -File kash-crown\install.ps1
#   powershell -ExecutionPolicy Bypass -File kash-crown\install.ps1 -Vault "C:\Users\me\Documents\MyVault"
#
# Installs to %USERPROFILE%\.claude\skills\kc-vendetta-bounce.
# A previous install is moved to %USERPROFILE%\.claude\skill-backups\ so it never loads twice.
param([string]$Vault = $env:OBSIDIAN_VAULT)
$ErrorActionPreference = 'Stop'

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src       = Join-Path $Here '..\.claude\skills\kc-vendetta-bounce'
$SkillsDir = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } else { Join-Path $HOME '.claude\skills' }
$Dest      = Join-Path $SkillsDir 'kc-vendetta-bounce'
$Backups   = Join-Path $HOME '.claude\skill-backups'

if (-not (Test-Path (Join-Path $Src 'SKILL.md'))) { throw "Skill source not found at $Src" }

if (Test-Path $Dest) {
    New-Item -ItemType Directory -Force -Path $Backups | Out-Null
    $bak = Join-Path $Backups ("kc-vendetta-bounce-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Move-Item $Dest $bak
    Write-Host "Previous install backed up -> $bak"
}
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
Copy-Item -Recurse $Src $Dest
Get-ChildItem $Dest -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Write-Host "Skill installed -> $Dest"

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Warning 'Python 3 not found. Install it from python.org, then: py -m pip install numpy pillow imageio-ffmpeg'
} else {
    & $py.Source -c "import numpy, PIL, imageio_ffmpeg" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $py.Source -m pip install --user --quiet numpy pillow imageio-ffmpeg
        if ($LASTEXITCODE -ne 0) { Write-Warning "pip install failed. Run: $($py.Source) -m pip install numpy pillow imageio-ffmpeg" }
    }
    & $py.Source (Join-Path $Dest 'scripts\render.py') --help *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host 'Renderer check OK' } else { Write-Warning 'Renderer self-check failed; see warning above' }
}

if ($Vault) {
    if (-not (Test-Path $Vault)) { throw "Obsidian vault not found: $Vault" }
    $NoteDir = Join-Path $Vault 'Kash Crown\Skills'
    New-Item -ItemType Directory -Force -Path $NoteDir | Out-Null
    Copy-Item (Join-Path $Here 'obsidian\KC Vendetta Bounce.md'), (Join-Path $Here 'obsidian\vendetta-soul-reference.png') $NoteDir -Force
    Write-Host "Obsidian note -> $NoteDir\KC Vendetta Bounce.md"
}

Write-Host ''
Write-Host 'Done. In any Claude Code session: upload a song and say "Use KC VENDETTA BOUNCE for this".'
