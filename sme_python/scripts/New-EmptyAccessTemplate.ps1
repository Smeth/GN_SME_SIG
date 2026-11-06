<# Build-only: operate on an owned copy, never the source database.
   DAO CompactDatabase rebuilds the file after deleting rows so deleted pages
   from the original are not distributed. No saved query is executed. #>
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$destinationPath = [IO.Path]::GetFullPath($Destination)

function Assert-BuildPath([string]$Path) {
    $valid = $false
    foreach ($folder in @('build', 'dist')) {
        $base = [IO.Path]::GetFullPath((Join-Path $project $folder)) + [IO.Path]::DirectorySeparatorChar
        if ($Path.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { $valid = $true }
    }
    if (-not $valid) { throw 'Destination must be inside project build/ or dist/.' }
    $item = $Path
    while ($item -and $item.Length -ge $project.Length) {
        if ((Test-Path -LiteralPath $item) -and
            ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Reparse points are forbidden for template output.'
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
}
Assert-BuildPath $destinationPath
if ($sourcePath -eq $destinationPath) { throw 'Source and destination must differ.' }
if ([IO.Path]::GetExtension($sourcePath) -notin @('.mdb', '.accdb') -or
    [IO.Path]::GetExtension($destinationPath) -ne [IO.Path]::GetExtension($sourcePath)) {
    throw 'Source and destination must have the same Access format.'
}
$sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
$work = Join-Path $project ('build\empty_access_' + [Guid]::NewGuid().ToString('N'))
Assert-BuildPath $work
[void][IO.Directory]::CreateDirectory($work)
$copy = Join-Path $work ('source' + [IO.Path]::GetExtension($sourcePath))
$clean = Join-Path $work ('empty' + [IO.Path]::GetExtension($sourcePath))
$engine = $null
$db = $null
$phase = 'initialization'

function Get-UserTables($Database) {
    $names = @()
    foreach ($table in $Database.TableDefs) {
        # dbSystemObject is the signed high bit; hidden user tables are included.
        if ($table.Connect) { throw 'Linked tables are forbidden in templates.' }
        if (($table.Attributes -band -2147483648) -eq 0 -and
            -not $table.Name.StartsWith('MSys', [StringComparison]::OrdinalIgnoreCase)) {
            $names += [string]$table.Name
        }
    }
    foreach ($query in $Database.QueryDefs) {
        if ($query.Connect) { throw 'External queries are forbidden in templates.' }
    }
    return $names | Sort-Object
}

function Get-Schema($Database, $Names) {
    $tables = foreach ($name in $Names) {
        $table = $Database.TableDefs.Item($name)
        $fields = foreach ($field in $table.Fields) {
            @($field.Name, $field.Type, $field.Size, $field.Attributes,
              $field.Required, $field.DefaultValue, $field.ValidationRule)
        }
        $indexes = foreach ($index in $table.Indexes) {
            $members = foreach ($field in $index.Fields) { @($field.Name, $field.Attributes) }
            @($index.Name, $index.Primary, $index.Unique, $index.Required, @($members))
        }
        [ordered]@{name=$name; fields=@($fields); indexes=@($indexes)}
    }
    $relations = foreach ($relation in $Database.Relations) {
        $members = foreach ($field in $relation.Fields) { @($field.Name, $field.ForeignName) }
        @($relation.Name, $relation.Table, $relation.ForeignTable, $relation.Attributes, @($members))
    }
    # Ordered outer representation; table properties use ordered dictionaries.
    return [ordered]@{tables=@($tables); relations=@($relations)} | ConvertTo-Json -Depth 20 -Compress
}

try {
    [IO.File]::Copy($sourcePath, $copy, $false)
    $engine = New-Object -ComObject DAO.DBEngine.120
    $phase = 'schema inspection'
    $db = $engine.OpenDatabase($copy, $true, $false)
    $phase = 'checking local tables'
    $names = @(Get-UserTables $db)
    if ($names.Count -eq 0) { throw 'No export tables.' }
    $phase = 'reading table structure'
    $before = Get-Schema $db $names
    $phase = 'clearing the owned copy'
    $pending = @($names)
    while ($pending.Count -gt 0) {
        $next = @()
        foreach ($name in $pending) {
            try { $db.Execute(('DELETE FROM [' + $name.Replace(']', ']]') + ']'), 128) }
            catch { $next += $name }
        }
        if ($next.Count -eq $pending.Count) { throw 'Cannot clear tables without changing relationships.' }
        $pending = $next
    }
    $db.Close()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    $db = $null
    $phase = 'compacting the empty copy'
    $engine.CompactDatabase($copy, $clean)
    $phase = 'verifying the compacted template'
    $db = $engine.OpenDatabase($clean, $true, $true)
    $afterNames = @(Get-UserTables $db)
    if ((Get-Schema $db $afterNames) -cne $before) { throw 'Schema changed.' }
    foreach ($name in $afterNames) {
        $records = $db.OpenRecordset(('SELECT COUNT(*) AS n FROM [' + $name.Replace(']', ']]') + ']'))
        try { if ([long]$records.Fields.Item(0).Value -ne 0) { throw 'Template is not empty.' } }
        finally { $records.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($records) }
    }
    $db.Close()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    $db = $null
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -ne $sourceHash) {
        throw 'Source changed during build.'
    }
    $phase = "publishing verified template"
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destinationPath))
    if (Test-Path -LiteralPath $destinationPath) { [IO.File]::Replace($clean, $destinationPath, [NullString]::Value) }
    else { [IO.File]::Move($clean, $destinationPath) }
    [ordered]@{tables=$names.Count; bytes=(Get-Item -LiteralPath $destinationPath).Length;
        sha256=(Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash.ToLowerInvariant();
        source_unchanged=$true} | ConvertTo-Json -Compress
}
catch {
    # Do not leak table contents or connection strings in build logs.
    throw ('Empty Access template failed during ' + $phase + ' (code ' + $_.Exception.HResult +
        ', script line ' + $_.InvocationInfo.ScriptLineNumber + '). No source data was modified.')
}
finally {
    if ($null -ne $db) { $db.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db) }
    if ($null -ne $engine) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($engine) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    # Only files from the unique owned working directory may be removed.
    foreach ($file in [IO.Directory]::GetFiles($work)) {
        Assert-BuildPath $file
        Remove-Item -LiteralPath $file -Force
    }
    [IO.Directory]::Delete($work, $false)
}
