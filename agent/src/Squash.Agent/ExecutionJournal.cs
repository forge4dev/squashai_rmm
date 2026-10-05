using System.Security.Cryptography;
using System.Text.Json;

namespace Squash.Agent;

internal sealed record JournalEntry(
    Guid ExecutionId,
    string State,
    DateTimeOffset UpdatedAt,
    string ScriptSha256,
    ExecutionResultMessage? Result);

internal sealed class ExecutionJournal
{
    private const int MaximumEntries = 1000;
    private readonly AgentPaths _paths;
    private readonly object _lock = new();
    private Dictionary<Guid, JournalEntry> _entries;

    public ExecutionJournal(AgentPaths paths)
    {
        _paths = paths;
        _paths.EnsureDirectories();
        _entries = Load();
    }

    public JournalEntry? Get(Guid executionId)
    {
        lock (_lock)
        {
            return _entries.GetValueOrDefault(executionId);
        }
    }

    public IReadOnlyList<JournalEntry> Snapshot()
    {
        lock (_lock)
        {
            return _entries.Values.OrderBy(entry => entry.UpdatedAt).ToArray();
        }
    }

    public void RecordRunning(Guid executionId, string scriptSha256)
    {
        lock (_lock)
        {
            _entries[executionId] = new JournalEntry(
                executionId,
                "running",
                DateTimeOffset.UtcNow,
                scriptSha256,
                null);
            Save();
        }
    }

    public void RecordResult(ExecutionResultMessage result)
    {
        lock (_lock)
        {
            _entries[result.ExecutionId] = new JournalEntry(
                result.ExecutionId,
                "terminal",
                DateTimeOffset.UtcNow,
                result.ExecutedSha256,
                result);
            if (_entries.Count > MaximumEntries)
            {
                foreach (Guid id in _entries.Values
                    .OrderBy(entry => entry.UpdatedAt)
                    .Take(_entries.Count - MaximumEntries)
                    .Select(entry => entry.ExecutionId))
                {
                    _entries.Remove(id);
                }
            }
            Save();
        }
    }

    private Dictionary<Guid, JournalEntry> Load()
    {
        if (!File.Exists(_paths.JournalFile))
        {
            return [];
        }

        try
        {
            byte[] protectedBytes = File.ReadAllBytes(_paths.JournalFile);
            byte[] plaintext = ProtectedData.Unprotect(
                protectedBytes,
                AgentConstants.DpapiEntropy,
                DataProtectionScope.LocalMachine);
            try
            {
                return JsonSerializer.Deserialize<Dictionary<Guid, JournalEntry>>(
                    plaintext,
                    JsonDefaults.Options) ?? [];
            }
            finally
            {
                CryptographicOperations.ZeroMemory(plaintext);
            }
        }
        catch (Exception exception) when (
            exception is CryptographicException or IOException or JsonException)
        {
            throw new InvalidOperationException("The encrypted execution journal is unreadable.", exception);
        }
    }

    private void Save()
    {
        byte[] plaintext = JsonSerializer.SerializeToUtf8Bytes(_entries, JsonDefaults.Options);
        try
        {
            byte[] protectedBytes = ProtectedData.Protect(
                plaintext,
                AgentConstants.DpapiEntropy,
                DataProtectionScope.LocalMachine);
            AtomicFile.WriteAllBytes(_paths.JournalFile, protectedBytes);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }
}
