using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using System.Drawing;
using System.Drawing.Drawing2D;

internal static class PacoLauncher
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
                "Paco launcher could not find the app runtime.",
                "Paco",
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
        private const int RuntimeStartupTimeoutMilliseconds = 45000;
        private const int WindowStartupTimeoutMilliseconds = 45000;

        private readonly string projectRoot;
        private readonly string python;
        private readonly string entryPoint;
        private readonly string ollamaStartup;
        private readonly Timer animationTimer;
        private readonly Timer runtimeStartupTimer;
        private readonly Timer windowActivationTimer;
        private readonly StringBuilder ollamaError = new StringBuilder();
        private Process pacoProcess;
        private Process ollamaProcess;
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

            runtimeStartupTimer = new Timer { Interval = RuntimeStartupTimeoutMilliseconds };
            runtimeStartupTimer.Tick += RuntimeStartupTimedOut;

            windowActivationTimer = new Timer { Interval = 75 };
            windowActivationTimer.Tick += ActivatePacoWindow;
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
                runtimeStartupTimer.Dispose();
                windowActivationTimer.Dispose();
                if (ollamaProcess != null)
                {
                    ollamaProcess.Dispose();
                }
                if (pacoProcess != null)
                {
                    pacoProcess.Dispose();
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
            ollamaProcess = new Process
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
            ollamaProcess.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (!string.IsNullOrWhiteSpace(eventArgs.Data))
                {
                    ollamaError.AppendLine(eventArgs.Data);
                }
            };
            ollamaProcess.Exited += delegate
            {
                if (!IsDisposed && IsHandleCreated)
                {
                    BeginInvoke((MethodInvoker)FinishRuntimeStart);
                }
            };

            try
            {
                ollamaProcess.Start();
                ollamaProcess.BeginErrorReadLine();
                runtimeStartupTimer.Start();
            }
            catch (Exception exception)
            {
                ShowStartupError("Paco could not start its local runtime.\n\n" + exception.Message);
            }
        }

        private void FinishRuntimeStart()
        {
            runtimeStartupTimer.Stop();
            if (errorShown || ollamaProcess == null)
            {
                return;
            }

            ollamaProcess.WaitForExit();
            int exitCode = ollamaProcess.ExitCode;
            ollamaProcess.Dispose();
            ollamaProcess = null;
            if (exitCode != 0)
            {
                ShowStartupError("Ollama could not be started.\n\n" + ollamaError.ToString().Trim());
                return;
            }

            try
            {
                string startupEventName = "PacoStartupReady_" + Process.GetCurrentProcess().Id + "_" + Guid.NewGuid().ToString("N");
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
                startInfo.EnvironmentVariables["PACO_STARTUP_EVENT"] = startupEventName;
                pacoProcess = Process.Start(startInfo);
                AllowSetForegroundWindow(pacoProcess.Id);
                activationAttempts = 0;
                windowActivationTimer.Start();
            }
            catch (Exception exception)
            {
                ShowStartupError("Paco could not open.\n\n" + exception.Message);
            }
        }

        private void ActivatePacoWindow(object sender, EventArgs eventArgs)
        {
            activationAttempts++;
            if (pacoProcess == null || startupReadyEvent == null)
            {
                ShowStartupError("Paco startup state was lost before its window opened.");
                return;
            }
            pacoProcess.Refresh();

            if (startupReadyEvent.WaitOne(0))
            {
                windowActivationTimer.Stop();
                Close();
                return;
            }

            if (pacoProcess.HasExited)
            {
                ShowStartupError("Paco closed before its window opened.");
                return;
            }

            int timeoutAttempts = WindowStartupTimeoutMilliseconds / windowActivationTimer.Interval;
            if (activationAttempts >= timeoutAttempts)
            {
                StopProcess(pacoProcess);
                ShowStartupError("Paco did not finish opening within 45 seconds. The stalled startup was stopped; try opening Paco again.");
            }
        }

        private void RuntimeStartupTimedOut(object sender, EventArgs eventArgs)
        {
            runtimeStartupTimer.Stop();
            StopProcess(ollamaProcess);
            ShowStartupError("The local runtime did not respond within 45 seconds. The stalled startup was stopped; check Ollama and try again.");
        }

        private static void StopProcess(Process process)
        {
            if (process == null)
            {
                return;
            }

            try
            {
                process.Refresh();
                if (!process.HasExited)
                {
                    process.Kill();
                }
            }
            catch (InvalidOperationException)
            {
            }
            catch (System.ComponentModel.Win32Exception)
            {
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
            runtimeStartupTimer.Stop();
            windowActivationTimer.Stop();
            Hide();
            MessageBox.Show(message, "Paco", MessageBoxButtons.OK, MessageBoxIcon.Error);
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
