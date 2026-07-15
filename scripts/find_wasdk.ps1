Get-AppxPackage *WindowsAppRuntime* | ForEach-Object {
    Write-Host "PKG $($_.Name) $($_.Version)"
    Write-Host "  $($_.InstallLocation)"
    if (Test-Path $_.InstallLocation) {
        Get-ChildItem $_.InstallLocation -Filter *.dll | Where-Object {
            $_.Name -match 'Boot|Deploy|Runtime|AI|Imaging|Graphics'
        } | ForEach-Object { Write-Host "  DLL $($_.Name)" }
    }
}
Write-Host "---"
$boot = Get-ChildItem -Path "$env:USERPROFILE\.nuget\packages" -Recurse -Filter "*Bootstrap*" -ErrorAction SilentlyContinue |
    Select-Object -First 20 FullName
$boot | ForEach-Object { Write-Host $_.FullName }
