using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using System.Security.Cryptography;

namespace Squash.Agent;

internal static class Program
{
    public static async Task<int> Main(string[] args)
    {
        try
        {
            if (args.Length > 0)
            {
                return await RunCommandAsync(args);
            }

            HostApplicationBuilder builder = Host.CreateApplicationBuilder(args);
            builder.Services.AddWindowsService(options =>
            {
                options.ServiceName = "Squash RMM Agent";
            });
            RegisterServices(builder.Services);
            using IHost host = builder.Build();
            await host.RunAsync();
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Squash Agent failed: {Sanitize(exception)}");
            return 1;
        }
    }

    private static async Task<int> RunCommandAsync(string[] args)
    {
        switch (args[0].ToLowerInvariant())
        {
            case "fingerprint" when args.Length == 1:
                Console.WriteLine(DeviceIdentity.GetFingerprint());
                return 0;
            case "enroll" when (args.Length == 5 || args.Length == 6) &&
                args[1].Equals("--server", StringComparison.OrdinalIgnoreCase) &&
                args[3].Equals("--token-file", StringComparison.OrdinalIgnoreCase) &&
                (args.Length == 5 || args[5].Equals(
                    "--delete-token-file",
                    StringComparison.OrdinalIgnoreCase)):
            {
                var paths = new AgentPaths();
                var store = new CredentialStore(paths);
                var client = new EnrollmentClient(store);
                await client.EnrollAsync(
                    new Uri(args[2], UriKind.Absolute),
                    args[4],
                    CancellationToken.None);
                if (args.Length == 6)
                {
                    File.Delete(args[4]);
                }
                Console.WriteLine("Enrollment completed.");
                return 0;
            }
            case "run":
            {
                HostApplicationBuilder builder = Host.CreateApplicationBuilder(args[1..]);
                RegisterServices(builder.Services);
                using IHost host = builder.Build();
                await host.RunAsync();
                return 0;
            }
            default:
                Console.Error.WriteLine(
                    "Usage: Squash.Agent.exe fingerprint | enroll --server <https-url> " +
                    "--token-file <path> [--delete-token-file] | run");
                return 2;
        }
    }

    private static void RegisterServices(IServiceCollection services)
    {
        services.AddSingleton<AgentPaths>();
        services.AddSingleton<CredentialStore>();
        services.AddSingleton<ExecutionJournal>();
        services.AddSingleton<PowerShellRunner>();
        services.AddSingleton<AgentChannel>();
        services.AddHostedService<AgentWorker>();
    }

    private static string Sanitize(Exception exception) => exception switch
    {
        FileNotFoundException => "a required local file was not found",
        UnauthorizedAccessException => "access to local agent state was denied",
        CryptographicException => "protected local state could not be decrypted",
        HttpRequestException => "the control plane request failed",
        _ => exception.GetType().Name,
    };
}
