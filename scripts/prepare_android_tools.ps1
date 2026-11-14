param(
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$ProxyHost = '',
    [int]$ProxyPort = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $projectRoot 'build/android-tools'
$sdkRoot = Join-Path $toolsRoot 'sdk'
$downloadRoot = Join-Path $toolsRoot 'downloads'
New-Item -ItemType Directory -Force $downloadRoot | Out-Null
$curlArguments = @('--fail', '--location', '--retry', '3', '--connect-timeout', '30', '--max-time', '900')
if ($ProxyHost -and $ProxyPort) { $curlArguments += @('--proxy', "http://${ProxyHost}:$ProxyPort") }

function Test-FullJdk21([string]$Path) {
    if (-not $Path) { return $false }
    foreach ($relative in @('bin/java.exe', 'bin/javac.exe', 'bin/jlink.exe', 'jmods/java.base.jmod')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $relative))) { return $false }
    }
    try {
        $version = (& (Join-Path $Path 'bin/javac.exe') -version 2>&1 | Out-String).Trim()
        return $LASTEXITCODE -eq 0 -and $version -match '^javac 21(?:\.|$)'
    } catch { return $false }
}

if (-not (Test-FullJdk21 $JavaHome)) {
    $JavaHome = [IO.Path]::GetFullPath((Join-Path $toolsRoot 'jdk-21'))
    if (-not (Test-FullJdk21 $JavaHome)) {
        # Full Temurin JDK; some IDE runtimes contain javac but omit jlink.
        # SHA-256 comes from the matching official release's .zip.sha256.txt.
        $jdkName = 'OpenJDK21U-jdk_x64_windows_hotspot_21.0.13_9.zip'
        $jdkUrl = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/$jdkName"
        $jdkSha = 'a4dbaa05fca4bd89d84584e58c459bd732efebb1722af80ab13c6516832004f9'
        $jdkZip = Join-Path $downloadRoot $jdkName
        if (-not (Test-Path -LiteralPath $jdkZip) -or (Get-FileHash -LiteralPath $jdkZip -Algorithm SHA256).Hash -ne $jdkSha) {
            & curl.exe @curlArguments --output $jdkZip $jdkUrl
            if ($LASTEXITCODE -ne 0) { throw 'JDK 21 download failed.' }
        }
        if ((Get-FileHash -LiteralPath $jdkZip -Algorithm SHA256).Hash -ne $jdkSha) { throw 'JDK 21 checksum mismatch.' }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($jdkZip)
        try {
            $prefix = 'jdk-21.0.13+9/'
            foreach ($entry in $archive.Entries) {
                if (-not $entry.FullName.StartsWith($prefix)) { continue }
                $relative = $entry.FullName.Substring($prefix.Length)
                if (-not $relative -or $entry.FullName.EndsWith('/')) { continue }
                $destination = [IO.Path]::GetFullPath((Join-Path $JavaHome $relative))
                if (-not $destination.StartsWith($JavaHome + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Unexpected archive entry outside the JDK directory.'
                }
                New-Item -ItemType Directory -Force (Split-Path -Parent $destination) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
            }
        } finally { $archive.Dispose() }
        if (-not (Test-FullJdk21 $JavaHome)) { throw 'A full JDK 21 with javac and jlink is required.' }
    }
}
$JavaHome = (Resolve-Path -LiteralPath $JavaHome).Path
$env:JAVA_HOME = $JavaHome
$env:ANDROID_HOME = $sdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_USER_HOME = Join-Path $toolsRoot 'user'
New-Item -ItemType Directory -Force $env:ANDROID_USER_HOME | Out-Null

# Official Windows command-line tools, pinned to the published SHA-256.
# https://developer.android.com/studio#command-tools
$toolsZip = Join-Path $downloadRoot 'commandlinetools-win-15859902.zip'
$toolsSha = '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
if (-not (Test-Path -LiteralPath $toolsZip) -or (Get-FileHash -LiteralPath $toolsZip -Algorithm SHA256).Hash -ne $toolsSha) {
    & curl.exe @curlArguments --output $toolsZip 'https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip'
    if ($LASTEXITCODE -ne 0) { throw 'Android command-line tools download failed.' }
}
if ((Get-FileHash -LiteralPath $toolsZip -Algorithm SHA256).Hash -ne $toolsSha) { throw 'Android command-line tools checksum mismatch.' }

$latestRoot = [IO.Path]::GetFullPath((Join-Path $sdkRoot 'cmdline-tools/latest'))
$sdkManager = Join-Path $latestRoot 'bin/sdkmanager.bat'
if (-not (Test-Path -LiteralPath $sdkManager)) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($toolsZip)
    try {
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith('cmdline-tools/')) { continue }
            $relative = $entry.FullName.Substring('cmdline-tools/'.Length)
            if (-not $relative -or $entry.FullName.EndsWith('/')) { continue }
            $destination = [IO.Path]::GetFullPath((Join-Path $latestRoot $relative))
            if (-not $destination.StartsWith($latestRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Unexpected archive entry outside the SDK directory.'
            }
            New-Item -ItemType Directory -Force (Split-Path -Parent $destination) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
        }
    } finally { $archive.Dispose() }
}

