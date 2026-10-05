$ErrorActionPreference = "Continue"
$taskName = "SquashRMMControlPlaneStartup"
$outputPath = Join-Path $env:TEMP "squash-rmm-startup-task-diagnostic.txt"
$lines = New-Object System.Collections.Generic.List[string]
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
    $lines.Add("TaskState=$($task.State)")
    $lines.Add("LastTaskResult=$($taskInfo.LastTaskResult)")
    $lines.Add("LastRunTime=$($taskInfo.LastRunTime.ToString('u'))")
    $lines.Add("NextRunTime=$($taskInfo.NextRunTime.ToString('u'))")
    $lines.Add("TaskUser=$($task.Principal.UserId)")
    foreach ($action in $task.Actions) {
        $lines.Add("ActionExecute=$($action.Execute)")
        $lines.Add("ActionArguments=$($action.Arguments)")
        $lines.Add("ActionWorkingDirectory=$($action.WorkingDirectory)")
    }
} else {
    $lines.Add("TaskState=NOT_FOUND")
}
$service = Get-Service -Name "SquashRMMControlPlane" -ErrorAction SilentlyContinue
if ($service) {
    $lines.Add("LegacyServiceStatus=$($service.Status)")
    $lines.Add("LegacyServiceStartType=$($service.StartType)")
}
foreach ($connection in (Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue)) {
    $lines.Add("Listener=$($connection.LocalAddress):$($connection.LocalPort) pid=$($connection.OwningProcess)")
}
foreach ($process in (Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue)) {
    $lines.Add("Process=$($process.ProcessId) $($process.CommandLine)")
}
$lines | Set-Content -LiteralPath $outputPath -Encoding UTF8
