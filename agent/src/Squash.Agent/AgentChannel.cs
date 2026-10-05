using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Net.WebSockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;

namespace Squash.Agent;

internal sealed class AgentChannel(
    CredentialStore credentialStore,
    ExecutionJournal journal,
    PowerShellRunner runner,
    ILogger<AgentChannel> logger)
{
    private static readonly Action<ILogger, Guid, Exception?> LogConnected = LoggerMessage.Define<Guid>(
        LogLevel.Information,
        new EventId(1001, "Connected"),
        "Connected to the control plane as device {DeviceId}.");

    public async Task RunSessionAsync(CancellationToken stoppingToken)
    {
        AgentIdentity identity = credentialStore.Load();
        using var socket = new ClientWebSocket();
        socket.Options.SetRequestHeader("Authorization", $"Bearer {identity.Credential}");
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(15);
        await socket.ConnectAsync(identity.WebSocketUrl, stoppingToken);
        LogConnected(logger, identity.DeviceId, null);

        using var sessionCancellation = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        var sendLock = new SemaphoreSlim(1, 1);
        var jobs = Channel.CreateBounded<ExecuteMessage>(new BoundedChannelOptions(32)
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = true,
        });

        await ReplayJournalAsync(socket, sendLock, sessionCancellation.Token);
        Task receiveTask = ReceiveLoopAsync(socket, jobs.Writer, sendLock, sessionCancellation.Token);
        Task heartbeatTask = HeartbeatLoopAsync(socket, sendLock, sessionCancellation.Token);
        Task executionTask = ExecutionLoopAsync(
            socket,
            jobs.Reader,
            sendLock,
            sessionCancellation.Token);
        Task completed = await Task.WhenAny(receiveTask, heartbeatTask, executionTask);
        sessionCancellation.Cancel();
        jobs.Writer.TryComplete();
        try
        {
            await Task.WhenAll(receiveTask, heartbeatTask, executionTask);
        }
        catch (OperationCanceledException) when (sessionCancellation.IsCancellationRequested)
        {
        }
        if (completed.IsFaulted)
        {
            await completed;
        }
    }

    public async Task UploadInventoryAsync(CancellationToken cancellationToken)
    {
        AgentIdentity identity = credentialStore.Load();
        using var http = new HttpClient
        {
            BaseAddress = identity.ControlPlaneBaseUrl,
            Timeout = TimeSpan.FromSeconds(30),
        };
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue(
            "Bearer",
            identity.Credential);
        using HttpResponseMessage response = await http.PutAsJsonAsync(
            "/v1/agent/inventory",
            InventoryCollector.Collect(),
            JsonDefaults.Options,
            cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            throw new HttpRequestException(
                $"Inventory upload returned HTTP status {(int)response.StatusCode}.");
        }
    }

    private static async Task ReceiveLoopAsync(
        ClientWebSocket socket,
        ChannelWriter<ExecuteMessage> writer,
        SemaphoreSlim sendLock,
        CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            byte[] payload = await ReceiveMessageAsync(socket, cancellationToken);
            using JsonDocument document = JsonDocument.Parse(payload);
            JsonElement root = document.RootElement;
            string? type = root.TryGetProperty("type", out JsonElement typeNode)
                ? typeNode.GetString()
                : null;
            switch (type)
            {
                case "connected":
                case "heartbeat_ack":
                    break;
                case "execute":
                    ExecuteMessage job = root.Deserialize<ExecuteMessage>(JsonDefaults.Options)
                        ?? throw new InvalidDataException("The execute message was invalid.");
                    ValidateJob(job);
                    await writer.WriteAsync(job, cancellationToken);
                    break;
                case "error":
                    throw new InvalidDataException("The control plane rejected an agent message.");
                default:
                    await SendAsync(
                        socket,
                        new { protocolVersion = 1, type = "error", code = "invalid_message" },
                        sendLock,
                        cancellationToken);
                    break;
            }
        }
    }

    private async Task ExecutionLoopAsync(
        ClientWebSocket socket,
        ChannelReader<ExecuteMessage> reader,
        SemaphoreSlim sendLock,
        CancellationToken cancellationToken)
    {
        await foreach (ExecuteMessage job in reader.ReadAllAsync(cancellationToken))
        {
            JournalEntry? existing = journal.Get(job.ExecutionId);
            if (existing?.Result is not null)
            {
                await SendAsync(socket, existing.Result, sendLock, cancellationToken);
                continue;
            }
            if (existing?.State == "running")
            {
                ExecutionResultMessage interrupted = InterruptedResult(job);
                journal.RecordResult(interrupted);
                await SendAsync(socket, interrupted, sendLock, cancellationToken);
                continue;
            }

            string actualHash = PowerShellRunner.HashScript(job.Script);
            if (!CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(actualHash),
                Encoding.ASCII.GetBytes(job.ScriptSha256.ToLowerInvariant())))
            {
                var mismatch = new ExecutionResultMessage(
                    AgentConstants.ProtocolVersion,
                    "result",
                    job.ExecutionId,
                    "failed",
                    null,
                    string.Empty,
                    "Script hash validation failed.",
                    false,
                    false,
                    0,
                    actualHash);
                journal.RecordResult(mismatch);
                await SendAsync(socket, mismatch, sendLock, cancellationToken);
                continue;
            }

            journal.RecordRunning(job.ExecutionId, actualHash);
            await SendAsync(
                socket,
                new StateMessage(
                    AgentConstants.ProtocolVersion,
                    "state",
                    job.ExecutionId,
                    "running",
                    DateTimeOffset.UtcNow),
                sendLock,
                cancellationToken);

            PowerShellRunResult run = await runner.RunAsync(
                job.ExecutionId,
                job.Script,
                job.TimeoutSeconds,
                job.OutputLimitBytesPerStream,
                cancellationToken);
            var result = new ExecutionResultMessage(
                AgentConstants.ProtocolVersion,
                "result",
                job.ExecutionId,
                run.Status,
                run.ExitCode,
                run.Stdout,
                run.Stderr,
                run.StdoutTruncated,
                run.StderrTruncated,
                run.DurationMs,
                actualHash);
            journal.RecordResult(result);
            if (!cancellationToken.IsCancellationRequested)
            {
                await SendAsync(socket, result, sendLock, cancellationToken);
            }
        }
    }

    private async Task ReplayJournalAsync(
        ClientWebSocket socket,
        SemaphoreSlim sendLock,
        CancellationToken cancellationToken)
    {
        foreach (JournalEntry entry in journal.Snapshot())
        {
            ExecutionResultMessage? result = entry.Result;
            if (entry.State == "running")
            {
                result = new ExecutionResultMessage(
                    AgentConstants.ProtocolVersion,
                    "result",
                    entry.ExecutionId,
                    "agent_interrupted",
                    null,
                    string.Empty,
                    "Execution was interrupted by an agent restart.",
                    false,
                    false,
                    0,
                    entry.ScriptSha256);
                journal.RecordResult(result);
            }
            if (result is not null)
            {
                await SendAsync(socket, result, sendLock, cancellationToken);
            }
        }
    }

    private static ExecutionResultMessage InterruptedResult(ExecuteMessage job) => new(
        AgentConstants.ProtocolVersion,
        "result",
        job.ExecutionId,
        "agent_interrupted",
        null,
        string.Empty,
        "Execution was interrupted before completion.",
        false,
        false,
        0,
        job.ScriptSha256);

    private static async Task HeartbeatLoopAsync(
        ClientWebSocket socket,
        SemaphoreSlim sendLock,
        CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(10));
        while (await timer.WaitForNextTickAsync(cancellationToken))
        {
            await SendAsync(
                socket,
                new { protocolVersion = 1, type = "heartbeat" },
                sendLock,
                cancellationToken);
        }
    }

    private static async Task SendAsync<T>(
        ClientWebSocket socket,
        T message,
        SemaphoreSlim sendLock,
        CancellationToken cancellationToken)
    {
        byte[] payload = JsonSerializer.SerializeToUtf8Bytes(message, JsonDefaults.Options);
        await sendLock.WaitAsync(cancellationToken);
        try
        {
            await socket.SendAsync(
                payload,
                WebSocketMessageType.Text,
                WebSocketMessageFlags.EndOfMessage,
                cancellationToken);
        }
        finally
        {
            sendLock.Release();
        }
    }

    private static async Task<byte[]> ReceiveMessageAsync(
        ClientWebSocket socket,
        CancellationToken cancellationToken)
    {
        byte[] buffer = new byte[16 * 1024];
        using var payload = new MemoryStream();
        while (true)
        {
            ValueWebSocketReceiveResult result = await socket.ReceiveAsync(
                buffer.AsMemory(),
                cancellationToken);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                throw new WebSocketException("The control plane closed the connection.");
            }
            if (result.MessageType != WebSocketMessageType.Text)
            {
                throw new InvalidDataException("Only text WebSocket messages are supported.");
            }
            if (payload.Length + result.Count > AgentConstants.MaximumMessageBytes)
            {
                throw new InvalidDataException("The WebSocket message exceeded the size limit.");
            }
            payload.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                return payload.ToArray();
            }
        }
    }

    private static void ValidateJob(ExecuteMessage job)
    {
        if (job.ProtocolVersion != AgentConstants.ProtocolVersion ||
            job.Type != "execute" ||
            job.ExecutionId == Guid.Empty ||
            job.TimeoutSeconds is < 1 or > 300 ||
            job.OutputLimitBytesPerStream is < 0 or > AgentConstants.MaximumOutputBytes ||
            Encoding.UTF8.GetByteCount(job.Script) > AgentConstants.MaximumScriptBytes ||
            job.ScriptSha256.Length != 64)
        {
            throw new InvalidDataException("The execute message failed validation.");
        }
    }
}
