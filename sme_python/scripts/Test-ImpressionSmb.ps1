#requires -Version 5.1
<#
Impression: isolated SMB file validation, using the current Windows session.
Start: -Role Start -Path 'Z:\Photos'
Join:  -Role Join -SessionFolder '\\server\share\Photos\impression_smb_validation_<id>'
Report:-Role Report -SessionFolder <same folder>
No application configuration, SQL, credentials, drive mappings or services are changed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Start','Join','Report')][string]$Role='Start',
    [string]$Path,
    [string]$SessionFolder,
    [ValidateRange(1,600)][int]$TimeoutSeconds=180,
    [switch]$AllowLocalSelfTest
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:Root=$null; $script:Context=$null; $script:Claimed=$false
$script:Stage='preconditions'
$script:Utf8=[Text.UTF8Encoding]::new($false)
$script:RenameRetries=0; $script:ReadRetries=0; $script:RetryCodes=@{}

function Assert-True($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Leaf-Exception($Exception) {
    while ($null -ne $Exception.InnerException) { $Exception=$Exception.InnerException }
    return $Exception
}
function Text-Hash([string]$Text) {
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($script:Utf8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
function Node-Hash([string]$Nonce) { return Text-Hash ($Nonce+'|'+[Environment]::MachineName.ToLowerInvariant()) }
function Load-NativeApi {
    if (-not ('ImpressionSmbNativeV1' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ImpressionSmbNativeV1 {
    [DllImport("mpr.dll",CharSet=CharSet.Unicode)]
    public static extern int WNetGetUniversalName(string path,int level,IntPtr buffer,ref int size);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileEx(string source,string target,uint flags);
}
'@
    }
}
function Resolve-TestPath([string]$Value) {
    Assert-True (-not [string]::IsNullOrWhiteSpace($Value)) 'A folder path is required.'
    $item=Get-Item -LiteralPath $Value -Force -ErrorAction Stop
    Assert-True ($item -is [IO.DirectoryInfo]) 'Select an existing folder.'
    $full=[IO.Path]::GetFullPath($item.FullName)
    if ($full.StartsWith('\\?\UNC\',[StringComparison]::OrdinalIgnoreCase)) { $full='\\'+$full.Substring(8) }
    Assert-True (-not ($full.StartsWith('\\.\') -or $full.StartsWith('\\?\'))) 'Device paths are not allowed.'
    if (-not $full.StartsWith('\\')) {
        $size=65536; $buffer=[Runtime.InteropServices.Marshal]::AllocHGlobal($size)
        try {
            $code=[ImpressionSmbNativeV1]::WNetGetUniversalName($full,1,$buffer,[ref]$size)
            if ($code -eq 234) {
                Assert-True ($size -gt 0 -and $size -le 1048576) 'Invalid UNC buffer size.'
                [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer); $buffer=[IntPtr]::Zero
                $buffer=[Runtime.InteropServices.Marshal]::AllocHGlobal($size)
                $code=[ImpressionSmbNativeV1]::WNetGetUniversalName($full,1,$buffer,[ref]$size)
            }
            if ($code -eq 0) {
                $full=[Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::ReadIntPtr($buffer))
                Assert-True ($full.StartsWith('\\')) 'The mapped drive did not resolve to UNC.'
            } else { Assert-True $AllowLocalSelfTest.IsPresent 'Network drive unavailable. Use an accessible mapped drive or UNC path.' }
        } finally { if ($buffer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer) } }
    }
    $unc=$full.StartsWith('\\')
    Assert-True ($unc -or $AllowLocalSelfTest.IsPresent) 'A real UNC folder is required.'
    if ($unc) {
        $pieces=$full.Substring(2).TrimEnd('\').Split('\')
        Assert-True ($pieces.Count -ge 2 -and $pieces[0] -and $pieces[1]) 'Incomplete UNC path.'
    }
    $canonical=$full.TrimEnd('\')
    if ($full -match '^[A-Za-z]:\\$') { $canonical=$full }
    return [pscustomobject]@{ FullName=$canonical; IsUnc=$unc }
}
function Smb-Evidence([string]$Folder) {
    if (-not $Folder.StartsWith('\\')) { return @{ Verified=$false; Dialect=$null; State='NOT_UNC' } }
    try {
        $pieces=$Folder.Substring(2).Split('\')
        $connection=Get-SmbConnection -ErrorAction Stop | Where-Object {
            $_.ServerName -ieq $pieces[0] -and $_.ShareName -ieq $pieces[1]
        } | Select-Object -First 1
        if ($null -ne $connection) { return @{ Verified=$true; Dialect=[string]$connection.Dialect; State='OBSERVED' } }
    } catch { }
    return @{ Verified=$false; Dialect=$null; State='NOT_OBSERVABLE' }
}
function Test-File([string]$Name) {
    Assert-True ($Name -match '^(manifest\.json|payload\.json|gate\.lock|[ab]\.[a-z-]+\.json|tmp-[0-9a-f]{32}\.part)$') 'Invalid test filename.'
    Assert-True (([IO.File]::GetAttributes($script:Root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Session reparse points are refused.'
    $file=[IO.Path]::GetFullPath([IO.Path]::Combine($script:Root,$Name))
    Assert-True ($file.StartsWith($script:Root+'\',[StringComparison]::OrdinalIgnoreCase)) 'Write outside the test session refused.'
    if ([IO.File]::Exists($file)) {
        Assert-True (([IO.File]::GetAttributes($file) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Reparse point refused.'
    }
    return $file
}
function Put-Json([string]$Name,$Data,[bool]$Replace=$false) {
    $target=Test-File $Name
    $temporary=Test-File ('tmp-'+[Guid]::NewGuid().ToString('N')+'.part')
    $bytes=$script:Utf8.GetBytes(($Data | ConvertTo-Json -Depth 8 -Compress))
    Assert-True ($bytes.Length -le 262144) 'Test payload too large.'
    try {
        $output=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $output.Write($bytes,0,$bytes.Length); $output.Flush($true) } finally { $output.Dispose() }
        $flags=[uint32]0; if ($Replace) { $flags=[uint32]1 }
        $watch=[Diagnostics.Stopwatch]::StartNew()
        while (-not [ImpressionSmbNativeV1]::MoveFileEx($temporary,$target,$flags)) {
            $code=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if (-not $Replace -or $code -notin @(5,32,33) -or $watch.ElapsedMilliseconds -ge 500) {
                throw [ComponentModel.Win32Exception]::new($code,'Atomic publication failed.')
            }
            $script:RenameRetries++
            $label='rename_'+$code
            if (-not $script:RetryCodes.ContainsKey($label)) { $script:RetryCodes[$label]=0 }
            $script:RetryCodes[$label]++
            Start-Sleep -Milliseconds 10
        }
    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
}
function Get-Json([string]$Name) {
    $file=Test-File $Name
    $sharing=[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try { $readStream=[IO.File]::Open($file,[IO.FileMode]::Open,[IO.FileAccess]::Read,$sharing); break }
        catch {
            $leaf=Leaf-Exception $_.Exception; $code=$leaf.HResult -band 65535
            if ($Name -ne 'payload.json' -or $code -notin @(2,5,32,33) -or $watch.ElapsedMilliseconds -ge 500) { throw }
            $script:ReadRetries++
            $label='read_'+$code
            if (-not $script:RetryCodes.ContainsKey($label)) { $script:RetryCodes[$label]=0 }
            $script:RetryCodes[$label]++
            Start-Sleep -Milliseconds 10
        }
    }
    try {
        Assert-True ($readStream.Length -le 262144) 'Invalid control file size.'
        $reader=[IO.StreamReader]::new($readStream,$script:Utf8,$true)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $readStream.Dispose() }
}
function Signal([string]$Name,$Extra=@{}) {
    $message=@{ RunId=$script:Context.RunId; Nonce=$script:Context.Nonce }
    foreach ($key in $Extra.Keys) { $message[$key]=$Extra[$key] }
    Put-Json $Name $message
}
function Wait-Signal([string]$Name,[string]$Peer) {
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while (-not [IO.File]::Exists((Test-File $Name))) {
        if ([IO.File]::Exists((Test-File ($Peer+'.error.json')))) { throw 'The other participant reported an error. Start a new test session after review.' }
        Assert-True ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) ('Timed out waiting for '+$Name)
        Start-Sleep -Milliseconds 30
    }
    $value=Get-Json $Name
    Assert-True ($value.RunId -ceq $script:Context.RunId -and $value.Nonce -ceq $script:Context.Nonce) 'Invalid or stale peer message.'
    return $value
}
function Open-Gate {
    return [IO.File]::Open((Test-File 'gate.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
}
function Assert-LockDenied($Stream) {
    try { $Stream.Lock(0,1); $Stream.Unlock(0,1) }
    catch {
        $leaf=Leaf-Exception $_.Exception
        if (($leaf.HResult -band 65535) -eq 33) { return }
        throw
    }
    throw 'Mutual exclusion failed: the other participant lock was not enforced.'
}
function Payload([int]$Sequence) {
    return @{ Sequence=$Sequence; Content=([string][char](65+($Sequence%26)))*32768; RunId=$script:Context.RunId }
}
function Check-Payload($Value) {
    Assert-True ($Value.RunId -ceq $script:Context.RunId -and $Value.Sequence -ge 0 -and $Value.Sequence -le 50) 'Unexpected payload identity or sequence.'
    $expected=([string][char](65+([int]$Value.Sequence%26)))*32768
    Assert-True ($Value.Content -ceq $expected) 'A partial or corrupted payload was observed.'
}

try {
    Load-NativeApi
    if ($Role -eq 'Start') {
        $base=Resolve-TestPath $Path
        $id=[Guid]::NewGuid().ToString('N')
        $script:Root=[IO.Path]::Combine($base.FullName,'impression_smb_validation_'+$id)
        Assert-True (-not [IO.Directory]::Exists($script:Root)) 'Test directory already exists.'
        [IO.Directory]::CreateDirectory($script:Root) | Out-Null
        $script:Claimed=$true
        $nonce=[Guid]::NewGuid().ToString('N')
        $script:Context=[pscustomobject]@{ Tool='Impression.SmbValidation'; Version=1; RunId=$id; Nonce=$nonce;
            SelfTest=$AllowLocalSelfTest.IsPresent; IsUnc=$base.IsUnc; NodeHash=(Node-Hash $nonce);
            RootHash=(Text-Hash $base.FullName.TrimEnd('\').ToLowerInvariant()); StartedUtc=[DateTime]::UtcNow.ToString('o') }
        Put-Json 'manifest.json' $script:Context
        Signal 'a.owner.json' @{ Publisher='A' }
        [Console]::WriteLine('SESSION_FOLDER='+$script:Root)
        [Console]::WriteLine('On workstation B, run this script with -Role Join -SessionFolder and the session folder above.')
        $script:Stage='peer-and-locks'
        $gate=Open-Gate
        try {
            $gate.Lock(0,1)
            try { Signal 'a.locked.json'; $peer=Wait-Signal 'b.denied.json' 'b' }
            finally { $gate.Unlock(0,1) }
            Assert-True ($peer.RootHash -ceq $script:Context.RootHash) 'UNC roots differ. Retry workstation B using the UNC displayed by A.'
            $peerProtocol=$peer.SmbVerified
            $peerDialect=$peer.Dialect
            $distinct=$peer.NodeHash -cne $script:Context.NodeHash
            Assert-True ($distinct -or $script:Context.SelfTest) 'Two distinct workstation names are required for real validation.'
            Signal 'a.released.json'
            Wait-Signal 'b.acquired.json' 'b' | Out-Null
            Assert-LockDenied $gate
            Signal 'a.denied.json'
            Wait-Signal 'b.released.json' 'b' | Out-Null
            $gate.Lock(0,1); $gate.Unlock(0,1)
        } finally { $gate.Dispose() }
        $script:Stage='cross-participant-replacement'
        Wait-Signal 'b.cross-replaced.json' 'b' | Out-Null
        Assert-True ((Get-Json 'a.owner.json').Publisher -ceq 'B') 'Workstation B could not replace the file created by A.'
        Put-Json 'a.owner.json' @{ RunId=$id; Nonce=$nonce; Publisher='A-again' } $true
        Signal 'a.cross-replaced.json'
        Wait-Signal 'b.cross-checked.json' 'b' | Out-Null
        $script:Stage='atomic-publication'
        Put-Json 'payload.json' (Payload 0)
        Signal 'a.publish-start.json'
        Wait-Signal 'b.reading.json' 'b' | Out-Null
        $elapsed=[Diagnostics.Stopwatch]::StartNew()
        for ($sequence=1; $sequence -le 50; $sequence++) { Put-Json 'payload.json' (Payload $sequence) $true; Start-Sleep -Milliseconds 10 }
        Signal 'a.publish-done.json'
        $peer=Wait-Signal 'b.result.json' 'b'
        Assert-True ($peer.Reads -gt 0 -and $peer.FinalSequence -eq 50) 'Peer did not validate the final publication.'
        $protocol=Smb-Evidence $base.FullName
        $twoPeers=(-not $script:Context.SelfTest) -and $base.IsUnc -and $distinct
        $real=$twoPeers -and $protocol.Verified -and $peerProtocol
        $status='LOCAL_REHEARSAL_PASSED'
        if ($twoPeers) { $status='UNC_FILE_CHECKS_PASSED' }
        if ($real) { $status='SMB_FILE_CHECKS_PASSED' }
        $result=@{ RunId=$id; Nonce=$nonce; Status=$status; SmbFileChecksValidated=$real; TwoDistinctWorkstations=$distinct;
            UncFileChecksValidated=$twoPeers; SmbDialectA=$protocol.Dialect; SmbDialectB=$peerDialect;
            MySQLApplicationSyncValidated=$false; MutualExclusionBothDirections=$true; LockReleased=$true;
            CrossParticipantReplacementBothDirections=$true;
            CompletePeerReads=$peer.Reads; AtomicPublications=50; FinalSequence=50;
            RenameRetriesA=$script:RenameRetries; RenameRetriesB=$peer.RenameRetries; ReadRetriesB=$peer.ReadRetries; RetryCodesA=$script:RetryCodes; RetryCodesB=$peer.RetryCodes;
            PublicationRoundtripMilliseconds=$elapsed.ElapsedMilliseconds; NoProductionFilesChanged=$true;
            ExecutableGenerated=$false; CompletedUtc=[DateTime]::UtcNow.ToString('o') }
        Put-Json 'a.result.json' $result
        $result | ConvertTo-Json -Depth 6
    } else {
        $resolved=Resolve-TestPath $SessionFolder
        $script:Root=$resolved.FullName
        Assert-True ([IO.Path]::GetFileName($script:Root) -match '^impression_smb_validation_([0-9a-f]{32})$') 'Not a validation session folder.'
        $id=$Matches[1]
        Assert-True (([IO.File]::GetAttributes($script:Root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Session reparse points are refused.'
        $script:Context=Get-Json 'manifest.json'
        Assert-True ($script:Context.Tool -ceq 'Impression.SmbValidation' -and $script:Context.Version -eq 1 -and
            $script:Context.RunId -ceq $id -and $script:Context.Nonce -match '^[0-9a-f]{32}$') 'Invalid validation manifest.'
        if ($Role -eq 'Report') {
            $report=Get-Json 'a.result.json'
            Assert-True ($report.RunId -ceq $id -and $report.Nonce -ceq $script:Context.Nonce) 'Invalid result identity.'
            $report | ConvertTo-Json -Depth 6
            exit 0
        }
        Assert-True ($script:Context.SelfTest -eq $AllowLocalSelfTest.IsPresent) 'Self-test mode must agree with workstation A.'
        Signal 'b.claim.json'; $script:Claimed=$true
        $script:Stage='peer-and-locks'
        Wait-Signal 'a.locked.json' 'a' | Out-Null
        $gate=Open-Gate
        try {
            Assert-LockDenied $gate
            $protocol=Smb-Evidence $script:Root
            Signal 'b.denied.json' @{ NodeHash=(Node-Hash $script:Context.Nonce); SmbVerified=$protocol.Verified; Dialect=$protocol.Dialect;
                RootHash=(Text-Hash ([IO.Directory]::GetParent($script:Root).FullName.TrimEnd('\').ToLowerInvariant())) }
            Wait-Signal 'a.released.json' 'a' | Out-Null
            $gate.Lock(0,1)
            try { Signal 'b.acquired.json'; Wait-Signal 'a.denied.json' 'a' | Out-Null }
            finally { $gate.Unlock(0,1) }
            Signal 'b.released.json'
        } finally { $gate.Dispose() }
        $script:Stage='cross-participant-replacement'
        Put-Json 'a.owner.json' @{ RunId=$id; Nonce=$script:Context.Nonce; Publisher='B' } $true
        Signal 'b.cross-replaced.json'
        Wait-Signal 'a.cross-replaced.json' 'a' | Out-Null
        Assert-True ((Get-Json 'a.owner.json').Publisher -ceq 'A-again') 'Workstation A could not replace the file created by B.'
        Signal 'b.cross-checked.json'
        $script:Stage='atomic-publication'
        Wait-Signal 'a.publish-start.json' 'a' | Out-Null
        $value=Get-Json 'payload.json'; Check-Payload $value; $reads=1
        Signal 'b.reading.json'
        $watch=[Diagnostics.Stopwatch]::StartNew()
        while (-not [IO.File]::Exists((Test-File 'a.publish-done.json'))) {
            $value=Get-Json 'payload.json'; Check-Payload $value; $reads++
            Assert-True ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) 'Publication timed out.'
            if ([IO.File]::Exists((Test-File 'a.error.json'))) { throw 'Workstation A reported an error.' }
            Start-Sleep -Milliseconds 5
        }
        $value=Get-Json 'payload.json'; Check-Payload $value
        Assert-True ($value.Sequence -eq 50) 'Final publication is missing.'
        Signal 'b.result.json' @{ Reads=$reads; FinalSequence=$value.Sequence; ReadRetries=$script:ReadRetries; RenameRetries=$script:RenameRetries; RetryCodes=$script:RetryCodes }
        Wait-Signal 'a.result.json' 'a' | ConvertTo-Json -Depth 6
    }
    exit 0
} catch {
    $leaf=Leaf-Exception $_.Exception
    $failure=@{ Status='VALIDATION_INCOMPLETE'; Stage=$script:Stage; ErrorType=$leaf.GetType().Name;
        NativeCode=($leaf.HResult -band 65535); SmbFileChecksValidated=$false; MySQLApplicationSyncValidated=$false; ExecutableGenerated=$false }
    if ($leaf -is [ComponentModel.Win32Exception]) { $failure.NativeCode=$leaf.NativeErrorCode }
    if ($script:Claimed -and $null -ne $script:Context) {
        try {
            $side='a'; if ($Role -eq 'Join') { $side='b' }
            Signal ($side+'.error.json') @{ Stage=$script:Stage; ErrorType=$failure.ErrorType; NativeCode=$failure.NativeCode }
        } catch { }
    }
    [Console]::Error.WriteLine(('Validation incomplete: '+$leaf.Message))
    $failure | ConvertTo-Json -Depth 5
    exit 1
}
