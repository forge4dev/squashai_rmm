using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace Squash.Agent;

internal sealed record InventoryPayload(
    [property: System.Text.Json.Serialization.JsonPropertyName("collected_at")]
    DateTimeOffset CollectedAt,
    Dictionary<string, object?> Os,
    Dictionary<string, object?> Hardware,
    IReadOnlyList<Dictionary<string, object?>> Disks,
    [property: System.Text.Json.Serialization.JsonPropertyName("installed_software")]
    IReadOnlyList<Dictionary<string, object?>> InstalledSoftware,
    [property: System.Text.Json.Serialization.JsonPropertyName("last_boot_at")]
    DateTimeOffset? LastBootAt);

internal static class InventoryCollector
{
    public static InventoryPayload Collect()
    {
        using RegistryKey? currentVersion = Registry.LocalMachine.OpenSubKey(
            @"SOFTWARE\Microsoft\Windows NT\CurrentVersion");
        var os = new Dictionary<string, object?>
        {
            ["caption"] = currentVersion?.GetValue("ProductName") as string ?? "Windows",
            ["display_version"] = currentVersion?.GetValue("DisplayVersion") as string,
            ["build"] = currentVersion?.GetValue("CurrentBuildNumber") as string,
            ["architecture"] = RuntimeInformation.OSArchitecture.ToString(),
        };

        var memory = new MemoryStatusEx();
        bool memoryAvailable = GlobalMemoryStatusEx(memory);
        var hardware = new Dictionary<string, object?>
        {
            ["machine_name"] = Environment.MachineName,
            ["processor_count"] = Environment.ProcessorCount,
            ["memory_bytes"] = memoryAvailable ? memory.TotalPhysical : null,
        };
        AddHardwareIdentity(hardware);

        var disks = DriveInfo.GetDrives()
            .Where(drive => drive.IsReady)
            .Select(drive => new Dictionary<string, object?>
            {
                ["name"] = drive.Name,
                ["format"] = drive.DriveFormat,
                ["total_bytes"] = drive.TotalSize,
                ["free_bytes"] = drive.AvailableFreeSpace,
            })
            .ToArray();

        DateTimeOffset lastBoot = DateTimeOffset.UtcNow - TimeSpan.FromMilliseconds(
            Environment.TickCount64);
        return new InventoryPayload(
            DateTimeOffset.UtcNow,
            os,
            hardware,
            disks,
            ReadInstalledSoftware(),
            lastBoot);
    }

    private static List<Dictionary<string, object?>> ReadInstalledSoftware()
    {
        var software = new List<Dictionary<string, object?>>();
        foreach ((RegistryView view, string path) in new[]
        {
            (RegistryView.Registry64, @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (RegistryView.Registry32, @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        })
        {
            using RegistryKey baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
            using RegistryKey? uninstall = baseKey.OpenSubKey(path);
            if (uninstall is null)
            {
                continue;
            }
            foreach (string subkeyName in uninstall.GetSubKeyNames())
            {
                using RegistryKey? item = uninstall.OpenSubKey(subkeyName);
                string? name = item?.GetValue("DisplayName") as string;
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }
                software.Add(new Dictionary<string, object?>
                {
                    ["name"] = name,
                    ["version"] = item?.GetValue("DisplayVersion") as string,
                    ["publisher"] = item?.GetValue("Publisher") as string,
                });
                if (software.Count >= 10_000)
                {
                    return software;
                }
            }
        }
        return software;
    }

    private static void AddHardwareIdentity(Dictionary<string, object?> hardware)
    {
        using RegistryKey? bios = Registry.LocalMachine.OpenSubKey(
            @"HARDWARE\DESCRIPTION\System\BIOS");
        hardware["manufacturer"] = bios?.GetValue("SystemManufacturer") as string;
        hardware["model"] = bios?.GetValue("SystemProductName") as string;
        hardware["system_family"] = bios?.GetValue("SystemFamily") as string;
        hardware["system_sku"] = bios?.GetValue("SystemSKU") as string;
        hardware["serial_number"] = bios?.GetValue("SystemSerialNumber") as string;
        hardware["bios_version"] = bios?.GetValue("BIOSVersion") as string;

        using RegistryKey? processor = Registry.LocalMachine.OpenSubKey(
            @"HARDWARE\DESCRIPTION\System\CentralProcessor\0");
        hardware["processor_name"] = processor?.GetValue("ProcessorNameString") as string;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx([In, Out] MemoryStatusEx buffer);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private sealed class MemoryStatusEx
    {
        public uint Length = checked((uint)Marshal.SizeOf<MemoryStatusEx>());
        public uint MemoryLoad;
        public ulong TotalPhysical;
        public ulong AvailablePhysical;
        public ulong TotalPageFile;
        public ulong AvailablePageFile;
        public ulong TotalVirtual;
        public ulong AvailableVirtual;
        public ulong AvailableExtendedVirtual;
    }
}
