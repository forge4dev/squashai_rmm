using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;

namespace Squash.Agent;

internal sealed record PowerShellRunResult(
    string Status,
    int? ExitCode,
    string Stdout,
    string Stderr,
    bool StdoutTruncated,
    bool StderrTruncated,
    long DurationMs);

internal sealed class PowerShellRunner(AgentPaths paths)
{
    public async Task<PowerShellRunResult> RunAsync(
        Guid executionId,
        string script,
        int timeoutSeconds,
        int outputLimit,
        CancellationToken cancellationToken)
    {
        paths.EnsureDirectories();
        string scriptPath = Path.Combine(paths.JobsDirectory, $"{executionId:D}.ps1");
        await File.WriteAllTextAsync(scriptPath, script, new UTF8Encoding(true), cancellationToken);

        using var stdout = new BoundedOutput(outputLimit);
        using var stderr = new BoundedOutput(outputLimit);
        var startInfo = new ProcessStartInfo
        {
            FileName = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe"),
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (string argument in new[]
        {
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            scriptPath,
        })
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, eventArgs) => stdout.AppendLine(eventArgs.Data);
        process.ErrorDataReceived += (_, eventArgs) => stderr.AppendLine(eventArgs.Data);
        var stopwatch = Stopwatch.StartNew();
        bool timedOut = false;
        bool interrupted = false;
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("PowerShell could not be started.");
            }
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSeconds));
            using var combined = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken,
                timeout.Token);
            try
            {
                await process.WaitForExitAsync(combined.Token);
                process.WaitForExit();
            }
            catch (OperationCanceledException)
            {
                timedOut = timeout.IsCancellationRequested && !cancellationToken.IsCancellationRequested;
                interrupted = !timedOut;
                KillProcessTree(process);
                await process.WaitForExitAsync(CancellationToken.None);
                process.WaitForExit();
            }

            string status = timedOut
                ? "timed_out"
                : interrupted
                    ? "agent_interrupted"
                    : process.ExitCode == 0 ? "succeeded" : "failed";
            return new PowerShellRunResult(
                status,
                timedOut || interrupted ? null : process.ExitCode,
                stdout.GetText(),
                stderr.GetText(),
                stdout.Truncated,
                stderr.Truncated,
                stopwatch.ElapsedMilliseconds);
        }
        finally
        {
            stopwatch.Stop();
            try
            {
                File.Delete(scriptPath);
            }
            catch (IOException)
            {
                // A later maintenance pass can remove a file still held by the OS.
            }
        }
    }

    private static void KillProcessTree(Process process)
    {
        if (!process.HasExited)
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
                // The process exited between the state check and termination.
            }
        }
    }

    public static string HashScript(string script) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(script))).ToLowerInvariant();
}
