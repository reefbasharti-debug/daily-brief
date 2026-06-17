# setup_enricher_task.ps1
# Creates Task Scheduler task: local_enricher.py runs at 9:05 AM Sun-Thu
# Run once as Administrator (right-click -> Run as administrator)

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = (Get-Command python -ErrorAction Stop).Source
$Script     = Join-Path $ScriptDir "local_enricher.py"
$LogFile    = Join-Path $ScriptDir "enricher_log.txt"

# Wrap in a cmd /c so we can redirect output to log
$CmdArgs = "/c python `"$Script`" >> `"$LogFile`" 2>&1"
$Action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $CmdArgs -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday `
    -At 09:05AM

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable $true `
    -RunOnlyIfNetworkAvailable $true

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Pushes IBKR + Calendar snapshot to GitHub repo before morning Telegram brief (9:15 AM)"

Register-ScheduledTask -TaskName "DailyBriefEnricher" -InputObject $Task -Force | Out-Null

Write-Host ""
Write-Host "Task 'DailyBriefEnricher' created successfully!"
Write-Host "   Schedule : 9:05 AM every Sun-Thu"
Write-Host "   Script   : $Script"
Write-Host "   Log      : $LogFile"
Write-Host ""
Write-Host "This task fetches IBKR + Calendar data and pushes enriched_data.json"
Write-Host "to the GitHub repo 10 minutes before the Telegram brief is sent."
