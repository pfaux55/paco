using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using System.Drawing;
using System.Drawing.Drawing2D;

internal static class JarvisLauncher
{
    [STAThread]
    private static void Main()
    {
        string launcherDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string projectRoot = Directory.GetParent(launcherDirectory.TrimEnd(Path.DirectorySeparatorChar)).FullName;
        string python = Path.Combine(projectRoot, ".venv-win", "Scripts", "pythonw.exe");
        string entryPoint = Path.Combine(projectRoot, "run_assistant.py");
        string ollamaStartup = Path.Combine(projectRoot, "scripts", "ensure_ollama.ps1");

        if (!File.Exists(python) || !File.Exists(entryPoint) || !File.Exists(ollamaStartup))
        {
            MessageBox.Show(
                "Jarvis launcher could not find the app runtime.",
                "Jarvis",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new StartupIndicator(projectRoot, python, entryPoint, ollamaStartup));
    }

    private sealed class StartupIndicator : Form
    {
        private readonly string projectRoot;
        private readonly string python;
        private readonly string entryPoint;
        private readonly string ollamaStartup;
        private readonly Timer animationTimer;
        private readonly Timer windowActivationTimer;
        private readonly StringBuilder ollamaError = new StringBuilder();
        private Process jarvisProcess;
        private System.Threading.EventWaitHandle startupReadyEvent;
        private int activationAttempts;
        private float angle;
        private bool errorShown;

        internal StartupIndicator(string projectRoot, string python, string entryPoint, string ollamaStartup)
        {
            this.projectRoot = projectRoot;
            this.python = python;
            this.entryPoint = entryPoint;
            this.ollamaStartup = ollamaStartup;

            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Color.FromArgb(9, 16, 13);
            ClientSize = new Size(82, 82);
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false;
            MinimizeBox = false;
            Opacity = 0.92;
            ShowIcon = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.CenterScreen;
            TopMost = true;

            animationTimer = new Timer { Interval = 16 };
            animationTimer.Tick += delegate
            {
                angle = (angle + 5.5f) % 360f;
                Invalidate();
            };

            windowActivationTimer = new Timer { Interval = 75 };
            windowActivationTimer.Tick += ActivateJarvisWindow;
        }

        protected override void OnShown(EventArgs eventArgs)
        {
            base.OnShown(eventArgs);
            using (GraphicsPath path = RoundedRectangle(ClientRectangle, 16))
            {
                Region = new Region(path);
            }
            animationTimer.Start();
            BeginInvoke((MethodInvoker)StartRuntime);
        }

        protected override void OnPaint(PaintEventArgs eventArgs)
        {
            base.OnPaint(eventArgs);
            Graphics graphics = eventArgs.Graphics;
            graphics.SmoothingMode = SmoothingMode.AntiAlias;

            RectangleF ring = new RectangleF(23f, 23f, 36f, 36f);
            using (Pen track = new Pen(Color.FromArgb(45, 92, 77), 3f))
            using (Pen active = new Pen(Color.FromArgb(112, 255, 184), 3f))
            using (SolidBrush center = new SolidBrush(Color.FromArgb(112, 255, 184)))
            {
                active.StartCap = LineCap.Round;
                active.EndCap = LineCap.Round;
                graphics.DrawEllipse(track, ring);
                graphics.DrawArc(active, ring, angle, 94f);
                graphics.FillEllipse(center, 38f, 38f, 6f, 6f);
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                animationTimer.Dispose();
                windowActivationTimer.Dispose();
                if (jarvisProcess != null)
                {
                    jarvisProcess.Dispose();
                }
                if (startupReadyEvent != null)
                {
                    startupReadyEvent.Dispose();
                }
            }
            base.Dispose(disposing);
        }

        private void StartRuntime()
        {
            Process ollama = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + ollamaStartup + "\"",
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardError = true
                },
                EnableRaisingEvents = true
            };
            ollama.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (!string.IsNullOrWhiteSpace(eventArgs.Data))
                {
                    ollamaError.AppendLine(eventArgs.Data);
                }
            };
            ollama.Exited += delegate
            {
                BeginInvoke((MethodInvoker)delegate { FinishRuntimeStart(ollama); });
            };

            try
            {
                ollama.Start();
                ollama.BeginErrorReadLine();
            }
            catch (Exception exception)
            {
                ShowStartupError("Jarvis could not start its local runtime.\n\n" + exception.Message);
            }
        }

        private void FinishRuntimeStart(Process ollama)
        {
            ollama.WaitForExit();
            int exitCode = ollama.ExitCode;
            ollama.Dispose();
            if (exitCode != 0)
            {
                ShowStartupError("Ollama could not be started.\n\n" + ollamaError.ToString().Trim());
                return;
            }

            try
            {
                string startupEventName = "JarvisStartupReady_" + Process.GetCurrentProcess().Id + "_" + Guid.NewGuid().ToString("N");
                startupReadyEvent = new System.Threading.EventWaitHandle(
                    false,
                    System.Threading.EventResetMode.ManualReset,
                    startupEventName
                );
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = "\"" + entryPoint + "\"",
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                startInfo.EnvironmentVariables["JARVIS_STARTUP_EVENT"] = startupEventName;
                jarvisProcess = Process.Start(startInfo);
                AllowSetForegroundWindow(jarvisProcess.Id);
                activationAttempts = 0;
                windowActivationTimer.Start();
            }
            catch (Exception exception)
            {
                ShowStartupError("Jarvis could not open.\n\n" + exception.Message);
            }
        }

        private void ActivateJarvisWindow(object sender, EventArgs eventArgs)
        {
            activationAttempts++;
            jarvisProcess.Refresh();

            if (startupReadyEvent.WaitOne(0))
            {
                windowActivationTimer.Stop();
                Close();
                return;
            }

            if (jarvisProcess.HasExited && jarvisProcess.ExitCode != 0)
            {
                ShowStartupError("Jarvis closed before its window opened.");
                return;
            }

            if (activationAttempts >= 600)
            {
                windowActivationTimer.Stop();
                Close();
            }
        }

        private void ShowStartupError(string message)
        {
            if (errorShown)
            {
                return;
            }
            errorShown = true;
            animationTimer.Stop();
            windowActivationTimer.Stop();
            Hide();
            MessageBox.Show(message, "Jarvis", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }

        private static GraphicsPath RoundedRectangle(Rectangle bounds, int radius)
        {
            int diameter = radius * 2;
            GraphicsPath path = new GraphicsPath();
            path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
            path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
            path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }

        [DllImport("user32.dll")]
        private static extern bool AllowSetForegroundWindow(int processId);
    }
}
