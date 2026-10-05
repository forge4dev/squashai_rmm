using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Squash.Agent;

internal sealed record AgentConfiguration(
    Uri ControlPlaneBaseUrl,
    Guid DeviceId,
    Uri WebSocketUrl,
    string ProtectedCredential);

internal sealed record AgentIdentity(
    Uri ControlPlaneBaseUrl,
    Guid DeviceId,
    Uri WebSocketUrl,
    string Credential);

internal sealed class CredentialStore(AgentPaths paths)
{
    public AgentIdentity Load()
    {
        AgentConfiguration configuration = JsonSerializer.Deserialize<AgentConfiguration>(
            File.ReadAllText(paths.ConfigurationFile), JsonDefaults.Options)
            ?? throw new InvalidOperationException("Agent configuration is invalid.");
        byte[] protectedBytes = Convert.FromBase64String(configuration.ProtectedCredential);
        byte[] plaintext = ProtectedData.Unprotect(
            protectedBytes,
            AgentConstants.DpapiEntropy,
            DataProtectionScope.LocalMachine);
        try
        {
            return new AgentIdentity(
                configuration.ControlPlaneBaseUrl,
                configuration.DeviceId,
                configuration.WebSocketUrl,
                Encoding.UTF8.GetString(plaintext));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }

    public void Save(Uri baseUrl, Guid deviceId, Uri websocketUrl, string credential)
    {
        paths.EnsureDirectories();
        byte[] plaintext = Encoding.UTF8.GetBytes(credential);
        try
        {
            byte[] protectedBytes = ProtectedData.Protect(
                plaintext,
                AgentConstants.DpapiEntropy,
                DataProtectionScope.LocalMachine);
            var configuration = new AgentConfiguration(
                baseUrl,
                deviceId,
                websocketUrl,
                Convert.ToBase64String(protectedBytes));
            AtomicFile.WriteAllText(
                paths.ConfigurationFile,
                JsonSerializer.Serialize(configuration, JsonDefaults.Options));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }
}
