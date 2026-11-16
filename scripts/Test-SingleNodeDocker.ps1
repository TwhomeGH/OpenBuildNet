param(
    [string]$Image = 'openbuildnet-dev:v0.1',
    [switch]$TestFailure
)

$ErrorActionPreference = 'Stop'
$taskId = [Guid]::NewGuid().ToString('N')
$container = "openbuildnet-smoke-$taskId"
$output = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) $container)

docker info --format '{{.OSType}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot connect to the Docker engine.' }

$expectedExitCode = 0
$command = 'cat /tmp/input.txt > /tmp/result.txt && echo build-complete'
if ($TestFailure) {
    $expectedExitCode = 7
    $command = 'echo build-failed >&2; exit 7'
}

try {
    docker create --pull never --name $container --network none --memory 128m --cpus 1 --pids-limit 32 $Image sh -c $command
    if ($LASTEXITCODE -ne 0) { throw "Container creation failed. Ensure the local image exists: $Image" }

    Set-Content -LiteralPath (Join-Path $output.FullName 'input.txt') -Value 'OpenBuildNet smoke test' -Encoding ascii
    docker cp (Join-Path $output.FullName 'input.txt') "${container}:/tmp/input.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Input transfer failed.' }

    $logPath = Join-Path $output.FullName 'build.log'
    $PSNativeCommandUseErrorActionPreference = $false
    docker start --attach $container 2>&1 | Set-Content -LiteralPath $logPath
    $attachExitCode = $LASTEXITCODE
    $exitCode = docker inspect --format '{{.State.ExitCode}}' $container
    if ($LASTEXITCODE -ne 0 -or $exitCode -ne "$expectedExitCode" -or $attachExitCode -ne $expectedExitCode) {
        throw "Expected exit code $expectedExitCode. Container: $exitCode. Docker attach: $attachExitCode. Output: $output"
    }

    if ($TestFailure) {
        if (-not (Select-String -LiteralPath $logPath -SimpleMatch 'build-failed' -Quiet)) {
            throw "Failure message missing from $logPath"
        }
        [pscustomobject]@{ TaskId = $taskId; ExitCode = [int]$exitCode; OutputDirectory = $output.FullName }
        return
    }

    docker cp "${container}:/tmp/result.txt" (Join-Path $output.FullName 'result.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Artifact collection failed.' }
    if ((Get-Content -LiteralPath (Join-Path $output.FullName 'result.txt')) -ne 'OpenBuildNet smoke test') {
        throw 'Artifact contents did not match the input.'
    }

    [pscustomobject]@{ TaskId = $taskId; ExitCode = [int]$exitCode; OutputDirectory = $output.FullName }
}
finally {
    docker rm -f $container | Out-Null
}
