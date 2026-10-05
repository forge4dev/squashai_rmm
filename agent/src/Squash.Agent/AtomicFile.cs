using System.Text;

namespace Squash.Agent;

internal static class AtomicFile
{
    public static void WriteAllText(string path, string contents) =>
        WriteAllBytes(path, new UTF8Encoding(false).GetBytes(contents));

    public static void WriteAllBytes(string path, byte[] contents)
    {
        string temporary = path + ".new";
        using (var stream = new FileStream(
            temporary,
            FileMode.Create,
            FileAccess.Write,
            FileShare.None,
            4096,
            FileOptions.WriteThrough))
        {
            stream.Write(contents);
            stream.Flush(true);
        }

        File.Move(temporary, path, true);
    }
}
