# ══════════════════════════════════════════════════════════════
#  Register Nginx / go2rtc as Windows services via NSSM
#
#  NOTE ON LANGUAGE
#  This script intentionally uses ASCII (English) only.
#  Windows PowerShell 5.1 reads .ps1 files as ANSI (GBK on CN systems)
#  unless they carry a UTF-8 BOM, so non-ASCII comments would be garbled
#  or could break parsing. Chinese documentation lives in
#  deploy/README.md and docs/03-版本部署-Windows版.md.
#
#  PREREQUISITES
#  1. NSSM extracted to C:\tools\nssm\win64
#     https://nssm.cc/download
#  2. Nginx extracted to C:\nginx            (optional, for the nginx service)
#  3. go2rtc extracted to C:\go2rtc          (optional, for the go2rtc service)
#
#  USAGE (run as Administrator)
#    powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1
#    powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1 -Action status
#    powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1 -Action remove
# ══════════════════════════════════════════════════════════════

[CmdletBinding()]
param(
    [ValidateSet('install', 'start', 'stop', 'restart', 'status', 'remove')]
    [string]$Action = 'install',

    [string]$NssmPath   = 'C:\tools\nssm\win64\nssm.exe',
    [string]$NginxPath  = 'C:\nginx',
    [string]$Go2rtcPath = 'C:\go2rtc',

    # Max log size before rotation (bytes). 10 MB.
    [int]$LogRotateBytes = 10485760
)

$ErrorActionPreference = 'Stop'

$Services = @(
    @{ Name = 'eep-nginx';  Exe = Join-Path $NginxPath  'nginx.exe'; Dir = $NginxPath;  ConsoleStopMs = 15000 },
    @{ Name = 'eep-go2rtc'; Exe = Join-Path $Go2rtcPath 'go2rtc.exe'; Dir = $Go2rtcPath; ConsoleStopMs = 8000 }
)

function Assert-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'This script must be run as Administrator.'
    }
}

function Assert-Nssm {
    if (-not (Test-Path $NssmPath)) {
        throw "nssm.exe not found at $NssmPath. Download it from https://nssm.cc/download and extract to C:\tools\nssm\win64"
    }
}

function Get-ServiceState([string]$Name) {
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $svc) { return 'not-installed' }
    return $svc.Status.ToString().ToLower()
}

function Install-EepService($Svc) {
    $name = $Svc.Name

    if (-not (Test-Path $Svc.Exe)) {
        Write-Warning "SKIP $name : executable not found at $($Svc.Exe)"
        return
    }

    $existing = Get-ServiceState $name
    if ($existing -ne 'not-installed') {
        Write-Host "SKIP $name : service already exists (state=$existing). Use -Action remove first to reinstall." -ForegroundColor Yellow
        return
    }

    Write-Host "Installing service $name ..." -ForegroundColor Cyan

    & $NssmPath install $name $Svc.Exe | Out-Null
    & $NssmPath set $name AppDirectory $Svc.Dir | Out-Null
    & $NssmPath set $name DisplayName "$name (energy-efficiency-platform)" | Out-Null
    & $NssmPath set $name Description "EEP - $name managed by NSSM" | Out-Null
    & $NssmPath set $name Start SERVICE_AUTO_START | Out-Null

    # Graceful stop: send console CTRL+C instead of hard kill,
    # so nginx can flush logs and go2rtc can release camera connections.
    & $NssmPath set $name AppStopMethodConsole $Svc.ConsoleStopMs | Out-Null

    # Auto restart on crash, 5 second delay.
    & $NssmPath set $name AppExit Default Restart | Out-Null
    & $NssmPath set $name AppRestartDelay 5000 | Out-Null

    # Log rotation: keep Nginx/go2rtc output under logs\ of the app directory.
    $logDir = Join-Path $Svc.Dir 'logs'
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    & $NssmPath set $name AppStdout (Join-Path $logDir "$name.out.log") | Out-Null
    & $NssmPath set $name AppStderr (Join-Path $logDir "$name.err.log") | Out-Null
    & $NssmPath set $name AppRotateFiles 1 | Out-Null
    & $NssmPath set $name AppRotateOnline 1 | Out-Null
    & $NssmPath set $name AppRotateBytes $LogRotateBytes | Out-Null

    & $NssmPath start $name | Out-Null
    Write-Host "OK $name installed and started (state=$(Get-ServiceState $name))" -ForegroundColor Green
}

function Invoke-ServiceAction([string]$Verb) {
    foreach ($svc in $Services) {
        $name = $svc.Name
        if ((Get-ServiceState $name) -eq 'not-installed') {
            Write-Warning "SKIP $name : not installed"
            continue
        }
        Write-Host "$Verb $name ..." -ForegroundColor Cyan
        & $NssmPath $Verb $name | Out-Null
        Start-Sleep -Milliseconds 600
        Write-Host "  state = $(Get-ServiceState $name)"
    }
}

function Show-Status {
    Write-Host ''
    Write-Host 'Service status' -ForegroundColor Cyan
    Write-Host '---------------'
    foreach ($svc in $Services) {
        $name  = $svc.Name
        $state = Get-ServiceState $name
        $color = if ($state -eq 'running') { 'Green' } elseif ($state -eq 'not-installed') { 'DarkGray' } else { 'Red' }
        Write-Host ("{0,-14} {1,-14} {2}" -f $name, $state, $svc.Exe) -ForegroundColor $color
    }

    Write-Host ''
    Write-Host 'HTTP probes' -ForegroundColor Cyan
    Write-Host '-----------'
    $targets = @(
        @{ Url = 'http://127.0.0.1/healthz';                     Desc = 'nginx healthz' },
        @{ Url = 'http://127.0.0.1/screen/';                     Desc = 'big screen' },
        @{ Url = 'http://127.0.0.1/admin/';                      Desc = 'admin home' },
        @{ Url = 'http://127.0.0.1/admin/system/user';           Desc = 'admin deep route (history fallback)' },
        @{ Url = 'http://127.0.0.1:1984/api/streams';            Desc = 'go2rtc stream list' }
    )
    foreach ($t in $targets) {
        try {
            $r = Invoke-WebRequest -Uri $t.Url -TimeoutSec 5 -UseBasicParsing
            Write-Host ("  {0,-42} HTTP {1}" -f $t.Desc, $r.StatusCode) -ForegroundColor Green
        }
        catch {
            $code = $null
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            $shown = if ($code) { $code } else { 'no response' }
            Write-Host ("  {0,-42} {1}" -f $t.Desc, $shown) -ForegroundColor Yellow
        }
    }
    Write-Host ''
}

function Remove-EepServices {
    foreach ($svc in $Services) {
        $name = $svc.Name
        if ((Get-ServiceState $name) -eq 'not-installed') {
            Write-Warning "SKIP $name : not installed"
            continue
        }
        Write-Host "Removing $name ..." -ForegroundColor Cyan
        & $NssmPath stop   $name | Out-Null
        & $NssmPath remove $name confirm | Out-Null
        Write-Host "  state = $(Get-ServiceState $name)"
    }
}

# ── main ────────────────────────────────────────────────────
Assert-Administrator

switch ($Action) {
    'install' {
        Assert-Nssm
        foreach ($svc in $Services) { Install-EepService $svc }
        Show-Status
    }
    'start'   { Assert-Nssm; Invoke-ServiceAction 'start' }
    'stop'    { Assert-Nssm; Invoke-ServiceAction 'stop' }
    'restart' { Assert-Nssm; Invoke-ServiceAction 'restart' }
    'status'  { Show-Status }
    'remove'  { Assert-Nssm; Remove-EepServices }
}
