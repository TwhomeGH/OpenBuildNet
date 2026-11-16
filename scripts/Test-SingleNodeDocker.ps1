param(
    [string]$Image = 'openbuildnet-dev:v0.1',
    [switch]$TestFailure,
    [switch]$TestTimeout,
    [switch]$TestIsolation
)

$ErrorActionPreference = 'Stop'
if (@($TestFailure, $TestTimeout, $TestIsolation).Where({ $_ }).Count -gt 1) { throw 'Choose one test case.' }
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

if ($TestTimeout) {
    $command = 'echo build-started; sleep 60'
}

if ($TestIsolation) {
    $sentinel = "host-only-$taskId"
    Set-Content -LiteralPath (Join-Path $output.FullName $sentinel) -Value 'host only'
    $command = 'test ! -e /tmp/{0} && test ! -e /run/docker.sock && test "$(ls /sys/class/net)" = lo && echo isolation-ok' -f $sentinel
}
try {
    docker create --pull never --name $container --network none --memory 128m --cpus 1 --pids-limit 32 $Image sh -c $command
    if ($LASTEXITCODE -ne 0) { throw "Container creation failed. Ensure the local image exists: $Image" }

    $isolation = docker inspect --format '{{.HostConfig.NetworkMode}} {{.HostConfig.Privileged}} {{len .Mounts}}' $container
    if ($LASTEXITCODE -ne 0 -or $isolation -ne 'none false 0') {
        throw "Expected no network, no privileged mode and no mounts. Docker reported: $isolation"
    }

    Set-Content -LiteralPath (Join-Path $output.FullName 'input.txt') -Value 'OpenBuildNet smoke test' -Encoding ascii
    docker cp (Join-Path $output.FullName 'input.txt') "${container}:/tmp/input.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Input transfer failed.' }

    $logPath = Join-Path $output.FullName 'build.log'
    if ($TestTimeout) {
        docker start $container | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Container start failed.' }

        Start-Sleep -Seconds 3
        $running = docker inspect --format '{{.State.Running}}' $container
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') {
            throw 'Expected the container to still be running at the deadline.'
        }

        docker kill $container | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Container stop failed.' }

        $running = docker inspect --format '{{.State.Running}}' $container
        if ($LASTEXITCODE -ne 0 -or $running -ne 'false') { throw 'Container did not stop.' }

        docker logs $container | Set-Content -LiteralPath $logPath
        if ($LASTEXITCODE -ne 0 -or -not (Select-String -LiteralPath $logPath -SimpleMatch 'build-started' -Quiet)) {
            throw "Startup message missing from $logPath"
        }
        [pscustomobject]@{ TaskId = $taskId; Status = 'timed_out'; OutputDirectory = $output.FullName }
        return
    }

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

    if ($TestIsolation) {
        if (-not (Select-String -LiteralPath $logPath -SimpleMatch 'isolation-ok' -Quiet)) {
            throw "Isolation confirmation missing from $logPath"
        }
        [pscustomobject]@{ TaskId = $taskId; Status = 'isolation_verified'; OutputDirectory = $output.FullName }
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
