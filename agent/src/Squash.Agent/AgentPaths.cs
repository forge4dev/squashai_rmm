namespace Squash.Agent;

internal sealed class AgentPaths
{
    public AgentPaths(string? root = null)
    {
        Root = root ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "SquashRMM",
            "Agent");
    }

    public string Root { get; }
    public string ConfigurationFile => Path.Combine(Root, "agent.json");
    public string JournalFile => Path.Combine(Root, "execution-journal.bin");
    public string JobsDirectory => Path.Combine(Root, "Jobs");

    public void EnsureDirectories()
    {
        Directory.CreateDirectory(Root);
        Directory.CreateDirectory(JobsDirectory);
    }
}
