param(
    [string]$ProxyHost = '',
    [int]$ProxyPort = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolchainFile = Join-Path $projectRoot 'build/android-tools/toolchain.json'
if (-not (Test-Path -LiteralPath $toolchainFile)) { throw 'Run scripts/prepare_android_tools.ps1 first.' }
$toolchain = Get-Content -LiteralPath $toolchainFile -Raw -Encoding UTF8 | ConvertFrom-Json
$env:JAVA_HOME = $toolchain.javaHome
$env:ANDROID_HOME = $toolchain.sdkRoot
$env:ANDROID_SDK_ROOT = $toolchain.sdkRoot
$env:ANDROID_USER_HOME = Join-Path $projectRoot 'build/android-tools/user'
$env:GRADLE_USER_HOME = Join-Path $projectRoot 'build/android-tools/gradle-user'
$env:npm_config_cache = Join-Path $projectRoot 'build/npm-cache'
$env:PYTHONUTF8 = '1'
$pythonExe = 'python'
foreach ($candidate in @((Join-Path (Split-Path -Parent $projectRoot) '.venv/Scripts/python.exe'), (Join-Path $projectRoot '.venv/Scripts/python.exe'))) {
    if (Test-Path -LiteralPath $candidate) { $pythonExe = $candidate }
}
if (-not $ProxyHost) {
    $proxyUrl = & $pythonExe -c "import urllib.request; print(urllib.request.getproxies().get('https', ''))"
    if ($LASTEXITCODE -eq 0 -and $proxyUrl) {
        $proxyUri = [uri]$proxyUrl
        $ProxyHost = $proxyUri.Host
        $ProxyPort = $proxyUri.Port
    }
}
$gradleProxy = @()
if ($ProxyHost -and $ProxyPort) {
    $gradleProxy = @("-Dhttps.proxyHost=$ProxyHost", "-Dhttps.proxyPort=$ProxyPort", "-Dhttp.proxyHost=$ProxyHost", "-Dhttp.proxyPort=$ProxyPort")
    $env:npm_config_https_proxy = "http://${ProxyHost}:$ProxyPort"
    $env:npm_config_proxy = $env:npm_config_https_proxy
}
$nativeRoot = Join-Path $projectRoot 'native-app'
$androidRoot = Join-Path $nativeRoot 'android'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sdkProperty = $toolchain.sdkRoot.Replace('\', '/').Replace(':', '\:')
[IO.File]::WriteAllText((Join-Path $androidRoot 'local.properties'), ('sdk.dir=' + $sdkProperty + "`n"), $utf8)

# A stable local test signing identity lets the next APK update the existing install.
# These private files are deliberately ignored by Git and must not accompany the APK.
$signingRoot = Join-Path $projectRoot 'build/android-signing'
New-Item -ItemType Directory -Force $signingRoot | Out-Null
$keyFile = Join-Path $signingRoot 'campus-photo-guide.jks'
$passwordFile = Join-Path $signingRoot 'password.txt'
$propertiesFile = Join-Path $signingRoot 'signing.properties'
if (-not (Test-Path -LiteralPath $keyFile)) {
    if (Test-Path -LiteralPath $propertiesFile) { throw 'Signing config exists but keystore is missing. Restore the original key before building an update.' }
    $password = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText($passwordFile, $password, $utf8)
    & (Join-Path $env:JAVA_HOME 'bin/keytool.exe') -genkeypair -keystore $keyFile -storetype JKS -alias campus-photo-guide -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=Campus Photo Guide Testing' -storepass:file $passwordFile -keypass:file $passwordFile
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate local APK signing key.' }
    [IO.File]::WriteAllText($propertiesFile, "storeFile=campus-photo-guide.jks`nkeyAlias=campus-photo-guide`nstorePassword=$password`nkeyPassword=$password`n", $utf8)
} elseif (-not (Test-Path -LiteralPath $propertiesFile)) {
    throw 'Keystore exists but signing.properties is missing. Restore the matching signing configuration.'
}

& $pythonExe (Join-Path $PSScriptRoot 'prepare_android.py')
if ($LASTEXITCODE -ne 0) { throw 'Android camera UI preparation failed.' }
Push-Location $nativeRoot
try {
    if (-not (Test-Path -LiteralPath 'node_modules/@capacitor/cli/bin/capacitor')) {
        & npm.cmd ci --fetch-timeout=60000 --fetch-retries=2 --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    }
    & npx.cmd --no-install cap sync android
    if ($LASTEXITCODE -ne 0) { throw 'Capacitor asset sync failed.' }
} finally { Pop-Location }

Push-Location $androidRoot
try {
    & (Join-Path $toolchain.gradleHome 'bin/gradle.bat') @gradleProxy --no-daemon --max-workers=4 --console=plain assembleRelease lintRelease
    if ($LASTEXITCODE -ne 0) { throw 'Android build or lint failed.' }
} finally { Pop-Location }
$sourceApk = Join-Path $androidRoot 'app/build/outputs/apk/release/app-release.apk'
& (Join-Path $toolchain.sdkRoot "build-tools/$($toolchain.buildTools)/apksigner.bat") verify --verbose $sourceApk
if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
$output = Join-Path $projectRoot 'build/apk'
New-Item -ItemType Directory -Force $output | Out-Null
$appVersion = (Get-Content -LiteralPath (Join-Path $nativeRoot 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
$targetApk = Join-Path $output "campus-photo-guide-$appVersion.apk"
Copy-Item -LiteralPath $sourceApk -Destination $targetApk
$sha = (Get-FileHash -LiteralPath $targetApk -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $output 'SHA256.txt'), "$sha  campus-photo-guide-$appVersion.apk`n", $utf8)
Write-Host "APK ready: $targetApk"
Write-Host ('Size: {0:N1} MB' -f ((Get-Item -LiteralPath $targetApk).Length / 1MB))
