using System.Text;

namespace Squash.Agent;

internal sealed class BoundedOutput : IDisposable
{
    private readonly int _limit;
    private readonly MemoryStream _buffer;
    private readonly object _lock = new();

    public BoundedOutput(int limit)
    {
        _limit = Math.Clamp(limit, 0, AgentConstants.MaximumOutputBytes);
        _buffer = new MemoryStream(Math.Min(_limit, 64 * 1024));
    }

    public bool Truncated { get; private set; }

    public void AppendLine(string? value)
    {
        if (value is null)
        {
            return;
        }

        byte[] bytes = Encoding.UTF8.GetBytes(value + Environment.NewLine);
        lock (_lock)
        {
            int remaining = _limit - checked((int)_buffer.Length);
            int count = Math.Min(remaining, bytes.Length);
            if (count > 0)
            {
                _buffer.Write(bytes, 0, count);
            }
            if (count != bytes.Length)
            {
                Truncated = true;
            }
        }
    }

    public string GetText()
    {
        lock (_lock)
        {
            byte[] bytes = _buffer.ToArray();
            while (bytes.Length > 0)
            {
                try
                {
                    return new UTF8Encoding(false, true).GetString(bytes);
                }
                catch (DecoderFallbackException)
                {
                    Array.Resize(ref bytes, bytes.Length - 1);
                    Truncated = true;
                }
            }
            return string.Empty;
        }
    }

    public void Dispose() => _buffer.Dispose();
}