$sdkArguments = @("--sdk_root=$sdkRoot")
if ($ProxyHost -and $ProxyPort) { $sdkArguments += @('--proxy=http', "--proxy_host=$ProxyHost", "--proxy_port=$ProxyPort") }
1..10 | ForEach-Object { 'y' } | & $sdkManager @sdkArguments --licenses
if ($LASTEXITCODE -ne 0) { throw 'Android SDK licenses could not be accepted.' }
& $sdkManager @sdkArguments 'platform-tools' 'platforms;android-36' 'build-tools;36.0.0'
if ($LASTEXITCODE -ne 0) { throw 'Android SDK packages could not be installed.' }

# Official Gradle BIN distribution; checksum published at the adjacent .sha256 URL.
$gradleVersion = '8.14.3'
$gradleSha = 'bd71102213493060956ec229d946beee57158dbd89d0e62b91bca0fa2c5f3531'
$gradleZip = Join-Path $downloadRoot "gradle-$gradleVersion-bin.zip"
$gradleHome = [IO.Path]::GetFullPath((Join-Path $toolsRoot "gradle-$gradleVersion"))
if (-not (Test-Path -LiteralPath $gradleZip) -or (Get-FileHash -LiteralPath $gradleZip -Algorithm SHA256).Hash -ne $gradleSha) {
    & curl.exe @curlArguments --output $gradleZip "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip"
    if ($LASTEXITCODE -ne 0) { throw 'Gradle download failed.' }
}
if ((Get-FileHash -LiteralPath $gradleZip -Algorithm SHA256).Hash -ne $gradleSha) { throw 'Gradle checksum mismatch.' }
if (-not (Test-Path -LiteralPath (Join-Path $gradleHome 'bin/gradle.bat'))) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($gradleZip)
    try {
        foreach ($entry in $archive.Entries) {
            $prefix = "gradle-$gradleVersion/"
            if (-not $entry.FullName.StartsWith($prefix)) { continue }
            $relative = $entry.FullName.Substring($prefix.Length)
            if (-not $relative -or $entry.FullName.EndsWith('/')) { continue }
            $destination = [IO.Path]::GetFullPath((Join-Path $gradleHome $relative))
            if (-not $destination.StartsWith($gradleHome + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Unexpected archive entry outside the Gradle directory.'
            }
            New-Item -ItemType Directory -Force (Split-Path -Parent $destination) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
        }
    } finally { $archive.Dispose() }
}

$toolchain = [ordered]@{javaHome=$JavaHome; sdkRoot=$sdkRoot; sdkManager=$sdkManager; compileSdk=36; buildTools='36.0.0'; gradleHome=$gradleHome}
$toolchain | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $toolsRoot 'toolchain.json') -Encoding utf8
$toolchain | ConvertTo-Json
