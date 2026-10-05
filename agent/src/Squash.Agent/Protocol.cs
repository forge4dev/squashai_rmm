using System.Text.Json.Serialization;

namespace Squash.Agent;

internal sealed record EnrollRequest(
    string Token,
    string Fingerprint,
    string Hostname,
    [property: JsonPropertyName("agent_version")] string AgentVersion);

internal sealed record EnrollResponse(
    [property: JsonPropertyName("device_id")] Guid DeviceId,
    [property: JsonPropertyName("device_credential")] string DeviceCredential,
    [property: JsonPropertyName("websocket_url")] Uri WebSocketUrl);

internal sealed record ExecuteMessage(
    int ProtocolVersion,
    string Type,
    Guid ExecutionId,
    string Script,
    string ScriptSha256,
    int TimeoutSeconds,
    int OutputLimitBytesPerStream);

internal sealed record StateMessage(
    int ProtocolVersion,
    string Type,
    Guid ExecutionId,
    string Status,
    DateTimeOffset OccurredAt);

internal sealed record ExecutionResultMessage(
    int ProtocolVersion,
    string Type,
    Guid ExecutionId,
    string Status,
    int? ExitCode,
    string Stdout,
    string Stderr,
    bool StdoutTruncated,
    bool StderrTruncated,
    long DurationMs,
    string ExecutedSha256);
