# Run ONCE in an elevated PowerShell to make the task survive being logged off.
# Registering an S4U principal requires admin; everything else is already configured.
$name = "ICT_Kullamagi_Scan"
$t = Get-ScheduledTask -TaskName $name
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
Set-ScheduledTask -TaskName $name -Principal $p -Action $t.Actions -Trigger $t.Triggers -Settings $t.Settings
"logon type is now: $((Get-ScheduledTask -TaskName $name).Principal.LogonType)"
