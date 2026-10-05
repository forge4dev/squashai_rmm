using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;

namespace Squash.Agent;

internal static class DeviceIdentity
{
    public static string GetFingerprint()
    {
        using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography");
        string machineGuid = key?.GetValue("MachineGuid") as string
            ?? throw new InvalidOperationException("Windows MachineGuid is unavailable.");
        byte[] material = Encoding.UTF8.GetBytes($"squash-rmm-device-v1\0{machineGuid.Trim()}");
        return Convert.ToHexString(SHA256.HashData(material)).ToLowerInvariant();
    }
}
