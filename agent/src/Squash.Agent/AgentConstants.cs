namespace Squash.Agent;

internal static class AgentConstants
{
    public const int ProtocolVersion = 1;
    public const string Version = "0.1.0";
    public const int MaximumMessageBytes = 512 * 1024;
    public const int MaximumScriptBytes = 256 * 1024;
    public const int MaximumOutputBytes = 1024 * 1024;
    public static readonly byte[] DpapiEntropy = "SquashRMM.Agent.v1"u8.ToArray();
}
