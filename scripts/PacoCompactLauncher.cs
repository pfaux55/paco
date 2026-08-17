using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class PacoCompactLauncher
{
    private const int RuntimeStartupTimeoutMilliseconds = 45000;

    [STAThread]
    private static void Main()
    {
        string launcherDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string projectRoot = ResolveProjectRoot(launcherDirectory);
        string python = Path.Combine(projectRoot, ".venv-win", "Scripts", "pythonw.exe");
        string entryPoint = Path.Combine(projectRoot, "run_compact_assistant.py");
        string ollamaStartup = Path.Combine(projectRoot, "scripts", "ensure_ollama.ps1");

        if (!File.Exists(python) || !File.Exists(entryPoint) || !File.Exists(ollamaStartup))
        {
            ShowError("Paco Compact could not find its project runtime.");
            return;
        }

        try
        {
            using (Process ollama = StartOllama(projectRoot, ollamaStartup))
            {
                if (!ollama.WaitForExit(RuntimeStartupTimeoutMilliseconds))
                {
                    ollama.Kill();
                    ShowError("Ollama did not start within 45 seconds.");
                    return;
                }

                string error = ollama.StandardError.ReadToEnd().Trim();
                if (ollama.ExitCode != 0)
                {
                    ShowError("Ollama could not start." + Environment.NewLine + Environment.NewLine + error);
                    return;
                }
            }

            Process.Start(new ProcessStartInfo
            {
                FileName = python,
                Arguments = "\"" + entryPoint + "\"",
                WorkingDirectory = projectRoot,
                UseShellExecute = false,
                CreateNoWindow = true
            });
        }
        catch (Exception exception)
        {
            ShowError("Paco Compact could not open." + Environment.NewLine + Environment.NewLine + exception.Message);
        }
    }

    private static string ResolveProjectRoot(string launcherDirectory)
    {
        string directory = launcherDirectory.TrimEnd(Path.DirectorySeparatorChar);
        if (File.Exists(Path.Combine(directory, "run_compact_assistant.py")))
        {
            return directory;
        }

        DirectoryInfo parent = Directory.GetParent(directory);
        return parent == null ? directory : parent.FullName;
    }

    private static Process StartOllama(string projectRoot, string ollamaStartup)
    {
        Process process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + ollamaStartup + "\"",
                WorkingDirectory = projectRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true
            }
        };
        process.Start();
        return process;
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "Paco Compact", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
