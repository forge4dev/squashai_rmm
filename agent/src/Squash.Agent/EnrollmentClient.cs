using System.Net.Http.Json;
using System.Security.Cryptography;

namespace Squash.Agent;

internal sealed class EnrollmentClient(CredentialStore credentialStore)
{
    public async Task EnrollAsync(
        Uri controlPlaneBaseUrl,
        string tokenFile,
        CancellationToken cancellationToken)
    {
        ValidateBaseUrl(controlPlaneBaseUrl);
        string token = (await File.ReadAllTextAsync(tokenFile, cancellationToken)).Trim();
        if (token.Length is < 32 or > 256)
        {
            throw new InvalidOperationException("The enrollment token file is invalid.");
        }

        try
        {
            using var http = new HttpClient { BaseAddress = controlPlaneBaseUrl, Timeout = TimeSpan.FromSeconds(30) };
            using HttpResponseMessage response = await http.PostAsJsonAsync(
                "/v1/agent/enroll",
                new EnrollRequest(
                    token,
                    DeviceIdentity.GetFingerprint(),
                    Environment.MachineName,
                    AgentConstants.Version),
                JsonDefaults.Options,
                cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException(
                    $"Enrollment was rejected with HTTP status {(int)response.StatusCode}.");
            }

            EnrollResponse enrolled = await response.Content.ReadFromJsonAsync<EnrollResponse>(
                JsonDefaults.Options,
                cancellationToken)
                ?? throw new InvalidOperationException("The enrollment response was invalid.");
            if (enrolled.WebSocketUrl.Scheme != "wss")
            {
                throw new InvalidOperationException("The control plane returned a non-TLS WebSocket URL.");
            }

            credentialStore.Save(
                controlPlaneBaseUrl,
                enrolled.DeviceId,
                enrolled.WebSocketUrl,
                enrolled.DeviceCredential);
        }
        finally
        {
            // Strings cannot be reliably zeroed; keep their lifetime as short as possible.
            token = string.Empty;
        }
    }

    private static void ValidateBaseUrl(Uri url)
    {
        if (!url.IsAbsoluteUri || url.Scheme != Uri.UriSchemeHttps || !string.IsNullOrEmpty(url.UserInfo))
        {
            throw new ArgumentException("The control-plane URL must be an absolute HTTPS URL.");
        }
    }
}
