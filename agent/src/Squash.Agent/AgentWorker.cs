using System.Net.WebSockets;
using System.Text.Json;

namespace Squash.Agent;

internal sealed class AgentWorker(
    AgentChannel channel,
    ILogger<AgentWorker> logger) : BackgroundService
{
    private static readonly Action<ILogger, int, Exception?> LogConnectionFailed =
        LoggerMessage.Define<int>(
            LogLevel.Warning,
            new EventId(2001, "ConnectionFailed"),
            "Control-plane connection failed; retrying (attempt {Attempt}).");

    private static readonly Action<ILogger, string, int, Exception?> LogSessionFailed =
        LoggerMessage.Define<string, int>(
            LogLevel.Error,
            new EventId(2002, "SessionFailed"),
            "Agent session failed ({ExceptionType}); retrying (attempt {Attempt}).");

    private static readonly Action<ILogger, string, Exception?> LogInventoryUploadFailed =
        LoggerMessage.Define<string>(
            LogLevel.Warning,
            new EventId(2003, "InventoryUploadFailed"),
            "Inventory upload failed ({ExceptionType}); retrying later.");

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        Task inventoryTask = InventoryLoopAsync(stoppingToken);
        await ConnectionLoopAsync(stoppingToken);
        await inventoryTask;
    }

    private async Task ConnectionLoopAsync(CancellationToken stoppingToken)
    {
        int failedAttempts = 0;
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await channel.RunSessionAsync(stoppingToken);
                failedAttempts = 0;
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception) when (
                exception is HttpRequestException or WebSocketException or IOException or JsonException)
            {
                failedAttempts++;
                LogConnectionFailed(logger, failedAttempts, null);
            }
            catch (Exception exception)
            {
                failedAttempts++;
                LogSessionFailed(
                    logger,
                    exception.GetType().Name,
                    failedAttempts,
                    null);
            }

            int maximumDelaySeconds = Math.Min(30, 1 << Math.Min(failedAttempts, 5));
            int delayMilliseconds = Random.Shared.Next(500, (maximumDelaySeconds * 1000) + 1);
            try
            {
                await Task.Delay(delayMilliseconds, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
        }
    }

    private async Task InventoryLoopAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            TimeSpan nextDelay = TimeSpan.FromHours(6);
            try
            {
                await channel.UploadInventoryAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                LogInventoryUploadFailed(logger, exception.GetType().Name, null);
                nextDelay = TimeSpan.FromMinutes(5);
            }

            try
            {
                await Task.Delay(nextDelay, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
        }
    }
}
