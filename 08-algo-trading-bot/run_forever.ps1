# run_forever.ps1 - keeps jevbot running 24/7 on this PC.
#
#   Start:  right-click > Run with PowerShell   (or: powershell -ExecutionPolicy Bypass -File run_forever.ps1)
#   Stop:   create an empty file named STOP_WATCHDOG in this folder, or close the window
#
# - Restarts the bot if it ever exits, waiting longer after repeated fast crashes.
# - Keeps the PC awake ONLY while this window is open (the same API video players use).
#   It does not change any Windows power setting; closing the window ends the request.
# - Logs to logs\watchdog.log and logs\bot-YYYY-MM-DD.log.

$ErrorActionPreference = "Continue"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Dir "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$WatchLog = Join-Path $Logs "watchdog.log"
$StopFile = Join-Path $Dir "STOP_WATCHDOG"

Add-Type -Namespace Win32 -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: system stays awake, the screen may still turn off
[void][Win32.Power]::SetThreadExecutionState([uint32]"0x80000001")

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $WatchLog -Value $line
}

if (Test-Path $StopFile) { Remove-Item $StopFile }
Log "watchdog started in $Dir (dashboard: http://127.0.0.1:8766)"
$fastCrashes = 0

try {
    while (-not (Test-Path $StopFile)) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $out = Join-Path $Logs "bot-$stamp.log"
        $err = Join-Path $Logs "bot-$stamp.err.log"
        $started = Get-Date
        Log "starting python -m jevbot"
        $p = Start-Process -FilePath "python" -ArgumentList "-u", "-m", "jevbot" -WorkingDirectory $Dir `
             -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru
        $null = $p.Handle   # PowerShell only records ExitCode if the handle is opened while the process lives
        while (-not $p.HasExited) {
            if (Test-Path $StopFile) { Log "STOP_WATCHDOG found: stopping bot"; Stop-Process -Id $p.Id; break }
            Start-Sleep -Seconds 2
        }
        $p.WaitForExit()
        $ran = [int]((Get-Date) - $started).TotalSeconds
        Log "bot exited with code $($p.ExitCode) after $ran s"
        if (Test-Path $StopFile) { break }
        if ($p.ExitCode -eq 2) { Log "config error (exit 2): fix .env, then restart this script"; break }
        if ($ran -lt 60) { $fastCrashes++ } else { $fastCrashes = 0 }
        $wait = [Math]::Min(300, 5 * [Math]::Pow(2, [Math]::Min($fastCrashes, 6)))
        Log "restarting in $wait s"
        Start-Sleep -Seconds $wait
    }
}
finally {
    [void][Win32.Power]::SetThreadExecutionState([uint32]"0x80000000")   # release the stay-awake request
    Log "watchdog stopped"
}
