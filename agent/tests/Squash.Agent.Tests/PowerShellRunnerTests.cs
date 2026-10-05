namespace Squash.Agent.Tests;

public sealed class PowerShellRunnerTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"squash-agent-tests-{Guid.NewGuid():N}");

    [Fact]
    public async Task ReturnsStructuredSuccessfulResult()
    {
        var runner = new PowerShellRunner(new AgentPaths(_root));

        PowerShellRunResult result = await runner.RunAsync(
            Guid.NewGuid(),
            "[Console]::Out.Write('hello')",
            10,
            1024,
            CancellationToken.None);

        Assert.Equal("succeeded", result.Status);
        Assert.Equal(0, result.ExitCode);
        Assert.Contains("hello", result.Stdout, StringComparison.Ordinal);
        Assert.Empty(result.Stderr);
    }

    [Fact]
    public async Task TimesOutAndKillsProcessTree()
    {
        var runner = new PowerShellRunner(new AgentPaths(_root));

        PowerShellRunResult result = await runner.RunAsync(
            Guid.NewGuid(),
            "Start-Sleep -Seconds 30",
            1,
            1024,
            CancellationToken.None);

        Assert.Equal("timed_out", result.Status);
        Assert.Null(result.ExitCode);
        Assert.InRange(result.DurationMs, 500, 10_000);
    }

    [Fact]
    public async Task BoundsOutputWhileDrainingProcess()
    {
        var runner = new PowerShellRunner(new AgentPaths(_root));

        PowerShellRunResult result = await runner.RunAsync(
            Guid.NewGuid(),
            "1..10000 | ForEach-Object { '0123456789' }",
            10,
            128,
            CancellationToken.None);

        Assert.Equal("succeeded", result.Status);
        Assert.True(result.StdoutTruncated);
        Assert.True(System.Text.Encoding.UTF8.GetByteCount(result.Stdout) <= 128);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }
}
