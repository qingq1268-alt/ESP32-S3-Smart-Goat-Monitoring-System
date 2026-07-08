$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } elseif ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { throw "Set ANDROID_SDK_ROOT or ANDROID_HOME before running this script." }
$Jdk = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { throw "Set JAVA_HOME before running this script." }
$BuildToolsVersion = if ($env:ANDROID_BUILD_TOOLS_VERSION) { $env:ANDROID_BUILD_TOOLS_VERSION } else { "35.0.0" }
$PlatformVersion = if ($env:ANDROID_PLATFORM_VERSION) { $env:ANDROID_PLATFORM_VERSION } else { "android-35" }
$BuildTools = Join-Path $Sdk "build-tools\$BuildToolsVersion"
$AndroidJar = Join-Path $Sdk "platforms\$PlatformVersion\android.jar"

foreach ($RequiredPath in @($Sdk, $Jdk, $BuildTools, $AndroidJar)) {
    if (!(Test-Path $RequiredPath)) {
        throw "Missing required Android build path: $RequiredPath"
    }
}

$env:JAVA_HOME = $Jdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:PATH = (Join-Path $Jdk "bin") + ";" + (Join-Path $BuildTools "") + ";" + $env:PATH

$Aapt2 = Join-Path $BuildTools "aapt2.exe"
$D8 = Join-Path $BuildTools "d8.bat"
$Zipalign = Join-Path $BuildTools "zipalign.exe"
$ApkSigner = Join-Path $BuildTools "apksigner.bat"
$Javac = Join-Path $Jdk "bin\javac.exe"
$Jar = Join-Path $Jdk "bin\jar.exe"
$Keytool = Join-Path $Jdk "bin\keytool.exe"

$Out = Join-Path $Root "build"
$Compiled = Join-Path $Out "compiled"
$Gen = Join-Path $Out "gen"
$Classes = Join-Path $Out "classes"
$Dex = Join-Path $Out "dex"
$Unsigned = Join-Path $Out "LXSPI-Monitor-unsigned.apk"
$Aligned = Join-Path $Out "LXSPI-Monitor-aligned.apk"
$Final = Join-Path $Out "LXSPI-Monitor-debug.apk"
$Keystore = Join-Path $Out "debug.keystore"
$ClassesJar = Join-Path $Out "classes.jar"

Remove-Item -LiteralPath $Compiled, $Gen, $Classes, $Dex -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Compiled, $Gen, $Classes, $Dex | Out-Null
Remove-Item -LiteralPath $Unsigned, $Aligned, $Final, $ClassesJar -Force -ErrorAction SilentlyContinue

& $Aapt2 compile --dir (Join-Path $Root "app\src\main\res") -o (Join-Path $Compiled "resources.zip")
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }
& $Aapt2 link `
    -I $AndroidJar `
    --manifest (Join-Path $Root "app\src\main\AndroidManifest.xml") `
    --java $Gen `
    --min-sdk-version 23 `
    --target-sdk-version 35 `
    -o $Unsigned `
    (Join-Path $Compiled "resources.zip")
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

$Sources = @()
$Sources += Get-ChildItem -Path (Join-Path $Root "app\src\main\java") -Filter *.java -Recurse | ForEach-Object { $_.FullName }
$Sources += Get-ChildItem -Path $Gen -Filter *.java -Recurse | ForEach-Object { $_.FullName }

& $Javac -encoding UTF-8 -source 8 -target 8 -bootclasspath $AndroidJar -d $Classes $Sources
if ($LASTEXITCODE -ne 0) { throw "javac failed" }
& $Jar cf $ClassesJar -C $Classes .
if ($LASTEXITCODE -ne 0) { throw "jar classes failed" }
& $D8 --lib $AndroidJar --min-api 23 --output $Dex $ClassesJar
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }
& $Jar uf $Unsigned -C $Dex classes.dex
if ($LASTEXITCODE -ne 0) { throw "jar update failed" }
& $Zipalign -f -p 4 $Unsigned $Aligned
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

if (!(Test-Path $Keystore)) {
    & $Keytool -genkeypair `
        -keystore $Keystore `
        -storepass android `
        -keypass android `
        -alias androiddebugkey `
        -keyalg RSA `
        -keysize 2048 `
        -validity 10000 `
        -dname "CN=LXSPI Debug,O=LXSPI,C=CN"
    if ($LASTEXITCODE -ne 0) { throw "keytool failed" }
}

& $ApkSigner sign `
    --ks $Keystore `
    --ks-key-alias androiddebugkey `
    --ks-pass pass:android `
    --key-pass pass:android `
    --out $Final `
    $Aligned
if ($LASTEXITCODE -ne 0) { throw "apksigner sign failed" }

& $ApkSigner verify --verbose $Final
if ($LASTEXITCODE -ne 0) { throw "apksigner verify failed" }
Write-Host "APK built: $Final"
