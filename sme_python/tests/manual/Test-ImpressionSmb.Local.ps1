#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProbeScript,[Parameter(Mandatory=$true)][string]$OutputRoot)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProbeScript=(Resolve-Path -LiteralPath $ProbeScript).Path
$OutputRoot=[IO.Path]::GetFullPath($OutputRoot)
if ([IO.Directory]::Exists($OutputRoot)) { throw 'Self-test output directory must be new.' }
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$temporary=[IO.Path]::Combine($OutputRoot,'temp')
[IO.Directory]::CreateDirectory($temporary) | Out-Null
$engine=[IO.Path]::Combine($env:SystemRoot,'System32\WindowsPowerShell\v1.0\powershell.exe')
$processes=[Collections.Generic.List[Diagnostics.Process]]::new()
function Quote-Literal([string]$Value) { return "'"+$Value.Replace("'","''")+"'" }
function Start-Probe([string]$Arguments,[string]$Label) {
    $command="& "+(Quote-Literal $ProbeScript)+' '+$Arguments
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$engine
    # Process-only policy; no machine/user execution policy is modified.
    $info.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $info.EnvironmentVariables['TEMP']=$temporary; $info.EnvironmentVariables['TMP']=$temporary
    $process=[Diagnostics.Process]::Start($info)
    $processes.Add($process)
    return $process
}
function Finish-Probe($Process,[int]$ExpectedExit,[string]$Label) {
    if (-not $Process.WaitForExit(20000)) { throw ('Owned probe timed out: '+$Label) }
    $stdout=$Process.StandardOutput.ReadToEnd(); $stderr=$Process.StandardError.ReadToEnd()
    [IO.File]::WriteAllText((Join-Path $OutputRoot ($Label+'.stdout.txt')),$stdout)
    $diagnostic=[regex]::Match($stderr,'Validation incomplete:[^\r\n<]*').Value
    [IO.File]::WriteAllText((Join-Path $OutputRoot ($Label+'.stderr.txt')),$diagnostic)
    if ($Process.ExitCode -ne $ExpectedExit) { throw ('Unexpected exit for '+$Label+': '+$Process.ExitCode+'; '+$diagnostic) }
    if ($ExpectedExit -eq 1 -and $stdout -notmatch 'VALIDATION_INCOMPLETE') { throw ('No actual validation result for '+$Label) }
}
function Wait-Session([string]$Folder) {
    $watch=[Diagnostics.Stopwatch]::StartNew()
    do {
        $session=Get-ChildItem -LiteralPath $Folder -Directory -Filter 'impression_smb_validation_*' | Select-Object -First 1
        if ($null -ne $session -and [IO.File]::Exists((Join-Path $session.FullName 'a.locked.json'))) { return $session.FullName }
        Start-Sleep -Milliseconds 40
    } while ($watch.Elapsed.TotalSeconds -lt 12)
    throw 'No owned test session became ready.'
}
try {
    $tokens=$null; $parseErrors=$null
    [Management.Automation.Language.Parser]::ParseFile($ProbeScript,[ref]$tokens,[ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -ne 0) { throw 'PowerShell parser errors.' }
    $refused=Join-Path $OutputRoot 'local-refused'; [IO.Directory]::CreateDirectory($refused) | Out-Null
    $process=Start-Probe ('-Role Start -Path '+(Quote-Literal $refused)) 'reject-local'
    Finish-Probe $process 1 'reject-local'
    if (@(Get-ChildItem -LiteralPath $refused -Force).Count -ne 0) { throw 'Refused local selection created files.' }

    $bad=Join-Path $OutputRoot 'impression_smb_validation_11111111111111111111111111111111'
    [IO.Directory]::CreateDirectory($bad) | Out-Null
    [IO.File]::WriteAllText((Join-Path $bad 'manifest.json'),'{"Tool":"Impression.SmbValidation","Version":1,"RunId":"22222222222222222222222222222222","Nonce":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","SelfTest":true}')
    $process=Start-Probe ('-Role Join -AllowLocalSelfTest -SessionFolder '+(Quote-Literal $bad)) 'reject-wrong-session'
    Finish-Probe $process 1 'reject-wrong-session'
    if (@(Get-ChildItem -LiteralPath $bad -Force).Count -ne 1) { throw 'Invalid session was modified.' }

    $exchange=Join-Path $OutputRoot "peer exchange's folder"; [IO.Directory]::CreateDirectory($exchange) | Out-Null
    $a=Start-Probe ('-Role Start -AllowLocalSelfTest -TimeoutSeconds 12 -Path '+(Quote-Literal $exchange)) 'A'
    $session=Wait-Session $exchange
    $b=Start-Probe ('-Role Join -AllowLocalSelfTest -TimeoutSeconds 12 -SessionFolder '+(Quote-Literal $session)) 'B'
    Finish-Probe $a 0 'A'; Finish-Probe $b 0 'B'
    $result=Get-Content -LiteralPath (Join-Path $session 'a.result.json') -Raw | ConvertFrom-Json
    if ($result.Status -ne 'LOCAL_REHEARSAL_PASSED' -or $result.SmbFileChecksValidated -or $result.UncFileChecksValidated -or $result.MySQLApplicationSyncValidated) { throw 'Local rehearsal must never claim real SMB/application validation.' }
    if ($result.AtomicPublications -ne 50 -or $result.FinalSequence -ne 50 -or -not $result.MutualExclusionBothDirections -or -not $result.LockReleased -or -not $result.CrossParticipantReplacementBothDirections -or $result.CompletePeerReads -lt 1) { throw 'Local peer checks incomplete.' }
    if (@(Get-ChildItem -LiteralPath $session -Filter '*.part').Count -ne 0) { throw 'Temporary files left by successful run.' }
    $report=Start-Probe ('-Role Report -AllowLocalSelfTest -SessionFolder '+(Quote-Literal $session)) 'report'
    Finish-Probe $report 0 'report'

    $timeout=Join-Path $OutputRoot 'timeout'; [IO.Directory]::CreateDirectory($timeout) | Out-Null
    $process=Start-Probe ('-Role Start -AllowLocalSelfTest -TimeoutSeconds 1 -Path '+(Quote-Literal $timeout)) 'timeout'
    Finish-Probe $process 1 'timeout'
    $timedSession=Get-ChildItem -LiteralPath $timeout -Directory -Filter 'impression_smb_validation_*' | Select-Object -First 1
    $gate=[IO.File]::Open((Join-Path $timedSession.FullName 'gate.lock'),[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    try { $gate.Lock(0,1); $gate.Unlock(0,1) } finally { $gate.Dispose() }
    $summary=[ordered]@{ parser_errors=0; local_paths_refused_without_self_test=$true;
        tampered_session_refused_without_write=$true; local_peer_processes=2;
        mutual_exclusion_both_directions=$true; publications=50; complete_peer_reads=$result.CompletePeerReads;
        cross_participant_replacement=$true; observed_rename_retries=($result.RenameRetriesA+$result.RenameRetriesB); observed_read_retries=$result.ReadRetriesB;
        timeout_releases_lock=$true; powershell_engine='Windows PowerShell 5.1';
        real_smb_tested=$false; two_workstations_tested=$false; mysql_application_sync_tested=$false;
        executable_generated=$false; output_root=$OutputRoot }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputRoot 'validation.json') -Encoding UTF8
    $summary | ConvertTo-Json -Depth 6
} finally {
    foreach ($ownedProcess in $processes) {
        if (-not $ownedProcess.HasExited) { $ownedProcess.Kill(); $ownedProcess.WaitForExit() }
        $ownedProcess.Dispose()
    }
}
