namespace Squash.Agent.Tests;

public sealed class ExecutionJournalTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"squash-agent-tests-{Guid.NewGuid():N}");

    [Fact]
    public void PersistsRunningIdentityAndTerminalResultEncrypted()
    {
        var paths = new AgentPaths(_root);
        var journal = new ExecutionJournal(paths);
        Guid executionId = Guid.NewGuid();
        string hash = new('a', 64);
        journal.RecordRunning(executionId, hash);

        var reloaded = new ExecutionJournal(paths);
        JournalEntry entry = Assert.IsType<JournalEntry>(reloaded.Get(executionId));
        Assert.Equal("running", entry.State);
        Assert.Equal(hash, entry.ScriptSha256);
        Assert.DoesNotContain(executionId.ToString(), File.ReadAllText(paths.JournalFile));

        var result = new ExecutionResultMessage(
            1, "result", executionId, "succeeded", 0, "output", "", false, false, 4, hash);
        reloaded.RecordResult(result);
        JournalEntry terminal = Assert.IsType<JournalEntry>(
            new ExecutionJournal(paths).Get(executionId));
        Assert.Equal("terminal", terminal.State);
        Assert.Equal("output", terminal.Result?.Stdout);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }
}
