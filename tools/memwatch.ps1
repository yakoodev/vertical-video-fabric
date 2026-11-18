# Пик памяти дерева процессов (корень по PID и все его потомки): рабочий набор и выделенная (commit) память, раз в
# секунду, пока жив корень; плюс минимум свободной commit-памяти системы. Итог — JSON в -Out.
#   powershell -NoProfile -File tools/memwatch.ps1 -Root <pid> -Out mem.json [-Stop stop.flag — появился файл: итог и выход]
param([int]$Root, [string]$Out, [double]$Every = 1.0, [string]$Stop = "")
$peakWs = 0; $peakPriv = 0; $minFreeCommit = [double]::MaxValue; $samples = 0; $byName = @{}; $nameMax = @{}
while ((Get-Process -Id $Root -ErrorAction SilentlyContinue) -and -not ($Stop -and (Test-Path $Stop))) {
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, WorkingSetSize, PrivatePageCount
    $kids = @{}; foreach ($p in $all) { if (-not $kids.ContainsKey([int]$p.ParentProcessId)) { $kids[[int]$p.ParentProcessId] = @() }; $kids[[int]$p.ParentProcessId] += $p }
    $stack = New-Object System.Collections.Stack; $stack.Push($Root); $ws = 0; $priv = 0; $names = @{}
    $seen = @{}
    while ($stack.Count) {
        $id = $stack.Pop(); if ($seen.ContainsKey($id) -or $id -eq $PID) { continue }; $seen[$id] = 1   # себя не считаем
        $me = $all | Where-Object { $_.ProcessId -eq $id } | Select-Object -First 1
        if ($me) { $ws += $me.WorkingSetSize; $priv += $me.PrivatePageCount; $names[$me.Name] = ($names[$me.Name] + $me.PrivatePageCount) }
        if ($kids.ContainsKey($id)) { foreach ($c in $kids[$id]) { $stack.Push([int]$c.ProcessId) } }
    }
    foreach ($k in $names.Keys) { if (-not $nameMax.ContainsKey($k) -or $names[$k] -gt $nameMax[$k]) { $nameMax[$k] = $names[$k] } }
    if ($ws -gt $peakWs) { $peakWs = $ws }
    if ($priv -gt $peakPriv) { $peakPriv = $priv; $byName = $names }
    $os = Get-CimInstance Win32_OperatingSystem -Property FreeVirtualMemory
    $fc = [double]$os.FreeVirtualMemory * 1024; if ($fc -lt $minFreeCommit) { $minFreeCommit = $fc }
    $samples++
    Start-Sleep -Milliseconds ([int]($Every * 1000))
}
$res = @{ samples = $samples; peak_working_set_mb = [math]::Round($peakWs / 1MB); peak_commit_mb = [math]::Round($peakPriv / 1MB);
          min_free_commit_mb = [math]::Round($minFreeCommit / 1MB); peak_commit_by_name_mb = @{}; max_commit_by_name_mb = @{} }
foreach ($k in $byName.Keys) { $res.peak_commit_by_name_mb[$k] = [math]::Round($byName[$k] / 1MB) }
foreach ($k in $nameMax.Keys) { $res.max_commit_by_name_mb[$k] = [math]::Round($nameMax[$k] / 1MB) }
$res | ConvertTo-Json -Depth 3 | Set-Content -Path $Out -Encoding UTF8
